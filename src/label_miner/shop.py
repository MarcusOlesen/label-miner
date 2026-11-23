"""Finding a label's product on the webshop.

The shop is a Shopify store. Its search allows a short burst and then only
about one search a minute, which a batch of 15-40 labels ran into with
several searches per label. So the app doesn't search: it downloads the
shop's whole catalogue from Shopify's public product list
(``/products.json``, 250 products per request - three requests for the
whole shop in November 2026) and finds every label's product in it on this
PC (see Catalogue).

The strategy is built around one rule: a price is only filled in when it is
certain. The product is found by the label's number, the SKU of one exact
variant, and counts only if that variant contains every word of the label's
name. Everything else becomes a question for the user, with the likely
products offered as choices.
"""

import difflib
import random
import re
import time
from dataclasses import replace
from threading import Lock

from .config import SHOP_URL
from .parsing import PART_PREFIX_RE, PART_SUFFIX_RE, STK_TAG_RE
from .records import Candidate, Product, ShopResult, Warning
from .util import debug_log

DEFAULT_TITLE_RE = re.compile(r"\bDefault Title\b", flags=re.IGNORECASE)

# Warning categories that come from the webshop lookup. They are replaced as
# a whole whenever the lookup runs again (e.g. after the user fixed a field).
SHOP_WARNING_CATEGORIES = {"conflict", "by_name", "by_number", "variant", "not_found", "shop", "part"}


def name_tokens(name: str) -> list[str]:
    """The words used to compare a label's product name with a webshop
    name: lowercase, punctuation and "|" dropped, × read as x, and without
    a "- 6 stk." quantity tag or a "1/2 " part prefix - those are on the
    label but never in the shop's name."""
    name = PART_PREFIX_RE.sub("", (name or "").strip())
    name = STK_TAG_RE.sub("", name)
    name = DEFAULT_TITLE_RE.sub("", name).replace("×", "x")
    return [token for token in re.split(r"[^\w]+", name.casefold()) if token]


def _tokens_match(label_token: str, shop_token: str) -> bool:
    """Exact match, or a match despite one of two OCR slips:
    - a "|" read as "1"/"I"/"l" and glued onto the next word
      ("1200x100" for "| 200x100", "Iopbevaring" for "| opbevaring")
    - a small misread inside a longer plain word
    Otherwise numbers, sizes and short words must match exactly: "Bali" is
    not "Cali", and "140x200" is not "200x300"."""
    if label_token == shop_token:
        return True
    if len(shop_token) >= 3 and label_token[:1] in ("1", "i", "l") and label_token[1:] == shop_token:
        return True
    return (
        len(label_token) >= 5
        and len(shop_token) >= 5
        and label_token.isalpha()
        and shop_token.isalpha()
        and difflib.SequenceMatcher(None, label_token, shop_token).ratio() >= 0.85
    )


def token_coverage(tokens: list[str], other_tokens: list[str]) -> float:
    """Share of `tokens` found in `other_tokens` (0.0 - 1.0)."""
    if not tokens:
        return 0.0
    found = sum(1 for token in tokens if any(_tokens_match(token, other) for other in other_tokens))
    return found / len(tokens)


def _format_shop_price(value) -> str:
    """A Shopify price as Danish text: 1699900 (øre) or "16999.00" (kroner) -> "16.999 kr"."""
    if value in (None, ""):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    # The product list has kroner as strings; integers are øre (as product .js gives them).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount /= 100
    if amount.is_integer():
        return f"{int(amount):,}".replace(",", ".") + " kr"
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr"


class ShopBusyError(Exception):
    """The webshop refused the request: too many in a short time (HTTP 429)."""


def _get_json(session, url, params=None):
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if response.status_code == 429:
        # Waiting here would stall the batch, so it says what happened and how
        # to fix it; the failure is only kept for a minute (see _catalogue).
        raise ShopBusyError(
            "hjemmesiden har fået for mange søgninger på kort tid. Vent et minut, "
            'og tryk "Søg på hjemmesiden igen".'
        )
    response.raise_for_status()
    return response.json()


# ---- The catalogue -----------------------------------------------------------

# Products per request: Shopify's most
CATALOGUE_PAGE_SIZE = 250
# A safety stop: the whole shop was 3 pages in November 2026
CATALOGUE_MAX_PAGES = 100
# Downloaded again after this long, so a batch reviewed hours later still
# gets today's prices (seconds)
CATALOGUE_MAX_AGE = 60 * 60
# After a failed download, the next attempt waits this long - so a blocked
# or offline shop isn't asked again for every label (seconds)
CATALOGUE_RETRY_AFTER = 60
# The product list also refuses requests that come too fast (HTTP 429): a
# pause between its pages, and when refused, these waits before asking again
# - once per batch, so waiting is better than failing (seconds). The pause is
# a random length in this range, so the pages don't arrive like clockwork
CATALOGUE_PAGE_PAUSE = (1.5, 3.5)
CATALOGUE_BUSY_WAITS = (5, 15, 30)


def _wait(seconds: float) -> None:
    """time.sleep, as its own function so the tests can skip the waiting."""
    time.sleep(seconds)


def _compact(code: str) -> str:
    return " ".join(code.split()).casefold()


class Catalogue:
    """Every product on the webshop, with a candidate per variant, and the
    two ways a label finds one: by its number (a variant's SKU) and by its
    name (every word of it in the variant's name)."""

    def __init__(self, products: list[Product], downloaded_at: float):
        self.products = products
        self.downloaded_at = downloaded_at  # time.monotonic()
        self._by_handle = {product.url.rsplit("/", 1)[-1]: product for product in products}
        # The shop's SKU is the item number of one exact variant (colour,
        # size); no two variants share one
        self._by_sku = {}
        # (product, candidate, name tokens) per variant, and for every word
        # in those names the variants that have it
        self._entries = []
        self._words = {}
        for product in products:
            for candidate in product.candidates:
                if candidate.sku:
                    self._by_sku.setdefault(_compact(candidate.sku), (product, candidate))
                tokens = name_tokens(candidate.name)
                for word in set(tokens):
                    self._words.setdefault(word, set()).add(len(self._entries))
                self._entries.append((product, candidate, tokens))
        # A label word -> the variants with a word that matches it (see _tokens_match)
        self._matching = {}

    def product(self, handle: str) -> Product | None:
        return self._by_handle.get(handle)

    def variant_with_sku(self, item_number: str) -> tuple[Product, Candidate] | None:
        """The product and variant whose SKU is the label's item number."""
        return self._by_sku.get(_compact(item_number))

    def _variants_matching(self, label_token: str) -> set[int]:
        if label_token not in self._matching:
            self._matching[label_token] = set().union(
                *(variants for word, variants in self._words.items() if _tokens_match(label_token, word))
            )
        return self._matching[label_token]

    def candidates_with_name(self, label_tokens: list[str], exclude_url: str = "") -> list[Candidate]:
        """The variants that contain every word of the label's name (numbers,
        sizes and the model name included), best fit first: a near miss
        (Bali -> Cali, Siesta 140x200 -> Siesta 200x300) must never be
        offered. Leaves out the variants of the product at exclude_url."""
        if not label_tokens:
            return []
        found = []
        for index in set.intersection(*(self._variants_matching(token) for token in label_tokens)):
            product, candidate, tokens = self._entries[index]
            if product.url != exclude_url:
                found.append((token_coverage(tokens, label_tokens), index, candidate))
        found.sort(key=lambda entry: (-entry[0], entry[1]))
        return [candidate for _, _, candidate in found]


def download_catalogue(session, report=None) -> Catalogue:
    """The whole shop, from Shopify's public product list: a few requests
    in a row, with a pause between them, rather than several searches for
    every label. ``report(text)``, if given, is told how far it has come,
    in Danish - so a wait for a busy shop doesn't look like a frozen
    window."""
    report = report or (lambda text: None)
    products = []
    for page in range(1, CATALOGUE_MAX_PAGES + 1):
        if page > 1:
            _wait(random.uniform(*CATALOGUE_PAGE_PAUSE))
        report(f"Henter hjemmesidens varer ({len(products)} indtil videre)…")
        data = _get_catalogue_page(session, page, report)
        page_products = data.get("products") or []
        for product_data in page_products:
            handle = product_data.get("handle")
            if not handle:
                continue
            title = (product_data.get("title") or "").strip()
            url = f"{SHOP_URL}/products/{handle}"
            products.append(Product(title, url, _variant_candidates(title, url, product_data)))
        if len(page_products) < CATALOGUE_PAGE_SIZE:
            break
    return Catalogue(products, time.monotonic())


def _get_catalogue_page(session, page: int, report) -> dict:
    """One page of the product list - asked again after a pause when the
    shop is busy (see CATALOGUE_BUSY_WAITS), before giving up."""
    params = {"limit": CATALOGUE_PAGE_SIZE, "page": page}
    for wait in CATALOGUE_BUSY_WAITS:
        try:
            return _get_json(session, f"{SHOP_URL}/products.json", params)
        except ShopBusyError:
            debug_log(f"The shop is busy (page {page} of its products): asking again in {wait} s")
            report(f"Hjemmesiden har travlt – prøver igen om {wait} sekunder…")
            _wait(wait)
    return _get_json(session, f"{SHOP_URL}/products.json", params)


def prefetch_catalogue(session, cache: dict, cache_lock: Lock, report=None) -> bool:
    """Download the catalogue now, at the start of a batch, saying how it
    goes (see download_catalogue) - rather than silently, inside the first
    lookup. The lookups then use it. Returns whether it worked; a failure
    is the lookups' to report, label by label."""
    try:
        _catalogue(session, cache, cache_lock, report)
    except Exception as error:
        debug_log(f"Could not download the catalogue: {error}")
        return False
    return True


def _catalogue(session, cache: dict, cache_lock: Lock, report=None) -> Catalogue:
    """The batch's catalogue: downloaded by the first lookup that needs it
    (the others wait for it), and again once it is CATALOGUE_MAX_AGE old.

    cache_lock guards the cache dict, as several photos are looked up at
    the same time (see OCR_WORKERS). A failed download is remembered for
    CATALOGUE_RETRY_AFTER, and every lookup until then fails the same way."""
    with cache_lock:
        catalogue = cache.get("catalogue")
        if catalogue is not None and time.monotonic() - catalogue.downloaded_at < CATALOGUE_MAX_AGE:
            return catalogue
        failure = cache.get("catalogue_failure")
        if failure is not None and time.monotonic() - failure[0] < CATALOGUE_RETRY_AFTER:
            raise failure[1]
        try:
            catalogue = download_catalogue(session, report)
        except Exception as error:
            cache["catalogue_failure"] = (time.monotonic(), error)
            raise
        cache["catalogue"] = catalogue
        cache.pop("catalogue_failure", None)
        return catalogue


def _absolute_image_url(image) -> str:
    """Shopify gives images as "//cdn..." strings or {"src": ...} dicts."""
    source = image.get("src") if isinstance(image, dict) else image
    if not source:
        return ""
    return f"https:{source}" if source.startswith("//") else source


def _variant_candidates(product_title: str, product_url: str, product_data: dict) -> list[Candidate]:
    """One candidate per variant of a product (or one for a product without
    variants). The same shape is used for automatic matching and for the
    review window's choices."""
    product_image = _absolute_image_url(
        product_data.get("featured_image") or (product_data.get("images") or [None])[0]
    )
    variants = product_data.get("variants") or [{}]
    candidates = []
    for variant in variants:
        variant_title = (variant.get("title") or "").strip()
        if not variant_title or variant_title.casefold() == "default title":
            name = product_title
        else:
            name = f"{product_title} {variant_title}"
        candidates.append(
            Candidate(
                name=name,
                price=_format_shop_price(variant.get("price", product_data.get("price"))),
                compare_price=_format_shop_price(
                    variant.get("compare_at_price", product_data.get("compare_at_price"))
                ),
                id=variant.get("id"),
                sku=(variant.get("sku") or "").strip(),
                url=_variant_url(product_url, variant.get("id")),
                image=_absolute_image_url(variant.get("featured_image")) or product_image,
            )
        )
    return candidates


def _variant_url(product_url: str, variant_id) -> str:
    """A link that opens the product page with this variant (colour, size)
    and its price selected."""
    if variant_id:
        return f"{product_url}?variant={variant_id}"
    return product_url


def _rank_by_name(candidates: list[Candidate], label_tokens: list[str]) -> list[tuple]:
    """Candidates sorted best first, each as (label_coverage,
    shop_coverage, candidate): label_coverage = how much of the label's
    name is in the shop name, shop_coverage = the reverse."""
    ranked = []
    for candidate in candidates:
        shop_tokens = name_tokens(candidate.name)
        ranked.append(
            (
                token_coverage(label_tokens, shop_tokens),
                token_coverage(shop_tokens, label_tokens),
                candidate,
            )
        )
    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return ranked


def format_combined_price(current_price: str, compare_price: str) -> str:
    """ "8.999 kr (12.999 kr)": the sale price, with the normal price in brackets."""
    if current_price and compare_price and current_price != compare_price:
        return f"{current_price} ({compare_price})"
    return current_price


# ---- Looking up a label ------------------------------------------------------


def look_up_product(
    session, cache: dict, cache_lock: Lock, mk_varenummer: str, varenavn: str, part: str = ""
) -> tuple[ShopResult, list[Warning]]:
    """Find the label's product on the webshop (see Catalogue).

    Fills in varenavn/price/url only when it is safe to do so without
    anyone looking: the label's number is the SKU of a variant, and that
    variant contains every word of the label's name. Only part of an item
    (``part``, e.g. "1/2") gets its name and link that way but never a
    price; a part that isn't confirmed like this also gets a "part"
    warning. Everything else returns ``candidates`` for the user to choose
    from, plus a warning that says what to check:

    - the number isn't on the shop but the name is -> offered, to confirm
      (the number is often a supplier code, e.g. "UE 45990201")
    - the number points to a different product than the name -> both are
      offered, as the label's number may be wrong (e.g. 106258 is a Vetro
      table, but the label says Manhattan sofa)
    - the number's variant doesn't have every word of the name (a German
      label, a typo) -> its product's variants are offered, that one first
    - neither is found -> nothing is offered

    With a number but no name (the user typed only the number in the
    review window, or the name couldn't be read), the number's variant
    fills in name, price and link, with a "by_number" warning so it is
    never approved without someone looking.

    Warnings are (category, message) tuples, category one of
    SHOP_WARNING_CATEGORIES."""
    result, warnings = _look_up(session, cache, cache_lock, mk_varenummer, varenavn, part)
    if part and not result.varenavn:
        warnings.append(
            (
                "part",
                f"Delvis vare ({part}) - der sættes ingen pris. Tjek at labelen er læst rigtigt.",
            )
        )
    return result, warnings


def _look_up(
    session, cache: dict, cache_lock: Lock, mk_varenummer: str, varenavn: str, part: str
) -> tuple[ShopResult, list[Warning]]:
    result = ShopResult()
    label_tokens = name_tokens(varenavn)
    item_number = PART_SUFFIX_RE.sub("", (mk_varenummer or "").strip())
    if not item_number and not label_tokens:
        return result, [("shop", "Kan ikke søge på hjemmesiden uden varenummer eller varenavn")]
    try:
        catalogue = _catalogue(session, cache, cache_lock)
    except Exception as error:
        return result, [("shop", f"Kunne ikke søge på hjemmesiden: {error}")]

    number_product, sku_variant = (catalogue.variant_with_sku(item_number) if item_number else None) or (
        None,
        None,
    )
    if number_product is not None and not label_tokens:
        return _number_only(sku_variant, item_number, part)

    ranked_variants = []
    if number_product is not None:
        # The number names the variant; the label's name must agree with it
        if token_coverage(label_tokens, name_tokens(sku_variant.name)) == 1.0:
            result.varenavn = sku_variant.name
            result.url = sku_variant.url
            if part:
                return result, []  # a part never gets a price
            result.price = sku_variant.price
            result.compare_price = sku_variant.compare_price
            warnings = [] if sku_variant.price else [("shop", "Kunne ikke læse aktuel pris på hjemmesiden")]
            return result, warnings
        # If it doesn't (a German label, a typo), it is still the first suggestion
        ranked_variants = _rank_by_name(number_product.candidates, label_tokens)
        ranked_variants.sort(key=lambda entry: entry[2] is not sku_variant)

    number_url = number_product.url if number_product is not None else ""
    name_candidates = catalogue.candidates_with_name(label_tokens, number_url)
    number_candidates = [replace(candidate, found_by="nummer") for _, _, candidate in ranked_variants[:5]]
    name_candidates = [replace(candidate, found_by="navn") for candidate in name_candidates]
    if number_product is not None and name_candidates:
        result.candidates = name_candidates[:3] + number_candidates[:3]
        return result, [
            (
                "conflict",
                f'Nummeret {item_number} hører til "{number_product.title}" på hjemmesiden, '
                f'men labelen siger "{varenavn}". Vælg den rigtige vare.',
            )
        ]
    if number_product is not None:
        result.candidates = number_candidates
        return result, [("variant", "Navnet passer ikke præcist med hjemmesiden - vælg den rigtige vare")]
    if name_candidates:
        result.candidates = name_candidates[:5]
        return result, [
            (
                "by_name",
                f"Nummeret {item_number} findes ikke på hjemmesiden, men varen blev fundet via navnet. "
                "Tjek at det er den rigtige."
                if item_number
                else "Varen blev fundet via navnet. Tjek at det er den rigtige.",
            )
        ]
    return result, [("not_found", "Varen blev ikke fundet på hjemmesiden")]


def _number_only(variant: Candidate, item_number: str, part: str) -> tuple[ShopResult, list[Warning]]:
    """The lookup for a number without a name: there is no name to check
    the product against, so the user always confirms it."""
    result = ShopResult(varenavn=variant.name, url=variant.url)
    if not part:  # a part never gets a price
        result.price = variant.price
        result.compare_price = variant.compare_price
    return result, [
        ("by_number", f"Udfyldt ud fra nummeret {item_number} alene. Tjek at varen passer med labelen.")
    ]


def remembered_candidate(session, cache: dict, cache_lock: Lock, choice: dict) -> Candidate | None:
    """A remembered choice (see choices.py) as a suggestion, with today's
    price - or None when the shop no longer has that variant, or it is now
    called something else (the shop reorganised its products, so the old
    choice may no longer be what the user picked)."""
    handle = re.search(r"/products/([^?/]+)", choice["url"])
    if handle is None:
        return None
    try:
        product = _catalogue(session, cache, cache_lock).product(handle.group(1))
    except Exception:
        return None
    if product is None:
        return None
    for candidate in product.candidates:
        if candidate.id == choice["variant_id"]:
            if name_tokens(candidate.name) != name_tokens(choice["name"]):
                return None
            return replace(candidate, found_by="valgt før", chosen_before=choice["count"])
    return None
