"""Remembered choices: the product the user picked for a label, offered first next time."""

import threading

from conftest import FIXTURES
from harness import CachedOcrEngine, CachedSession
from label_miner.choices import (
    choice_key,
    choices_path,
    load_choices,
    remember_choice,
    remembered_choice,
    save_choices,
)
from label_miner.pipeline import extract_label
from label_miner.records import Candidate
from label_miner.shop import remembered_candidate

NORD_URL = "https://moebelkompagniet.dk/products/nord-spisebordsstol"
NORD_BEIGE = Candidate(
    "Nord spisebordsstol Beige", id=54932797686137, url=f"{NORD_URL}?variant=54932797686137"
)
NORD_TAUPE = Candidate(
    "Nord spisebordsstol Taupe", id=54932797653369, url=f"{NORD_URL}?variant=54932797653369"
)


def test_key_is_the_label_as_read():
    # Case, spacing and punctuation don't matter; the supplier prefix does
    assert choice_key("ay 0000108453", "Cholet sengebord, væghængt | vild eg") == choice_key(
        "AY  0000108453", "cholet sengebord væghængt vild eg"
    )
    assert choice_key("AY 0000108453", "Cholet") != choice_key("0000108453", "Cholet")
    assert choice_key("103284", "Nord spisebordsstol") != choice_key("103284", "Nord")
    # A number or a name alone says too little
    assert choice_key("", "Nord spisebordsstol") is None
    assert choice_key("103284", "") is None


def test_same_product_counts_up_another_replaces_it():
    choices = {}
    assert remember_choice(choices, "103284", "Nord spisebordsstol", NORD_BEIGE)
    remember_choice(choices, "103284", "Nord spisebordsstol", NORD_BEIGE)
    assert remembered_choice(choices, "103284", "Nord spisebordsstol")["count"] == 2
    # A different pick - e.g. the first one was a mistake - starts over
    remember_choice(choices, "103284", "Nord spisebordsstol", NORD_TAUPE)
    choice = remembered_choice(choices, "103284", "Nord spisebordsstol")
    assert (choice["variant_id"], choice["count"]) == (NORD_TAUPE.id, 1)
    assert not remember_choice(choices, "", "Nord spisebordsstol", NORD_BEIGE)


def test_saved_and_loaded(tmp_path):
    choices = {}
    remember_choice(choices, "103284", "Nord spisebordsstol", NORD_BEIGE)
    save_choices(choices)
    assert choices_path().exists()
    assert load_choices() == choices
    damaged = tmp_path / "choices.json"
    for content in ("{not json", "[]", '{"choices": {"x": {"url": 3}}}'):
        damaged.write_text(content, encoding="utf-8")
        assert load_choices(damaged) == {}


def session():
    return CachedSession(FIXTURES / "shop")


def choice_for(candidate):
    choices = {}
    remember_choice(choices, "103284", "Nord spisebordsstol", candidate)
    return choices, remembered_choice(choices, "103284", "Nord spisebordsstol")


def test_remembered_product_with_todays_price():
    _, choice = choice_for(NORD_BEIGE)
    candidate = remembered_candidate(session(), {}, threading.Lock(), choice)
    assert candidate.id == NORD_BEIGE.id and candidate.price == "899 kr"
    assert candidate.chosen_before == 1


def test_remembered_product_the_shop_changed_is_dropped():
    _, choice = choice_for(NORD_BEIGE)
    lock = threading.Lock()
    renamed = {**choice, "name": "Nord spisebordsstol Sort"}
    assert remembered_candidate(session(), {}, lock, renamed) is None
    gone = {**choice, "variant_id": 123}
    assert remembered_candidate(session(), {}, lock, gone) is None
    unknown_product = {**choice, "url": "https://moebelkompagniet.dk/products/not-recorded"}
    assert remembered_candidate(session(), {}, lock, unknown_product) is None  # lookup error: no suggestion


def run(name, choices):
    return extract_label(
        CachedOcrEngine(FIXTURES / "ocr"),
        FIXTURES / "photos" / name,
        session(),
        {},
        threading.Lock(),
        choices,
    )


def test_a_label_in_review_gets_its_remembered_product_first():
    """A label whose name doesn't match the shop (German): the product
    picked last time comes first - and the label still goes to review."""
    lissabon_url = "https://moebelkompagniet.dk/products/lissabon-2-personers-sofa"
    picked = Candidate(
        "Lissabon 2 personers sofa Rebel Mørkebrun",
        id=48466917327185,
        url=f"{lissabon_url}?variant=48466917327185",
    )
    choices = {}
    remember_choice(choices, "100718", "Lissabon 2-Sitzer-Sofa Rebel Dunkelbeige", picked)
    record = run("24-9/IMG_0292.HEIC", choices)
    candidates = record.candidates
    assert [category for category, _ in record.warnings] == ["variant"]
    assert candidates[0].id == picked.id and candidates[0].chosen_before == 1
    assert [c.id for c in candidates].count(picked.id) == 1
    assert record.price == "" and record.url == ""  # nothing filled in or approved


def test_an_approved_label_ignores_remembered_choices():
    choices = {}
    remember_choice(choices, "106854", "Oris sofabord, marmor emperador | Ø82", NORD_BEIGE)
    record = run("4-11/IMG_0978.JPG", choices)
    assert record.warnings == [] and record.candidates == []
    assert "/products/oris-sofabord-marmor-emperador-o82" in record.url


def test_without_shop_lookup_the_shop_is_never_asked(pipeline_results):
    """"Slå varer op på hjemmesiden automatisk" off: the label is read as
    always, with no price, link or suggestions, and no shop warnings."""

    class NoShop:
        def get(self, *args, **kwargs):
            raise AssertionError("the webshop was asked")

    for name, looked_up in pipeline_results.items():
        record = extract_label(
            CachedOcrEngine(FIXTURES / "ocr"), FIXTURES / "photos" / name, NoShop(), {}, threading.Lock(),
            shop_lookup=False,
        )
        assert record.label.mk_varenummer == looked_up.label.mk_varenummer
        assert record.url == "" and record.candidates == []
        assert not [w for w in record.warnings if w[0] in ("conflict", "by_name", "by_number", "variant", "not_found", "shop")]
