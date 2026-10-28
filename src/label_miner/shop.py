"""Finding a label's product on the webshop.

The shop is a Shopify store; its public search (``/search/suggest.json``)
and product (``/products/<handle>.js``) endpoints are used.

The strategy is built around one rule: a price is only filled in when it is
certain. The product is searched by the label's number; the result counts
only if exactly one of its variants contains every word of the label's name.
Everything else becomes a question for the user, with the likely products
offered as choices.
"""

import difflib
import re
from threading import Lock

from .config import SHOP_URL
from .parsing import PART_PREFIX_RE, PART_SUFFIX_RE, STK_TAG_RE

Candidate = dict  # {name, price, compare_price, id, url, image[, found_by]}

DEFAULT_TITLE_RE = re.compile(r"\bDefault Title\b", flags=re.IGNORECASE)

# Warning categories that come from the webshop lookup. They are replaced as
# a whole whenever the lookup runs again (e.g. after the user fixed a field).
SHOP_WARNING_CATEGORIES = {"conflict", "by_name", "variant", "not_found", "shop"}


def name_tokens(name: str) -> list[str]:
    """The words used to compare a label's product name with a webshop
    name: lowercase, punctuation and "|" dropped, × read as x, and without
    a "- 6 stk." quantity tag or a "1/2 " part prefix - those are on the
    label but never in the shop's name."""
    name = PART_PREFIX_RE.sub("", (name or "").strip())
    name = STK_TAG_RE.sub("", name)
    name = DEFAULT_TITLE_RE.sub("", name).replace("×", "x")
    return [token for token in re.split(r"[^\w]+", name.casefold()) if token]


def tokens_match(label_token: str, shop_token: str) -> bool:
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
    found = sum(1 for token in tokens if any(tokens_match(token, other) for other in other_tokens))
    return found / len(tokens)


def format_shop_price(value) -> str:
    """A Shopify price as Danish text: 1699900 (øre) or "16999.00" (kroner) -> "16.999 kr"."""
    if value in (None, ""):
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return ""
    # Shopify search JSON uses kroner as strings; product .js uses oere as integers.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        amount /= 100
    if amount.is_integer():
        return f"{int(amount):,}".replace(",", ".") + " kr"
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr"


def _get_json(session, url, params=None):
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    return response.json()


def search_product_handles(session, cache: dict, cache_lock: Lock, query: str, limit: int) -> list[str]:
    """Product handles the webshop's own search suggests for `query`, best
    first. Cached per (query, limit) - only successful searches, so a
    transient failure isn't stuck for the rest of the batch.

    cache_lock guards the dict, as several photos are looked up at the same
    time (see OCR_WORKERS)."""
    cache_key = ("search", query, limit)
    with cache_lock:
        if cache_key in cache:
            return cache[cache_key]
    data = _get_json(
        session,
        f"{SHOP_URL}/search/suggest.json",
        {"q": query, "resources[type]": "product", "resources[limit]": limit},
    )
    products = data.get("resources", {}).get("results", {}).get("products", [])
    handles = [product["handle"] for product in products if product.get("handle")]
    with cache_lock:
        cache[cache_key] = handles
    return handles


def fetch_product(session, cache: dict, cache_lock: Lock, handle: str) -> dict:
    """One webshop product as {title, url, candidates} - a candidate per
    variant (see variant_candidates). Cached per handle."""
    cache_key = ("product", handle)
    with cache_lock:
        if cache_key in cache:
            return cache[cache_key]
    product_data = _get_json(session, f"{SHOP_URL}/products/{handle}.js")
    title = (product_data.get("title") or "").strip()
    url = f"{SHOP_URL}/products/{handle}"
    product = {
        "title": title,
        "url": url,
        "candidates": variant_candidates(title, url, product_data),
    }
    with cache_lock:
        cache[cache_key] = product
    return product


def absolute_image_url(image) -> str:
    """Shopify gives images as "//cdn..." strings or {"src": ...} dicts."""
    source = image.get("src") if isinstance(image, dict) else image
    if not source:
        return ""
    return f"https:{source}" if source.startswith("//") else source


def variant_candidates(product_title: str, product_url: str, product_data: dict) -> list[Candidate]:
    """One candidate per variant of a product (or one for a product without
    variants). The same shape is used for automatic matching and for the
    review window's choices."""
    product_image = absolute_image_url(product_data.get("featured_image"))
    variants = product_data.get("variants") or [{}]
    candidates = []
    for variant in variants:
        variant_title = (variant.get("title") or "").strip()
        if not variant_title or variant_title.casefold() == "default title":
            name = product_title
        else:
            name = f"{product_title} {variant_title}"
        candidates.append(
            {
                "name": name,
                "price": format_shop_price(variant.get("price", product_data.get("price"))),
                "compare_price": format_shop_price(
                    variant.get("compare_at_price", product_data.get("compare_at_price"))
                ),
                "id": variant.get("id"),
                "url": variant_url(product_url, variant.get("id")),
                "image": absolute_image_url(variant.get("featured_image")) or product_image,
            }
        )
    return candidates


def variant_url(product_url: str, variant_id) -> str:
    """A link that opens the product page with this variant (colour, size)
    and its price selected."""
    if variant_id:
        return f"{product_url}?variant={variant_id}"
    return product_url


def rank_by_name(candidates: list[Candidate], label_tokens: list[str]) -> list[tuple]:
    """Candidates sorted best first, each as (label_coverage,
    shop_coverage, candidate): label_coverage = how much of the label's
    name is in the shop name, shop_coverage = the reverse."""
    ranked = []
    for candidate in candidates:
        shop_tokens = name_tokens(candidate["name"])
        ranked.append(
            (
                token_coverage(label_tokens, shop_tokens),
                token_coverage(shop_tokens, label_tokens),
                candidate,
            )
        )
    ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return ranked


def single_best_match(ranked: list[tuple]) -> Candidate | None:
    """The one candidate that contains every word of the label's name and
    fits it strictly better than any other - or None. Two variants that
    both fit equally well (the label doesn't say which colour) is not a
    match."""
    if not ranked or ranked[0][0] < 1.0:
        return None
    if len(ranked) > 1 and ranked[1][:2] == ranked[0][:2]:
        return None
    return ranked[0][2]


def find_candidates_by_name(
    session, cache: dict, cache_lock: Lock, varenavn: str, label_tokens: list[str], exclude_url: str = ""
) -> list[Candidate]:
    """Candidates found by searching the webshop for the label's name.

    Only candidates that contain every word of the name (numbers, sizes and
    the model name included) are kept: the shop's search always returns
    *something*, and when the real product isn't on the shop that is a near
    miss (Bali -> Cali, Siesta 140x200 -> Siesta 200x300) that must never be
    offered.
    """
    query = " ".join(name_tokens(varenavn))
    if not query:
        return []
    handles = search_product_handles(session, cache, cache_lock, query, 3)
    if not handles:
        # Long names with colour/options can be too specific for the
        # shop's search - retry with just the start of the name
        short_query = " ".join(label_tokens[:3])
        if short_query != query:
            handles = search_product_handles(session, cache, cache_lock, short_query, 3)
    found = []
    for handle in handles:
        product = fetch_product(session, cache, cache_lock, handle)
        if product["url"] == exclude_url:
            continue
        for coverage, shop_coverage, candidate in rank_by_name(product["candidates"], label_tokens):
            if coverage == 1.0:
                found.append((shop_coverage, candidate))
    found.sort(key=lambda entry: entry[0], reverse=True)
    return [candidate for _, candidate in found]


def format_combined_price(current_price: str, compare_price: str) -> str:
    """ "8.999 kr (12.999 kr)": the sale price, with the normal price in brackets."""
    if current_price and compare_price and current_price != compare_price:
        return f"{current_price} ({compare_price})"
    return current_price


def look_up_product(
    session, cache: dict, cache_lock: Lock, mk_varenummer: str, varenavn: str, part: str = ""
) -> tuple[dict, list[tuple[str, str]]]:
    """Find the label's product on the webshop.

    Fills in varenavn/price/url only when it is safe to do so without
    anyone looking: the product was found via the label's own number,
    exactly one variant contains every word of the label's name, and the
    item isn't marked as only part of an item (those never get a price).
    Everything else returns ``candidates`` for the user to choose from, plus
    a warning that says what to check:

    - the number isn't on the shop but the name is -> offered, to confirm
      (the number is often a supplier code, e.g. "UE 45990201")
    - the number points to a different product than the name -> both are
      offered, as the label's number may be wrong (e.g. 106258 is a Vetro
      table, but the label says Manhattan sofa)
    - neither is found -> nothing is offered

    Warnings are (category, message) tuples, category one of
    SHOP_WARNING_CATEGORIES."""
    result = {"varenavn": "", "price": "", "compare_price": "", "url": "", "candidates": []}
    label_tokens = name_tokens(varenavn)
    item_number = PART_SUFFIX_RE.sub("", (mk_varenummer or "").strip())
    if not item_number and not label_tokens:
        return result, [("shop", "Kan ikke søge på hjemmesiden uden varenummer eller varenavn")]

    try:
        number_product = None
        if item_number:
            handles = search_product_handles(session, cache, cache_lock, item_number, 1)
            if handles:
                number_product = fetch_product(session, cache, cache_lock, handles[0])

        ranked_variants = rank_by_name(number_product["candidates"], label_tokens) if number_product else []
        match = single_best_match(ranked_variants)
        if match is not None:
            if part:
                # Identified, but a part never gets a price - offer it so
                # the user can confirm it with one click
                result["candidates"] = [{**match, "found_by": "nummer"}]
                return result, []
            result["varenavn"] = match["name"]
            result["price"] = match["price"]
            result["compare_price"] = match["compare_price"]
            result["url"] = match["url"]
            warnings = [] if match["price"] else [("shop", "Kunne ikke læse aktuel pris på hjemmesiden")]
            return result, warnings

        number_url = number_product["url"] if number_product else ""
        name_candidates = (
            find_candidates_by_name(session, cache, cache_lock, varenavn, label_tokens, number_url)
            if label_tokens
            else []
        )
    except Exception as error:
        return result, [("shop", f"Kunne ikke søge på hjemmesiden: {error}")]

    number_candidates = [{**candidate, "found_by": "nummer"} for _, _, candidate in ranked_variants[:5]]
    name_candidates = [{**candidate, "found_by": "navn"} for candidate in name_candidates]
    if number_product and name_candidates:
        result["candidates"] = name_candidates[:3] + number_candidates[:3]
        return result, [
            (
                "conflict",
                f'Nummeret {item_number} hører til "{number_product["title"]}" på hjemmesiden, '
                f'men labelen siger "{varenavn}". Vælg den rigtige vare.',
            )
        ]
    if number_product:
        result["candidates"] = number_candidates
        return result, [("variant", "Navnet passer ikke præcist med hjemmesiden - vælg den rigtige vare")]
    if name_candidates:
        result["candidates"] = name_candidates[:5]
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
