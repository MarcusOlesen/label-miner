"""The webshop lookup, on recorded webshop answers."""

import threading

import pytest

from conftest import FIXTURES
from harness import CachedSession, _Response
from label_miner.review import review_question
from label_miner.shop import look_up_product


def look_up(number, name="", part="", session=None):
    session = session or CachedSession(FIXTURES / "shop")
    return look_up_product(session, {}, threading.Lock(), number, name, part)


def test_number_only_with_one_variant_fills_in_the_product():
    """A typed number and no name: the product has one variant, so name,
    price and link are filled in - with a warning, so the user confirms it."""
    result, warnings = look_up("106854")
    assert result["varenavn"] == "Oris sofabord, marmor emperador | Ø82"
    assert (result["price"], result["compare_price"]) == ("1.999 kr", "2.999 kr")
    assert "/products/oris-sofabord-marmor-emperador-o82?variant=56390611075449" in result["url"]
    assert [category for category, _ in warnings] == ["by_number"]
    assert review_question(warnings)["title"] == "Er det den rigtige vare?"


def test_number_only_with_several_variants_offers_them_all():
    result, warnings = look_up("103284")
    assert result["varenavn"] == result["price"] == result["url"] == ""
    assert sorted(candidate["name"] for candidate in result["candidates"]) == [
        "Nord spisebordsstol Beige",
        "Nord spisebordsstol Taupe",
    ]
    assert [category for category, _ in warnings] == ["variant"]


def test_number_only_offers_every_variant_not_just_five():
    result, _ = look_up("102686")  # Como puf: 20 colours
    assert len(result["candidates"]) == 20


def test_number_only_part_of_an_item_gets_no_price():
    result, warnings = look_up("106854_1/2", part="1/2")
    assert result["varenavn"] and result["url"]
    assert result["price"] == result["compare_price"] == ""
    assert [category for category, _ in warnings] == ["by_number"]


def test_number_with_name_still_needs_every_word_of_the_name():
    result, warnings = look_up("106854", "Oris sofabord, marmor emperador | Ø82")
    assert result["price"] and warnings == []
    result, warnings = look_up("103284", "Nord spisebordsstol")
    assert result["price"] == "" and [category for category, _ in warnings] == ["variant"]


class TooManyRequests:
    def get(self, url, params=None, headers=None, timeout=None):
        return _Response(429, None)


@pytest.mark.parametrize("name", ["", "Nord spisebordsstol"])
def test_too_many_searches_says_so(name):
    result, warnings = look_up("103284", name, session=TooManyRequests())
    assert result["candidates"] == [] and result["price"] == ""
    [(category, message)] = warnings
    assert category == "shop"
    assert "for mange søgninger" in message and "Vent et minut" in message
