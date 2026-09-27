"""Finding a label's product on the webshop."""

import difflib
import re

from .config import SHOP_URL
from .parsing import PART_PREFIX_RE, PART_SUFFIX_RE, STK_TAG_RE

DEFAULT_TITLE_RE = re.compile(r"\bDefault Title\b", flags=re.IGNORECASE)


# Warning categories that come from the webshop lookup (see
# lookup_shop_price) - replaced wholesale whenever the lookup reruns
SHOP_WARNING_CATEGORIES = {"conflict", "by_name", "variant", "not_found", "shop"}


def name_tokens(name):
    """The words used to compare a label's product name with a webshop
    name: lowercase, punctuation and "|" dropped, × read as x, and without
    a "- 6 stk." quantity tag or a "1/2 " part prefix - those are on the
    label but never in the shop's name."""
    name = PART_PREFIX_RE.sub("", (name or "").strip())
    name = STK_TAG_RE.sub("", name)
    name = DEFAULT_TITLE_RE.sub("", name).replace("×", "x")
    return [token for token in re.split(r"[^\w]+", name.casefold()) if token]


def tokens_match(label_token, shop_token):
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


def token_coverage(tokens, other_tokens):
    """Share of `tokens` found in `other_tokens` (0.0 - 1.0)."""
    if not tokens:
        return 0.0
    found = sum(1 for token in tokens if any(tokens_match(token, other) for other in other_tokens))
    return found / len(tokens)


def format_shop_price(value):
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


def _shop_get_json(session, url, params=None):
    response = session.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    response.raise_for_status()
    return response.json()


def search_shop(session, cache, cache_lock, query, limit):
    """Product handles the webshop's own search suggests for `query`, best
    first. Cached per (query, limit) - only successful searches, so a
    transient failure isn't stuck for the rest of the batch.

    cache_lock guards the dict since multiple images are looked up
    concurrently (see OCR_WORKERS)."""
    cache_key = ("search", query, limit)
    with cache_lock:
        if cache_key in cache:
            return cache[cache_key]
    data = _shop_get_json(
        session,
        f"{SHOP_URL}/search/suggest.json",
        {"q": query, "resources[type]": "product", "resources[limit]": limit},
    )
    products = data.get("resources", {}).get("results", {}).get("products", [])
    handles = [product["handle"] for product in products if product.get("handle")]
    with cache_lock:
        cache[cache_key] = handles
    return handles


def fetch_shop_product(session, cache, cache_lock, handle):
    """One webshop product as {title, url, candidates} - a candidate per
    variant (see build_variant_candidates). Cached per handle."""
    cache_key = ("product", handle)
    with cache_lock:
        if cache_key in cache:
            return cache[cache_key]
    product_data = _shop_get_json(session, f"{SHOP_URL}/products/{handle}.js")
    title = (product_data.get("title") or "").strip()
    url = f"{SHOP_URL}/products/{handle}"
    product = {
        "title": title,
        "url": url,
        "candidates": build_variant_candidates(title, url, product_data),
    }
    with cache_lock:
        cache[cache_key] = product
    return product


def shop_image_url(image):
    """Shopify gives images as "//cdn..." strings or {"src": ...} dicts."""
    source = image.get("src") if isinstance(image, dict) else image
    if not source:
        return ""
    return f"https:{source}" if source.startswith("//") else source


def build_variant_candidates(product_title, product_url, product_data):
    """Flatten one fetched product into a list of candidates - one per
    variant, or a single entry for a variant-less product. Used both for
    automatic matching and for the review window's "pick the right one"
    choices, so the two always agree on what a candidate looks like.
    `url` deep-links straight to the variant (see build_variant_url)."""
    product_image = shop_image_url(product_data.get("featured_image"))
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
                "url": build_variant_url(product_url, variant.get("id")),
                "image": shop_image_url(variant.get("featured_image")) or product_image,
            }
        )
    return candidates


def build_variant_url(product_url, variant_id):
    """A product page URL that deep-links straight to one specific
    variant - most Shopify themes pre-select that variant's colour/size
    option (and its price) on load instead of defaulting to whichever
    variant happens to be listed first."""
    if variant_id:
        return f"{product_url}?variant={variant_id}"
    return product_url


def rank_candidates(candidates, label_tokens):
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


def clear_winner(ranked):
    """The one candidate that contains every word of the label's name and
    fits it strictly better than any other - or None. Two variants that
    both fit equally well (the label doesn't say which colour) is not a
    winner."""
    if not ranked or ranked[0][0] < 1.0:
        return None
    if len(ranked) > 1 and ranked[1][:2] == ranked[0][:2]:
        return None
    return ranked[0][2]


def find_by_name(session, cache, cache_lock, varenavn, label_tokens, exclude_url=""):
    """Candidates found by searching the webshop for the label's name,
    keeping only those that contain every word of it - numbers, sizes and
    the model name included. Needed because the webshop search always
    returns *something*: when the real product isn't on the shop it
    suggests a near miss (Bali -> Cali, Siesta 140x200 -> Siesta 200x300),
    and those must never be offered."""
    query = " ".join(name_tokens(varenavn))
    if not query:
        return []
    handles = search_shop(session, cache, cache_lock, query, 3)
    if not handles:
        # Long names with colour/options can be too specific for the
        # shop's search - retry with just the start of the name
        short_query = " ".join(label_tokens[:3])
        if short_query != query:
            handles = search_shop(session, cache, cache_lock, short_query, 3)
    found = []
    for handle in handles:
        product = fetch_shop_product(session, cache, cache_lock, handle)
        if product["url"] == exclude_url:
            continue
        for coverage, shop_coverage, candidate in rank_candidates(product["candidates"], label_tokens):
            if coverage == 1.0:
                found.append((shop_coverage, candidate))
    found.sort(key=lambda entry: entry[0], reverse=True)
    return [candidate for _, candidate in found]


def format_combined_price(current_price, compare_price):
    if current_price and compare_price and current_price != compare_price:
        return f"{current_price} ({compare_price})"
    return current_price


def lookup_shop_price(session, cache, cache_lock, mk_varenummer, varenavn, part=""):
    """Find the label's product on the webshop.

    Fills in varenavn/price/url only when it is safe to do so without
    anyone looking: the product was found via the label's own number,
    exactly one variant contains every word of the label's name, and the
    item isn't marked as only part of the item (`part`, those never get
    a price).
    Everything else returns `candidates` for the user to choose from, plus a
    warning that says what to check:

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
    number = PART_SUFFIX_RE.sub("", (mk_varenummer or "").strip())
    if not number and not label_tokens:
        return result, [("shop", "Kan ikke søge på hjemmesiden uden varenummer eller varenavn")]

    try:
        by_number = None
        if number:
            handles = search_shop(session, cache, cache_lock, number, 1)
            if handles:
                by_number = fetch_shop_product(session, cache, cache_lock, handles[0])

        ranked = rank_candidates(by_number["candidates"], label_tokens) if by_number else []
        winner = clear_winner(ranked)
        if winner is not None:
            if part:
                # Identified, but a part never gets a price - offer it
                # so the user can confirm with one click
                result["candidates"] = [{**winner, "found_by": "nummer"}]
                return result, []
            result["varenavn"] = winner["name"]
            result["price"] = winner["price"]
            result["compare_price"] = winner["compare_price"]
            result["url"] = winner["url"]
            warnings = [] if winner["price"] else [("shop", "Kunne ikke læse aktuel pris på hjemmesiden")]
            return result, warnings

        by_name = (
            find_by_name(
                session, cache, cache_lock, varenavn, label_tokens, by_number["url"] if by_number else ""
            )
            if label_tokens
            else []
        )
    except Exception as error:
        return result, [("shop", f"Kunne ikke søge på hjemmesiden: {error}")]

    number_candidates = [{**candidate, "found_by": "nummer"} for _, _, candidate in ranked[:5]]
    by_name = [{**candidate, "found_by": "navn"} for candidate in by_name]
    if by_number and by_name:
        result["candidates"] = by_name[:3] + number_candidates[:3]
        return result, [
            (
                "conflict",
                f'Nummeret {number} hører til "{by_number["title"]}" på hjemmesiden, '
                f'men labelen siger "{varenavn}". Vælg den rigtige vare.',
            )
        ]
    if by_number:
        result["candidates"] = number_candidates
        return result, [("variant", "Navnet passer ikke præcist med hjemmesiden - vælg den rigtige vare")]
    if by_name:
        result["candidates"] = by_name[:5]
        return result, [
            (
                "by_name",
                f"Nummeret {number} findes ikke på hjemmesiden, men varen blev fundet via navnet. "
                "Tjek at det er den rigtige."
                if number
                else "Varen blev fundet via navnet. Tjek at det er den rigtige.",
            )
        ]
    return result, [("not_found", "Varen blev ikke fundet på hjemmesiden")]
