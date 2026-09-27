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


def test_sideways_labels_are_read_upright(pipeline_results):
    turned = {name for name, r in pipeline_results.items() if r["parsed"].get("photo_turn")}
    assert turned == {"15-9/IMG_9164.HEIC", "24-9/IMG_0285.HEIC"}
