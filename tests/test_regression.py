"""The whole pipeline on real label photos, scored against a verified answer key.

The hard rule: a label may only be approved without a human looking at it when
everything about it is right. An unnecessary review is annoying; a silently
wrong product or price is not acceptable.
"""

from collections import Counter

import pytest

from conftest import fixture_photos
from harness import score


@pytest.mark.parametrize("name", fixture_photos())
def test_photo(name, answers, pipeline_results):
    result = pipeline_results[name]
    verdict, problems = score(answers[name], result["parsed"], result["warnings"], result["candidates"])
    expected = "AUTO ok" if answers[name]["expect"] == "auto" else "REVIEW ok"
    assert verdict == expected, f"{verdict}: {problems or [m for _, m in result['warnings']]}"
    assert not problems, problems


def test_nothing_wrong_is_approved(answers, pipeline_results):
    verdicts = Counter(
        score(answers[name], r["parsed"], r["warnings"], r["candidates"])[0]
        for name, r in pipeline_results.items()
    )
    assert verdicts["WRONG"] == 0


def test_part_of_item_never_gets_a_price(pipeline_results):
    for result in pipeline_results.values():
        if result["parsed"].get("part"):
            assert result["parsed"]["price"].startswith("OBS: delvis vare")


def test_part_of_item_confirmed_by_the_shop(pipeline_results):
    """A part is approved when the shop confirms the item by number and name -
    with the shop's name and link, and the note instead of a price."""
    confirmed = pipeline_results["24-9/IMG_0285.HEIC"]
    assert confirmed["warnings"] == []
    assert confirmed["parsed"]["varenavn"] == "Vega sofabord, travertin marmor | 90x90"
    assert "/products/vega-sofabord-travertin-marmor-90x90" in confirmed["parsed"]["url"]
    assert confirmed["parsed"]["price"] == "OBS: delvis vare (del 1/2)"
    unconfirmed = pipeline_results["24-9/IMG_0283.HEIC"]
    assert "part" in {category for category, _ in unconfirmed["warnings"]}


def test_sideways_labels_are_read_upright(pipeline_results):
    turned = {name for name, r in pipeline_results.items() if r["parsed"].get("photo_turn")}
    assert turned == {
        "15-9/IMG_9164.HEIC",
        "24-9/IMG_0285.HEIC",
        "4-11/IMG_0980.JPG",
        "4-11/IMG_0981.JPG",
        "4-11/IMG_0986.JPG",
        "4-11/IMG_0987.JPG",
    }


def test_review_reasons(pipeline_results):
    """The warning category decides which question the review window asks."""

    def categories(name):
        return {category for category, _ in pipeline_results[name]["warnings"]}

    assert "by_name" in categories("24-9/IMG_0275.HEIC")  # supplier code, found by its name
    assert "conflict" in categories("24-9/IMG_0302.HEIC")  # the number is another product
    assert "not_found" in categories("24-9/IMG_0296.HEIC")


def test_both_label_designs_in_one_batch(answers, pipeline_results):
    """The 4-11 batch has 28 labels in the new design and one in the old:
    each is read with its own layout, and every field of every label is
    read right - whatever the webshop says."""
    batch = {name: r for name, r in pipeline_results.items() if name.startswith("4-11/")}
    assert len(batch) == 29
    for name, result in batch.items():
        answer, parsed = answers[name], result["parsed"]
        fields = ["mk_varenummer", "mk_ordrenr", "stand"] + (["antal"] if answer["design"] == "new" else [])
        assert {field: parsed[field] for field in fields} == {field: answer[field] for field in fields}, name
        assert ("antal" in parsed) == (answer["design"] == "new"), name
        assert not [message for category, message in result["warnings"] if category in ("missing", "partial")]
    assert pipeline_results["4-11/IMG_0983.JPG"]["warnings"] == []  # the old design, approved as before


def test_new_design_without_colour_gets_the_variant_from_the_number(pipeline_results):
    """New labels often leave out the colour: the item number is the SKU
    of one variant, so the colour comes from the number - never guessed."""
    nord = pipeline_results["4-11/IMG_0974.JPG"]
    assert nord["warnings"] == []
    assert nord["parsed"]["varenavn"] == "Nord spisebordsstol Taupe"
    assert nord["parsed"]["price"] == "899 kr (1.399 kr)"
    # Same name on both labels, two colours
    assert pipeline_results["4-11/IMG_1002.JPG"]["parsed"]["varenavn"].endswith("Amiya Beige")
    assert pipeline_results["4-11/IMG_1003.JPG"]["parsed"]["varenavn"].endswith("Amiya Brun")
