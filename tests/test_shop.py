"""The webshop lookup, on the recorded catalogue (see shop.Catalogue)."""

import threading

import pytest

from conftest import FIXTURES
from harness import CachedSession, CachedSessionWithoutSkus, _Response
from label_miner import shop
from label_miner.review import review_question
from label_miner.shop import look_up_product


def look_up(number, name="", part="", session=None):
    session = session or CachedSession(FIXTURES / "shop")
    return look_up_product(session, {}, threading.Lock(), number, name, part)


def test_number_only_with_one_variant_fills_in_the_product():
    """A typed number and no name: the product has one variant, so name,
    price and link are filled in - with a warning, so the user confirms it."""
    result, warnings = look_up("106854")
    assert result.varenavn == "Oris sofabord, marmor emperador | Ø82"
    assert (result.price, result.compare_price) == ("1.999 kr", "2.999 kr")
    assert "/products/oris-sofabord-marmor-emperador-o82?variant=56390611075449" in result.url
    assert [category for category, _ in warnings] == ["by_number"]
    assert review_question(warnings)["title"] == "Er det den rigtige vare?"


def test_the_number_is_the_variants_sku():
    """The shop's SKU is the item number of one exact variant, so the
    number tells the colour even when the label's name doesn't."""
    result, warnings = look_up("103284")  # typed number alone
    assert result.varenavn == "Nord spisebordsstol Taupe"
    assert [category for category, _ in warnings] == ["by_number"]
    result, warnings = look_up("103284", "Nord spisebordsstol")
    assert (result.varenavn, result.price, warnings) == ("Nord spisebordsstol Taupe", "899 kr", [])
    beige, _ = look_up("103485", "Curve spisebordsstol")
    brown, _ = look_up("103484", "Curve spisebordsstol")
    assert beige.varenavn == "Curve spisebordsstol Amiya Beige"
    assert brown.varenavn == "Curve spisebordsstol Amiya Brun"


def test_name_that_disagrees_with_the_sku_variant_is_reviewed():
    """German label: "Rebel Dunkelbeige" isn't every word of "Rebel Mørk
    Beige", so it is reviewed - with the number's variant first."""
    result, warnings = look_up("100718", "Lissabon 2-Sitzer-Sofa Rebel Dunkelbeige")
    assert result.price == "" and [category for category, _ in warnings] == ["variant"]
    assert result.candidates[0].name == "Lissabon 2 personers sofa Rebel Mørk Beige"


def test_a_number_is_only_found_as_a_whole_sku():
    """A number that is no variant's SKU finds nothing on its own - not a
    product whose SKU starts with it, or one that merely has it in its name."""
    for number in ("10328", "1032845", "103 284 1"):
        result, warnings = look_up(number)
        assert result.varenavn == "" and result.candidates == []
        assert [category for category, _ in warnings] == ["not_found"]
    result, _ = look_up("ay  h000022202")  # case and spacing don't matter (SKU "AY H000022202")
    assert result.varenavn.startswith("Vega")


def test_a_name_alone_offers_the_variants_with_every_word():
    result, warnings = look_up("", "Nord spisebordsstol")
    assert result.varenavn == result.price == result.url == ""
    assert sorted(candidate.name for candidate in result.candidates) == [
        "Nord spisebordsstol Beige",
        "Nord spisebordsstol Taupe",
    ]
    assert [category for category, _ in warnings] == ["by_name"]
    result, _ = look_up("", "Como puf")  # 20 colours: the five best are offered
    assert len(result.candidates) == 5


def test_number_only_part_of_an_item_gets_no_price():
    result, warnings = look_up("106854_1/2", part="1/2")
    assert result.varenavn and result.url
    assert result.price == result.compare_price == ""
    assert [category for category, _ in warnings] == ["by_number"]


def test_number_with_name_still_needs_every_word_of_the_name():
    result, warnings = look_up("106854", "Oris sofabord, marmor emperador | Ø82")
    assert result.price and warnings == []
    # A number the shop doesn't have: the name finds the product, but the
    # colour is the user's to pick
    without_skus = CachedSessionWithoutSkus(FIXTURES / "shop")
    result, warnings = look_up("103284", "Nord spisebordsstol", session=without_skus)
    assert result.price == "" and [category for category, _ in warnings] == ["by_name"]
    assert len(result.candidates) == 2


class TooManyRequests:
    def get(self, url, params=None, headers=None, timeout=None):
        return _Response(429, None)


@pytest.mark.parametrize("name", ["", "Nord spisebordsstol"])
def test_too_many_searches_says_so(name):
    result, warnings = look_up("103284", name, session=TooManyRequests())
    assert result.candidates == [] and result.price == ""
    [(category, message)] = warnings
    assert category == "shop"
    assert "for mange søgninger" in message and "Vent et minut" in message


class CountingSession(CachedSession):
    """The recorded catalogue, counting the requests; fails while ``failing``."""

    def __init__(self):
        super().__init__(FIXTURES / "shop")
        self.requests, self.failing = 0, False

    def get(self, url, params=None, headers=None, timeout=None):
        self.requests += 1
        if self.failing:
            return _Response(429, None)
        return super().get(url, params, headers, timeout)


def test_the_catalogue_is_downloaded_once_per_batch():
    session, cache, lock = CountingSession(), {}, threading.Lock()
    for number, name in [("106854", ""), ("103284", "Nord spisebordsstol"), ("", "Como puf")]:
        look_up_product(session, cache, lock, number, name)
    assert session.requests == 1  # the recorded shop fits on one page


def test_a_failed_download_is_not_retried_for_a_minute(monkeypatch):
    """A blocked or offline shop isn't asked again for every label."""
    now = [1000.0]
    monkeypatch.setattr(shop.time, "monotonic", lambda: now[0])
    session, cache, lock = CountingSession(), {}, threading.Lock()
    session.failing = True
    for _ in range(5):
        _, warnings = look_up_product(session, cache, lock, "106854", "")
        assert [category for category, _ in warnings] == ["shop"]
    tries = 1 + len(shop.CATALOGUE_BUSY_WAITS)  # asked again after each wait, then given up
    assert session.requests == tries
    session.failing = False
    now[0] += shop.CATALOGUE_RETRY_AFTER + 1
    result, warnings = look_up_product(session, cache, lock, "106854", "")
    assert result.price and session.requests == tries + 1


def test_a_busy_shop_is_asked_again_after_a_pause(no_waiting_for_the_shop):
    """Refused once (too many requests): the download waits and asks again,
    instead of failing the whole batch."""
    session, cache, lock = CountingSession(), {}, threading.Lock()
    session.failing = True
    real_get = session.get

    def busy_once(*args, **kwargs):
        response = real_get(*args, **kwargs)
        session.failing = False
        return response

    session.get = busy_once
    result, warnings = look_up_product(
        session, cache, lock, "106854", "Oris sofabord, marmor emperador | Ø82"
    )
    assert result.price and warnings == []
    assert session.requests == 2
    assert no_waiting_for_the_shop == [shop.CATALOGUE_BUSY_WAITS[0]]


def test_the_pages_are_asked_for_with_a_pause_between(no_waiting_for_the_shop, monkeypatch):
    """A whole shop is several pages; asked for back to back, the shop
    refuses the later ones."""
    monkeypatch.setattr(shop, "CATALOGUE_PAGE_SIZE", 40)  # the recorded 85 products: three pages

    class Paged:
        products = (
            CachedSession(FIXTURES / "shop")
            .get(f"{shop.SHOP_URL}/products.json", {"limit": 250, "page": 1})
            .payload["products"]
        )

        def get(self, url, params=None, headers=None, timeout=None):
            start = (params["page"] - 1) * params["limit"]
            return _Response(200, {"products": self.products[start : start + params["limit"]]})

    catalogue = shop.download_catalogue(Paged())
    assert len(catalogue.products) == 85
    # A random pause in the range before each later page, not the same every time
    low, high = shop.CATALOGUE_PAGE_PAUSE
    assert len(no_waiting_for_the_shop) == 2
    assert all(low <= pause <= high for pause in no_waiting_for_the_shop)
    assert len(set(no_waiting_for_the_shop)) == 2


def test_the_catalogue_is_downloaded_again_when_an_hour_old(monkeypatch):
    """A batch reviewed hours later still gets today's prices."""
    now = [1000.0]
    monkeypatch.setattr(shop.time, "monotonic", lambda: now[0])
    session, cache, lock = CountingSession(), {}, threading.Lock()
    look_up_product(session, cache, lock, "106854", "")
    now[0] += shop.CATALOGUE_MAX_AGE - 1
    look_up_product(session, cache, lock, "106854", "")
    assert session.requests == 1
    now[0] += 2
    look_up_product(session, cache, lock, "106854", "")
    assert session.requests == 2


def test_the_download_says_how_it_goes(no_waiting_for_the_shop):
    """What the status line shows while the catalogue downloads, including
    a wait for a busy shop."""
    session, cache, lock = CountingSession(), {}, threading.Lock()
    session.failing = True
    real_get = session.get

    def busy_once(*args, **kwargs):
        response = real_get(*args, **kwargs)
        session.failing = False
        return response

    session.get = busy_once
    reports = []
    assert shop.prefetch_catalogue(session, cache, lock, reports.append)
    assert reports == [
        "Henter hjemmesidens varer (0 indtil videre)…",
        f"Hjemmesiden har travlt – prøver igen om {shop.CATALOGUE_BUSY_WAITS[0]} sekunder…",
    ]
    result, _ = look_up_product(session, cache, lock, "106854", "Oris sofabord, marmor emperador | Ø82")
    assert result.price and session.requests == 2  # the lookup used the downloaded catalogue
