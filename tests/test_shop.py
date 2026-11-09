"""The webshop lookup, on recorded webshop answers."""

import threading

import pytest

from conftest import FIXTURES
from harness import CachedSession, CachedSessionWithoutSkus, _Response
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


def test_the_number_is_the_variants_sku():
    """The shop's SKU is the item number of one exact variant, so the
    number tells the colour even when the label's name doesn't."""
    result, warnings = look_up("103284")  # typed number alone
    assert result["varenavn"] == "Nord spisebordsstol Taupe"
    assert [category for category, _ in warnings] == ["by_number"]
    result, warnings = look_up("103284", "Nord spisebordsstol")
    assert (result["varenavn"], result["price"], warnings) == ("Nord spisebordsstol Taupe", "899 kr", [])
    beige, _ = look_up("103485", "Curve spisebordsstol")
    brown, _ = look_up("103484", "Curve spisebordsstol")
    assert beige["varenavn"] == "Curve spisebordsstol Amiya Beige"
    assert brown["varenavn"] == "Curve spisebordsstol Amiya Brun"


def test_name_that_disagrees_with_the_sku_variant_is_reviewed():
    """German label: "Rebel Dunkelbeige" isn't every word of "Rebel Mørk
    Beige", so it is reviewed - with the number's variant first."""
    result, warnings = look_up("100718", "Lissabon 2-Sitzer-Sofa Rebel Dunkelbeige")
    assert result["price"] == "" and [category for category, _ in warnings] == ["variant"]
    assert result["candidates"][0]["name"] == "Lissabon 2 personers sofa Rebel Mørk Beige"


def test_number_only_with_several_variants_offers_them_all():
    result, warnings = look_up("103284", session=CachedSessionWithoutSkus(FIXTURES / "shop"))
    assert result["varenavn"] == result["price"] == result["url"] == ""
    assert sorted(candidate["name"] for candidate in result["candidates"]) == [
        "Nord spisebordsstol Beige",
        "Nord spisebordsstol Taupe",
    ]
    assert [category for category, _ in warnings] == ["variant"]


def test_number_only_offers_every_variant_not_just_five():
    result, _ = look_up("102686", session=CachedSessionWithoutSkus(FIXTURES / "shop"))  # Como puf: 20 colours
    assert len(result["candidates"]) == 20


def test_number_only_part_of_an_item_gets_no_price():
    result, warnings = look_up("106854_1/2", part="1/2")
    assert result["varenavn"] and result["url"]
    assert result["price"] == result["compare_price"] == ""
    assert [category for category, _ in warnings] == ["by_number"]


def test_number_with_name_still_needs_every_word_of_the_name():
    result, warnings = look_up("106854", "Oris sofabord, marmor emperador | Ø82")
    assert result["price"] and warnings == []
    without_skus = CachedSessionWithoutSkus(FIXTURES / "shop")
    result, warnings = look_up("103284", "Nord spisebordsstol", session=without_skus)
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
