from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import difflib
import io
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import webbrowser
import zipfile
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from PIL import Image, ImageOps, ImageTk
from pillow_heif import register_heif_opener

try:
    import sv_ttk
except ImportError:  # optional visual-polish dependency, see requirements
    sv_ttk = None

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:  # optional drag-and-drop dependency: pip install tkinterdnd2
    DND_FILES = None
    TkinterDnD = None


# Shown in the title bar; keep in sync with MyAppVersion in build/installer.iss
APP_VERSION = "2.1"
# The user manual, built by build/manual/make_manual_miner.py. Installed
# next to Label-Miner.exe; opened by the "? Hjælp" buttons.
MANUAL_NAME = "Label-Miner-Vejledning.pdf"

# Pass --debug on the command line (or when launching the frozen .exe from a
# terminal) to see per-image raw OCR text/JSON and other diagnostics printed
# to the console. Without it the program stays silent, which also avoids a
# crash on a --windowed/--noconsole build, where sys.stdout is None.
DEBUG = "--debug" in sys.argv


def debug_log(*args, **kwargs):
    """print(), but only under --debug, and safe when no console is
    attached (sys.stdout is None on a windowed frozen build)."""
    if DEBUG and sys.stdout is not None:
        print(*args, **kwargs)


def open_manual(parent=None):
    """Open the PDF manual: next to the .exe when installed, next to the
    script when run from source."""
    for folder in (Path(sys.executable).parent, Path(__file__).resolve().parent):
        manual = folder / MANUAL_NAME
        if manual.exists():
            os.startfile(manual)
            return
    messagebox.showinfo("Label Miner", f"Vejledningen ({MANUAL_NAME}) blev ikke fundet.", parent=parent)


def resource_path(name):
    """Resolve a bundled resource (icon, docling-models, etc.) next to the
    script when run from source, or inside PyInstaller's bundle once frozen.

    sys._MEIPASS is PyInstaller's own, version-stable answer to "where did
    you actually put the data files I asked you to bundle?" -- NOT the
    folder the .exe sits in. As of PyInstaller 6.x a "--onedir" build puts
    everything except the launcher exe itself into an "_internal" folder
    next to it (a "--onefile" build extracts to a temp folder instead), so
    assuming bundled files sit next to sys.executable silently breaks:
    resource_path() would resolve to the wrong folder, find nothing there,
    and Docling would fall back to needing a live internet connection. Using
    sys._MEIPASS avoids hard-coding that layout, so this keeps working even
    if a future PyInstaller version rearranges things again."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent
    return base / name


def set_window_icon(window):
    icon_path = resource_path("label_miner.ico")
    if not icon_path.exists():
        return
    try:
        window.iconbitmap(str(icon_path))
    except tk.TclError:
        pass  # .ico icons aren't supported on every platform (e.g. Linux)


def resolve_tk_color(widget, color, fallback="#202020"):
    """Resolve a Tk color to a "#rrggbb" string that PIL can actually use
    (e.g. as a rotate() fillcolor). Tk themes - especially Windows' native
    "vista" theme, which is what you get without sv-ttk installed - often
    report a widget's background as a *symbolic* system color name like
    "SystemButtonFace" rather than a hex value. Tk itself understands
    those, but PIL does not, and PIL.Image.rotate(fillcolor=...) raises on
    one instead of falling back to anything. Because that raise happened
    inside a slider callback, it silently aborted the redraw before the
    image ever updated - which looked like "rotation does nothing" - and
    left the rotation angle set, so *every* later redraw (mirror, crop,
    reset) hit the same crash. Going through widget.winfo_rgb() resolves
    any Tk color name (symbolic or not) to real RGB first, so this can
    never hand PIL something it can't parse."""
    try:
        r, g, b = widget.winfo_rgb(color)
        return f"#{r >> 8:02x}{g >> 8:02x}{b >> 8:02x}"
    except tk.TclError:
        return fallback


DEFAULT_FONT_FAMILY = "Segoe UI"
DEFAULT_FONT = (DEFAULT_FONT_FAMILY, 10)


def apply_theme(root):
    """Apply the sv-ttk sleek light/dark theme if it's installed, and set a
    baseline font/spacing that looks reasonable even without it. Returns
    True if sv-ttk was actually applied, so the caller can hint at the
    missing dependency instead of leaving the old look unexplained."""
    sv_ttk_applied = False
    if sv_ttk is not None:
        sv_ttk.set_theme("dark")
        sv_ttk_applied = True

    # Stock ttk (no theme package installed) defaults to a small system
    # font that reads as dated - set a consistent, larger one regardless
    # of whether sv-ttk is available, so the app never looks like this
    # step silently did nothing.
    root.option_add("*Font", DEFAULT_FONT)
    style = ttk.Style(root)
    style.configure(".", font=DEFAULT_FONT)
    style.configure("Treeview", font=DEFAULT_FONT, rowheight=28)
    style.configure("Treeview.Heading", font=(DEFAULT_FONT_FAMILY, 10, "bold"))
    return sv_ttk_applied


def shade_color(hex_color, amount):
    """A "#rrggbb" color moved `amount` (0-1) towards white on a dark
    color, or towards black on a light one - for card backgrounds and
    borders that stand out slightly from the theme's own background."""
    red, green, blue = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    target = 255 if (red + green + blue) / 3 < 128 else 0
    return "#" + "".join(
        f"{round(channel + (target - channel) * amount):02x}" for channel in (red, green, blue)
    )


def accent_button_style():
    """Style name for the app's one 'primary action' button. sv-ttk ships
    an Accent.TButton variant; without sv-ttk installed we fall back to a
    plain button rather than referencing a style that doesn't exist."""
    return "Accent.TButton" if sv_ttk is not None else "TButton"


def install_error_reporting(root):
    """By default, an exception raised inside a Tk callback (a button
    command, a slider drag, a canvas event...) is swallowed - Tkinter just
    prints a traceback that nobody sees once this is a --windowed exe with
    no console attached, and the widget is left however that half-finished
    callback left it. That's exactly what made a past bug here look like
    "rotation does nothing, and now other buttons are broken too": the
    real error was invisible. Route it to a visible error dialog instead,
    on top of always logging it (debug_log so it's not printed when frozen
    with no console)."""

    def report(exc, value, tb):
        debug_log("".join(traceback.format_exception(exc, value, tb)))
        try:
            messagebox.showerror(
                "Uventet fejl",
                f"Der opstod en uventet fejl:\n\n{value}",
                parent=root,
            )
        except Exception:
            pass

    root.report_callback_exception = report


SUPPORTED_EXTENSIONS = {
    ".heic",
    ".heif",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
}
MAX_IMAGE_SIDE = 2500
SHOP_URL = "https://moebelkompagniet.dk"

# How many images are processed in flight at once (OCR + webshop price
# lookup together). Docling's DocumentConverter has been verified safe to
# call concurrently from multiple threads against one shared instance (see
# extract_label), and doing so measurably speeds up a batch: on this
# machine (16 logical cores, CPU OCR backend) 3 workers processed a sample
# of label photos ~1.8x faster than sequentially, with zero errors and
# byte-identical OCR text vs. a single-threaded run. Kept modest rather
# than maxed out to the core count - most of the win comes from overlapping
# OCR with the webshop network round trips, and this is a desktop app run
# by one person, not a batch server.
OCR_WORKERS = 3
EXCEL_COLUMNS = [
    "Varenummer",
    "Varenavn",
    "MK Varenummer",
    "MK ordrenr",
    "STAND",
    "STK",
    "Modtaget",
    "Vejledende priser",
    "Pris pr stk",
    "Pris samlet",
    "URL",
    "Image name",
]
COPY_COLUMNS = EXCEL_COLUMNS[:-2]

WHITESPACE_RE = re.compile(r"\s+")
DEFAULT_TITLE_RE = re.compile(r"\bDefault Title\b", flags=re.IGNORECASE)
PIPE_RE = re.compile(r"\s*\|\s*")
# A diameter as OCR reads it, with a zero instead of Ø: "090", "0120"
DIAMETER_DIGITS_RE = re.compile(r"(?<![\w/.,])0(\d{2,3})(?!\d)")

# Strips the timestamp/part suffix Google Drive appends when it zips a
# folder for download, e.g. "9-20260917T102218Z-1-001" -> "9". Only used
# as a fallback name when a zip's images sit directly at its root instead
# of inside a named subfolder (see derive_source_folder).
GOOGLE_DRIVE_ZIP_SUFFIX_RE = re.compile(r"-\d{8}T\d{6}Z-\d+-\d+$")

# A label reads, line by line:
#     <number>, <product name>        (or "<number> - <product name>")
#     OUTLET
#     <order number>
#     STAND: <A/B/C>
# The number is either an MK item number, possibly marked as only part of
# the item ("101338_1/2"), or a supplier code ("UE 45990201", "LI 600200049",
# "AY H000017492", "VN 19987-330" - the "-330" belongs to the code, it is
# not the separator before the name). Supplier codes have 7+ digits or a
# dash, so stray capitals in front of an MK number (box print read as
# "OM 103490") aren't mistaken for one.
LABEL_NUMBER_RE = re.compile(
    r"(?<![\w#])(?P<number>"
    r"[A-Z]{2} ?(?:[A-Z]?\d{7,}|\d{4,}-\d+)"
    r"|\d{5,6}(?:_\d+/\d+)?"
    r")(?!\d)\s*(?:,|-(?!\d))?"
)
# "137.017" - a smudge on the label read as a dot inside the order number
DIGIT_GROUP_DOT_RE = re.compile(r"(?<=\d)\.(?=\d{3}\b)")
OUTLET_RE = re.compile(r"\bOUTLET\b", flags=re.IGNORECASE)
STAND_RE = re.compile(r"\bSTAND\b\s*:?\s*(?P<stand>[A-Z]{1,3}\b)?", flags=re.IGNORECASE)
LABEL_ANCHOR_RE = re.compile(r"\b(?:OUTLET|STAND)\b", flags=re.IGNORECASE)
VALID_STANDS = {"A", "B", "C"}
# Only part of an item: "101338_1/2" / "1/2 Vega ..." = part 1 of 2. The
# other parts may come separately - or not at all.
PART_SUFFIX_RE = re.compile(r"_(?P<part>\d+/\d+)$")
PART_PREFIX_RE = re.compile(r"^(?P<part>\d+/\d+)\s+")
# A quantity tag at the end of the name: "... Naturflet - 1 stk."
STK_TAG_RE = re.compile(r"\s*-?\s*\b\d+\s*stk\b\.?\s*$", flags=re.IGNORECASE)


def warning_messages(warnings):
    """Flatten (category, message) warning tuples down to just the messages."""
    return [message for _, message in warnings]


# Shared severity colors, used by both the review window and the status
# Treeview on the main window, so the same problem reads the same way
# everywhere in the app.
COLOR_OK = "#3fb950"
COLOR_WARNING = "#d29922"
COLOR_ERROR = "#f85149"
COLOR_MUTED = "#9aa0a6"
# The review window's main button - deliberately the only green, filled
# button in the window, so "approve" can't be confused with anything else
COLOR_APPROVE = "#238636"
COLOR_APPROVE_ACTIVE = "#2ea043"

# Warning categories that come from the webshop lookup (see
# lookup_shop_price) - replaced wholesale whenever the lookup reruns
SHOP_WARNING_CATEGORIES = {"conflict", "by_name", "variant", "not_found", "shop"}
MISSING_FIELD_NAMES = {
    "mk_varenummer": "MK-varenummeret",
    "varenavn": "varenavnet",
    "mk_ordrenr": "MK-ordrenummeret",
    "stand": "standen",
}


def review_question(warnings, part=""):
    """The one thing the user is asked to do for a label in review, in plain
    Danish: {"title", "detail", "fields", "others", "color"}. Picked by
    priority from the record's warnings - "fields" are the entry fields to
    highlight, "others" the remaining warning messages, shown smaller.
    Returns None when there is nothing to review."""
    if not warnings:
        return None
    by_category = {}
    for category, message in warnings:
        by_category.setdefault(category, []).append(message)

    def others(*handled):
        return [message for category, message in warnings if category not in handled]

    if "error" in by_category:
        return {
            "title": "Billedet kunne ikke læses",
            "detail": by_category["error"][0]
            + "\nTryk \"Læs billedet igen\", eller skriv felterne ud fra billedet.",
            "fields": list(MISSING_FIELD_NAMES),
            "others": others("error"),
            "color": COLOR_ERROR,
        }
    if "conflict" in by_category:
        return {
            "title": "Nummer og navn passer ikke sammen",
            "detail": by_category["conflict"][0],
            "fields": ["mk_varenummer", "varenavn"],
            "others": others("conflict"),
            "color": COLOR_WARNING,
        }
    if "missing" in by_category:
        missing = [
            field for field, name in MISSING_FIELD_NAMES.items()
            if any(name in message for message in by_category["missing"])
        ]
        if missing == ["stand"]:
            title, detail = "Skriv STAND (A, B eller C)", "Standen kunne ikke læses på labelen."
        else:
            title = "Nogle felter kunne ikke læses"
            detail = "Skriv dem ud fra billedet: " + ", ".join(
                MISSING_FIELD_NAMES[field] for field in missing
            ) + "."
        return {
            "title": title,
            "detail": detail,
            "fields": missing,
            "others": others("missing", "partial"),
            "color": COLOR_WARNING,
        }
    if "suspicious" in by_category:
        message = by_category["suspicious"][0]
        field = "stand" if "STAND" in message else "mk_ordrenr"
        return {
            "title": "Tjek STAND" if field == "stand" else "Tjek MK-ordrenummeret",
            "detail": message.rstrip(".") + (". Ret den til A, B eller C." if field == "stand" else "."),
            "fields": [field],
            "others": others("suspicious"),
            "color": COLOR_WARNING,
        }
    if "part" in by_category:
        return {
            "title": f"Delvis vare (del {part})" if part else "Delvis vare",
            "detail": "Kun en del af varen – der sættes ingen pris. Tjek nummer og navn.",
            "fields": ["mk_varenummer", "varenavn"],
            "others": [
                message for category, message in warnings
                if category not in ("part", "variant", "by_name")
            ],
            "color": COLOR_WARNING,
        }
    if "by_name" in by_category:
        return {
            "title": "Er det den rigtige vare?",
            "detail": by_category["by_name"][0],
            "fields": [],
            "others": others("by_name"),
            "color": COLOR_WARNING,
        }
    if "variant" in by_category:
        return {
            "title": "Vælg den rigtige vare",
            "detail": "Navn eller farve passer ikke præcist med hjemmesiden. "
            "Vælg den vare der passer til labelen.",
            "fields": [],
            "others": others("variant"),
            "color": COLOR_WARNING,
        }
    if "not_found" in by_category:
        return {
            "title": "Varen findes ikke på hjemmesiden",
            "detail": "Tjek nummer og navn på billedet. Prisen skal skrives manuelt.",
            "fields": ["price"],
            "others": others("not_found"),
            "color": COLOR_WARNING,
        }
    category, message = warnings[0]
    return {
        "title": "Tjek labelen",
        "detail": message,
        "fields": ["price"] if category == "shop" else [],
        "others": [other for _, other in warnings[1:]],
        "color": COLOR_WARNING,
    }


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} t {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} sek"
    return f"{seconds} sek"


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
        candidates.append({
            "name": name,
            "price": format_shop_price(variant.get("price", product_data.get("price"))),
            "compare_price": format_shop_price(
                variant.get("compare_at_price", product_data.get("compare_at_price"))
            ),
            "id": variant.get("id"),
            "url": build_variant_url(product_url, variant.get("id")),
            "image": shop_image_url(variant.get("featured_image")) or product_image,
        })
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
        ranked.append((
            token_coverage(label_tokens, shop_tokens),
            token_coverage(shop_tokens, label_tokens),
            candidate,
        ))
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

        by_name = find_by_name(
            session, cache, cache_lock, varenavn, label_tokens, by_number["url"] if by_number else ""
        ) if label_tokens else []
    except Exception as error:
        return result, [("shop", f"Kunne ikke søge på hjemmesiden: {error}")]

    number_candidates = [{**candidate, "found_by": "nummer"} for _, _, candidate in ranked[:5]]
    by_name = [{**candidate, "found_by": "navn"} for candidate in by_name]
    if by_number and by_name:
        result["candidates"] = by_name[:3] + number_candidates[:3]
        return result, [(
            "conflict",
            f"Nummeret {number} hører til \"{by_number['title']}\" på hjemmesiden, "
            f"men labelen siger \"{varenavn}\". Vælg den rigtige vare.",
        )]
    if by_number:
        result["candidates"] = number_candidates
        return result, [("variant", "Navnet passer ikke præcist med hjemmesiden - vælg den rigtige vare")]
    if by_name:
        result["candidates"] = by_name[:5]
        return result, [(
            "by_name",
            f"Nummeret {number} findes ikke på hjemmesiden, men varen blev fundet via navnet. "
            "Tjek at det er den rigtige." if number else
            "Varen blev fundet via navnet. Tjek at det er den rigtige.",
        )]
    return result, [("not_found", "Varen blev ikke fundet på hjemmesiden")]


def derive_source_folder(image_files, extraction_root=None, zip_path=None):
    """Figure out the "batch name" (used for the Excel first row and its
    filename) and the directory to save the export into, from wherever the
    images actually came from.

    - Zip case (extraction_root/zip_path given): the expected shape is one
      inner folder holding all the photos (e.g. "15-9\\IMG_....HEIC"), so
      the common parent of the images is that folder, and we save next to
      the zip file itself. If the images sit directly at the zip root
      instead, fall back to the zip's own filename.
    - Loose-file case: the common parent of whatever was selected/dropped
      doubles as both the batch name and the export directory.
    """
    parents = {path.parent for path in image_files}
    try:
        # os.path.commonpath raises ValueError when the paths don't share
        # a drive (e.g. loose files hand-picked from C:\ and a USB/network
        # drive in the same dialog/drag-drop) - treat that the same as "no
        # common ancestor" instead of letting it blow up file selection.
        common = Path(os.path.commonpath([str(parent) for parent in parents])) if parents else None
    except ValueError:
        common = None

    if extraction_root is not None:
        if common is not None and common != extraction_root:
            return common.name, zip_path.parent
        name = GOOGLE_DRIVE_ZIP_SUFFIX_RE.sub("", zip_path.stem) or zip_path.stem
        return name, zip_path.parent

    if common is not None and common.name:
        return common.name, common
    return datetime.now().strftime("%d-%m"), (common or Path.cwd())


EXTRACT_DIR_PREFIX = "label_miner_"
EXTRACT_OWNER_FILE = "owner.pid"
# Folders from before the owner file existed (v1.0) are only removed once
# they're this old - no Label Miner session lasts that long unnoticed
UNMARKED_EXTRACT_DIR_MAX_AGE = 24 * 60 * 60


def process_is_running(pid):
    """True if a process with this id exists (Windows). A reused id counts
    as running - that only means a leftover folder is kept a bit longer."""
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def clean_up_old_extract_dirs(temp_dir=None):
    """Delete zip-extraction folders left behind by earlier Label Miner
    sessions that didn't close normally (on_close removes its own).

    Never touches a folder another running Label Miner is using: a
    folder is removed only if the program that wrote its owner.pid is no
    longer running - however old it is, since a batch can stay open for
    days - or, for folders from before owner.pid existed, once it is more
    than a day old. Runs on a background thread at startup; returns the
    removed folders (for the log)."""
    temp_dir = Path(temp_dir or tempfile.gettempdir())
    removed = []
    for folder in temp_dir.glob(f"{EXTRACT_DIR_PREFIX}*"):
        if not folder.is_dir():
            continue
        owner_file = folder / EXTRACT_OWNER_FILE
        try:
            if owner_file.exists():
                owner = int(owner_file.read_text(encoding="ascii").strip())
                if owner == os.getpid() or process_is_running(owner):
                    continue
            elif time.time() - folder.stat().st_mtime < UNMARKED_EXTRACT_DIR_MAX_AGE:
                continue
        except (OSError, ValueError):
            continue  # can't tell whose it is - leave it
        shutil.rmtree(folder, ignore_errors=True)
        removed.append(folder)
    return removed


def load_images_from_zip(zip_path):
    """Extract a Google-Drive-style folder zip to a temp directory and
    collect the supported images inside it, wherever they are nested.
    Returns (image_files, extract_dir) - the caller keeps extract_dir
    alive for the app's lifetime since images are re-opened by path
    throughout the review flow."""
    extract_dir = Path(tempfile.mkdtemp(prefix=EXTRACT_DIR_PREFIX))
    # Marks the folder as this running program's, so another Label Miner
    # starting up leaves it alone (see clean_up_old_extract_dirs)
    (extract_dir / EXTRACT_OWNER_FILE).write_text(str(os.getpid()), encoding="ascii")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    image_files = sorted(
        path
        for path in extract_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and "__MACOSX" not in path.parts
        and not path.name.startswith("._")
    )
    return image_files, extract_dir


def unique_export_path(directory, base_name):
    """The export path for base_name in directory, never an existing
    file - appends " (1)", " (2)", ... instead of overwriting anything."""
    candidate = directory / f"{base_name}.xlsx"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = directory / f"{base_name} ({counter}).xlsx"
        if not candidate.exists():
            return candidate
        counter += 1


class DoclingImageMiner:
    def __init__(self, root, theme_applied=True):
        self.root = root
        self.root.title(f"Label Miner {APP_VERSION}")
        self.center_window(860, 600)
        self.root.minsize(700, 480)
        set_window_icon(self.root)

        self.theme_applied = theme_applied
        self.files = []
        self.records = []
        self.converter = None
        self.http_session = requests.Session()
        # Sized for OCR_WORKERS concurrent images each potentially making
        # their own webshop request at once - default pool_maxsize is 10,
        # which would silently throttle price lookups back to serial once
        # more than 10 are in flight (e.g. during a fast batch with a lot
        # of cache misses).
        adapter = HTTPAdapter(pool_maxsize=max(OCR_WORKERS * 2, 10))
        self.http_session.mount("https://", adapter)
        self.http_session.mount("http://", adapter)
        self.price_cache = {}
        self.price_cache_lock = threading.Lock()
        # Product photos for the review window's suggestion cards, by URL
        self.thumbnail_cache = {}
        self.loading = True
        self.processing = False
        self.source_folder_name = ""
        self.export_dir = None
        self.exported = False
        self.export_path = None
        self.temp_extract_dirs = []
        self.review_queue = queue.Queue()
        self.review_window = None
        # Tracks standalone editors opened via reopen_record_review, keyed
        # by id(record) - lets a second double-click on the same row (e.g.
        # while a "Prøv igen" retry it just kicked off is still running in
        # the background, see retry()/retry_finished) bring the existing
        # editor forward instead of opening a second one that could race
        # the first (or the in-flight retry thread) writing to the same
        # record dict.
        self.open_standalone_windows = {}
        self.review_total = 0
        self.review_completed = 0
        self.work_total = 0
        self.work_completed = 0
        self.work_durations = []
        self.progress_info = tk.StringVar(value="")

        self.status = tk.StringVar(value="Indlæser Docling og dets modeller...")
        self.selected_count = tk.StringVar(value="Valgte billeder: 0")

        # The window shows exactly one of three "screens" at a time - a
        # boot screen while Docling's models load, a select screen with one
        # big unmissable button once it's ready, and the work screen (results
        # list + export/copy) once images are actually being processed -
        # rather than one crowded window with disabled controls that don't
        # explain why they're disabled.
        self.screen_container = ttk.Frame(root, padding=24)
        self.screen_container.pack(fill="both", expand=True)

        self.build_boot_screen()
        self.build_select_screen()
        self.build_work_screen()
        self.boot_frame.pack(fill="both", expand=True)

        register_heif_opener()
        self.root.after(100, self.check_review_queue)
        self.boot_progress.start(50)
        threading.Thread(target=self.load_docling, daemon=True).start()
        threading.Thread(target=self._clean_up_old_extract_dirs, daemon=True).start()

    def _clean_up_old_extract_dirs(self):
        try:
            for folder in clean_up_old_extract_dirs():
                debug_log(f"Removed leftover temp folder {folder}")
        except Exception as error:
            debug_log(f"Cleaning up old temp folders failed: {error!r}")

    def center_window(self, width, height):
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2, 0)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def build_boot_screen(self):
        self.boot_frame = ttk.Frame(self.screen_container)
        center = ttk.Frame(self.boot_frame)
        center.pack(expand=True)

        ttk.Label(center, text="Label Miner", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(
            center,
            text="Klargør OCR-motoren...",
            foreground=COLOR_MUTED,
        ).pack(pady=(4, 24))

        if not self.theme_applied:
            ttk.Label(
                center,
                text=(
                    "Tip: kør \"pip install sv-ttk\" og genstart for det "
                    "moderne udseende - det er ikke installeret lige nu."
                ),
                foreground=COLOR_WARNING,
                wraplength=380,
                justify="center",
            ).pack(pady=(0, 16))

        self.boot_progress = ttk.Progressbar(center, mode="indeterminate", length=280)
        self.boot_progress.pack()
        ttk.Label(center, textvariable=self.status, foreground=COLOR_MUTED).pack(pady=(12, 0))

        # Only shown on a load failure (see loading_failed/retry_load_docling)
        # so a broken Docling install doesn't leave the user stuck staring at
        # a spinner that will never finish.
        self.boot_retry_button = ttk.Button(
            center, text="Prøv igen", command=self.retry_load_docling
        )

    def build_select_screen(self):
        self.select_frame = ttk.Frame(self.screen_container)
        center = ttk.Frame(self.select_frame)
        center.pack(expand=True)

        ttk.Label(center, text="Label Miner", font=("Segoe UI", 22, "bold")).pack()
        ttk.Label(
            center,
            text="Vælg label-billeder eller en ZIP-mappe, og få dem læst automatisk.",
            foreground=COLOR_MUTED,
        ).pack(pady=(4, 24))

        big_style = "Big." + accent_button_style()
        style = ttk.Style(self.root)
        style.configure(big_style, font=(DEFAULT_FONT_FAMILY, 14, "bold"), padding=(28, 18))
        self.select_button = ttk.Button(
            center,
            text="📂  Vælg billeder eller ZIP",
            command=self.select_images,
            style=big_style,
        )
        self.select_button.pack()
        ttk.Button(
            self.select_frame, text="?  Hjælp", command=lambda: open_manual(self.root)
        ).place(relx=1.0, rely=0.0, anchor="ne")

        drop_targets = [self.select_frame, center, self.select_button]
        if DND_FILES is not None:
            hint = ttk.Label(
                center,
                text="...eller træk billeder eller en ZIP-fil ind i vinduet",
                foreground=COLOR_MUTED,
            )
            hint.pack(pady=(12, 0))
            drop_targets.append(hint)
            for widget in drop_targets:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.handle_dropped_files)
        else:
            ttk.Label(
                center,
                text=(
                    "Tip: kør \"pip install tkinterdnd2\" og genstart for at "
                    "kunne trække billeder ind i vinduet."
                ),
                foreground=COLOR_WARNING,
                wraplength=380,
                justify="center",
            ).pack(pady=(12, 0))

    def build_work_screen(self):
        self.work_frame = ttk.Frame(self.screen_container)

        header = ttk.Frame(self.work_frame)
        header.pack(fill="x")
        ttk.Label(header, text="Label Miner", font=("Segoe UI", 18, "bold")).pack(side="left")
        ttk.Button(header, text="?  Hjælp", command=lambda: open_manual(self.root)).pack(side="right")
        ttk.Label(
            header,
            textvariable=self.selected_count,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right", padx=(0, 16))

        # Progress row (bar + ETA text) is only shown while work is actually
        # happening - built here but not packed until update_progress_info
        # decides there's something to show, instead of permanently
        # occupying space with an idle "Ingen behandling i gang" message.
        self.progress_frame = ttk.Frame(self.work_frame)
        self.progress = ttk.Progressbar(self.progress_frame, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(
            self.progress_frame,
            textvariable=self.progress_info,
            foreground=COLOR_MUTED,
        ).pack(anchor="w", pady=(4, 0))

        # Shown whenever there are labels waiting for review - the review
        # window only opens when the user asks for it, instead of popping up in
        # the middle of whatever they're looking at (see check_review_queue).
        self.review_bar = ttk.Frame(self.work_frame)
        self.review_button = ttk.Button(
            self.review_bar,
            text="🔍  Gennemse labels",
            command=self.open_review,
            style=accent_button_style(),
        )
        self.review_button.pack(side="left")
        ttk.Label(
            self.review_bar,
            text="Nogle labels skal tjekkes, før de kan eksporteres.",
            foreground=COLOR_WARNING,
        ).pack(side="left", padx=(12, 0))
        self.review_bar_visible = False

        # Result list: one row per processed image (filename / status /
        # short summary) instead of a raw text+JSON dump - the full raw
        # OCR text and parsed JSON are only ever shown where a decision is
        # actually needed (the review window) or via --debug.
        self.results_frame = ttk.Frame(self.work_frame)
        self.results_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.results_frame.columnconfigure(0, weight=1)
        self.results_frame.rowconfigure(0, weight=1)

        self.results_tree = ttk.Treeview(
            self.results_frame,
            columns=("file", "status", "summary"),
            show="headings",
            selectmode="browse",
        )
        self.results_tree.heading("file", text="Fil")
        self.results_tree.heading("status", text="Status")
        self.results_tree.heading("summary", text="Resultat")
        self.results_tree.column("file", width=190, anchor="w", stretch=False)
        self.results_tree.column("status", width=100, anchor="w", stretch=False)
        self.results_tree.column("summary", width=380, anchor="w")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        results_scroll = ttk.Scrollbar(
            self.results_frame, orient="vertical", command=self.results_tree.yview
        )
        results_scroll.grid(row=0, column=1, sticky="ns")
        self.results_tree.configure(yscrollcommand=results_scroll.set)
        self.results_tree.tag_configure("ok", foreground=COLOR_OK)
        self.results_tree.tag_configure("warning", foreground=COLOR_WARNING)
        self.results_tree.tag_configure("error", foreground=COLOR_ERROR)
        # Double-click any already-processed row to reopen it for editing -
        # see reopen_record_review.
        self.results_tree.bind("<Double-1>", self.reopen_record_review)

        # Export/copy live in their own frame, packed but left empty until
        # enable_export_controls() fills it in - so they don't exist on
        # screen at all (not even greyed out) until there's actually
        # something finished to export, including anything sent through a
        # review-window retry.
        self.actions_frame = ttk.Frame(self.work_frame)
        self.actions_frame.pack(fill="x", pady=(12, 0))
        self.export_ready_label = ttk.Label(
            self.actions_frame,
            text="✅ Færdig — klar til eksport",
            font=(DEFAULT_FONT_FAMILY, 10, "bold"),
            foreground=COLOR_OK,
        )
        self.actions_buttons = ttk.Frame(self.actions_frame)
        self.copy_button = ttk.Button(
            self.actions_buttons,
            text="📋  Kopiér resultat",
            command=self.copy_result,
            style=accent_button_style(),
        )
        self.copy_button.pack(side="left")

        ttk.Separator(self.work_frame, orient="horizontal").pack(fill="x", pady=(12, 6))
        ttk.Label(self.work_frame, textvariable=self.status, foreground=COLOR_MUTED).pack(anchor="w")

    def load_docling(self):
        try:
            from docling.document_converter import DocumentConverter
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                RapidOcrOptions,
            )
            from docling.document_converter import ImageFormatOption

            # Pinned explicitly to RapidOCR's torch backend -- NOT left as
            # Docling's "auto" default. Auto-selection picks whichever OCR
            # engine happens to be importable, in a fixed priority order,
            # which silently changes behavior if your installed packages
            # change: this app used RapidOCR (torch) for its whole
            # history simply because `easyocr` was never pip-installed. The
            # moment `docling[easyocr]` got installed (while pre-fetching
            # models for the packaged build), auto-detection silently
            # switched to EasyOCR's English-only recognizer instead -- no
            # error, no warning, just measurably worse OCR on Danish labels
            # (verified: it dropped leading reference numbers like
            # "103035 -" that RapidOCR reads correctly). Pinning the engine
            # here makes behavior deterministic regardless of what else is
            # installed on the machine.
            pipeline_options = PdfPipelineOptions(
                do_ocr=True,
                do_table_structure=False,
                do_picture_classification=False,
                do_picture_description=False,
                images_scale=1.0,
                generate_page_images=False,
                generate_picture_images=False,
                ocr_options=RapidOcrOptions(backend="torch"),
            )
            # If build/fetch_models.ps1 (or the "Bundle Docling models" step
            # in build/README.md) has been run, a pre-fetched copy of the
            # layout + EasyOCR models sits next to this script (or next to
            # the .exe once frozen). Point Docling at it so it never has to
            # reach Hugging Face at runtime. If the folder isn't there,
            # artifacts_path stays None and Docling falls back to its normal
            # Hugging Face cache, downloading models on first use instead.
            models_dir = resource_path("docling-models")
            if models_dir.is_dir():
                pipeline_options.artifacts_path = models_dir
                debug_log(f"Using bundled Docling models from {models_dir}")
            else:
                debug_log(
                    "No bundled docling-models folder found next to "
                    f"{models_dir.parent}; Docling will use its normal "
                    "Hugging Face cache and needs internet on first run."
                )
            self.converter = DocumentConverter(
                allowed_formats=[InputFormat.IMAGE],
                format_options={
                    InputFormat.IMAGE: ImageFormatOption(
                        pipeline_options=pipeline_options,
                    ),
                },
            )
        except Exception as error:
            self.root.after(0, self.loading_failed, error)
            return
        self.root.after(0, self.docling_ready)

    def docling_ready(self):
        self.loading = False
        self.boot_progress.stop()
        self.boot_frame.pack_forget()
        self.select_frame.pack(fill="both", expand=True)
        self.status.set("Docling er klar. Vælg billeder for at starte.")
        # Photos or a zip dropped onto the program's desktop icon (or
        # opened with it) arrive as command-line arguments - start on them
        # right away, as if they'd been picked in the file dialog
        startup_paths = [arg for arg in sys.argv[1:] if not arg.startswith("--") and Path(arg).exists()]
        if startup_paths:
            self.begin_processing(startup_paths)

    def loading_failed(self, error):
        self.loading = False
        self.boot_progress.stop()
        self.status.set("Docling kunne ikke indlæses.")
        self.boot_retry_button.pack(pady=(16, 0))
        messagebox.showerror(
            "Docling ikke tilgængelig",
            "Installer Docling med: pip install docling pillow-heif\n\n"
            f"Detaljer: {error}",
            parent=self.root,
        )

    def retry_load_docling(self):
        self.loading = True
        self.boot_retry_button.pack_forget()
        self.status.set("Indlæser Docling og dets modeller...")
        self.boot_progress.start(50)
        threading.Thread(target=self.load_docling, daemon=True).start()

    def select_images(self):
        image_patterns = [f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS)]
        selected = filedialog.askopenfilenames(
            parent=self.root,
            title="Vælg billeder eller en ZIP-fil",
            filetypes=[
                ("Billeder og ZIP", " ".join(image_patterns + ["*.zip"])),
                ("ZIP-fil", "*.zip"),
                ("Billeder", " ".join(image_patterns)),
                ("Alle filer", "*.*"),
            ],
        )
        if not selected:
            return
        self.begin_processing(selected)

    def handle_dropped_files(self, event):
        self.begin_processing(self.root.tk.splitlist(event.data))

    def begin_processing(self, paths):
        """Entry point shared by the file dialog and drag-and-drop. Only a
        non-empty, actually-supported selection consumes the select screen -
        cancelling the dialog or dropping something unsupported leaves the
        one-shot select button right where it was."""
        paths = [Path(p) for p in paths]
        zip_paths = [path for path in paths if path.suffix.lower() == ".zip"]

        if zip_paths:
            if len(zip_paths) > 1:
                messagebox.showwarning(
                    "Flere ZIP-filer",
                    "Der blev valgt flere ZIP-filer - kun den første bruges.",
                    parent=self.root,
                )
            zip_path = zip_paths[0]
            self.status.set("Udpakker ZIP-fil...")
            self.root.update_idletasks()
            try:
                files, extract_dir = load_images_from_zip(zip_path)
            except Exception as error:
                messagebox.showerror(
                    "Kunne ikke åbne ZIP-filen", str(error), parent=self.root
                )
                return
            self.temp_extract_dirs.append(extract_dir)
            if not files:
                messagebox.showwarning(
                    "Ingen billeder fundet",
                    "ZIP-filen indeholder ingen understøttede billedformater.",
                    parent=self.root,
                )
                return
            folder_name, export_dir = derive_source_folder(
                files, extraction_root=extract_dir, zip_path=zip_path
            )
        else:
            files = [path for path in paths if path.suffix.lower() in SUPPORTED_EXTENSIONS]
            if not files:
                if paths:
                    messagebox.showwarning(
                        "Ingen billeder fundet",
                        "Ingen af filerne er understøttede billedformater.",
                        parent=self.root,
                    )
                return
            folder_name, export_dir = derive_source_folder(files)

        self.files = files
        self.source_folder_name = folder_name
        self.export_dir = export_dir
        self.exported = False
        self.export_path = None
        self.selected_count.set(f"Valgte billeder: {len(self.files)}")
        # The select screen is consumed for good here - this app processes
        # one batch per launch, so the button never needs to come back.
        self.select_frame.pack_forget()
        self.work_frame.pack(fill="both", expand=True)
        self.start_processing()

    def start_processing(self):
        if not self.files or self.converter is None or self.processing:
            return
        self.processing = True
        self.progress.configure(value=0, maximum=len(self.files))
        self.progress.configure(mode="determinate")
        self.results_tree.delete(*self.results_tree.get_children())
        self.records = []
        self.review_total = 0
        self.review_completed = 0
        self.work_total = len(self.files)
        self.work_completed = 0
        self.work_durations = []
        self.update_progress_info()
        self.start_busy_indicator()
        threading.Thread(target=self.process_in_background, daemon=True).start()

    def _extract_one(self, path):
        """Runs on a worker thread (see process_in_background). Exceptions
        are caught here, not left to propagate through the future, so one
        bad image can't abort the rest of the batch - matching the original
        per-image try/except in the sequential loop this replaced."""
        started_at = time.perf_counter()
        try:
            raw_text, parsed_label, warnings, candidates = extract_label(
                self.converter,
                path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
            )
        except Exception as error:
            raw_text = ""
            parsed_label = {}
            warnings = [("error", f"Behandling af billedet fejlede: {error}")]
            candidates = []
        duration = time.perf_counter() - started_at
        return raw_text, parsed_label, warnings, candidates, duration

    def process_in_background(self):
        """Runs up to OCR_WORKERS images through _extract_one at once
        (see that constant's comment for why this is safe/worthwhile).
        This method itself stays the single thread that ever appends to
        self.records / self.review_queue or increments review_total, so
        those aren't touched concurrently even though the OCR/lookup work
        behind each future is. self.records is pre-sized and filled by
        original file position rather than completion order, so a retry
        or the exported Excel still reflect the batch in the order the
        photos were selected, regardless of which finished OCR first."""
        try:
            self.records = [None] * len(self.files)
            completed_count = 0
            worker_count = max(1, min(OCR_WORKERS, len(self.files)))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(self._extract_one, path): (position, path)
                    for position, path in enumerate(self.files)
                }
                for future in as_completed(futures):
                    position, path = futures[future]
                    raw_text, parsed_label, warnings, candidates, duration = future.result()
                    completed_count += 1
                    debug_log(f"\n===== {path.name} =====")
                    debug_log("RAW TEXT")
                    debug_log(raw_text or "<empty>")
                    debug_log("PARSED LABEL")
                    debug_log(json.dumps(parsed_label, indent=2, ensure_ascii=False))
                    for warning in warning_messages(warnings):
                        debug_log(f"Advarsel: {warning}")
                    record = {
                        "path": path,
                        "raw_text": raw_text,
                        "parsed": parsed_label,
                        "warnings": warnings,
                        "candidates": candidates,
                        # True while a record is sitting in review_queue
                        # waiting for (or actively going through) the live
                        # review popup - reopen_record_review refuses to
                        # open a standalone editor for one of these, so a
                        # row can't be resolved twice (once through each
                        # window) and double-count review_completed.
                        "review_pending": bool(warnings),
                    }
                    self.records[position] = record
                    if warnings:
                        self.review_total += 1
                        self.review_queue.put(record)
                    self.root.after(
                        0,
                        self.show_result,
                        path,
                        raw_text,
                        parsed_label,
                        warnings,
                        completed_count,
                        duration,
                    )
        except Exception as error:
            self.root.after(0, self.processing_failed, error)
        else:
            self.root.after(0, self.processing_finished)

    def show_result(self, path, raw_text, parsed_label, warnings, index, duration):
        if index == 1:
            self.stop_busy_indicator()
        self.upsert_result_row(path, parsed_label, warnings, raw_text)
        self.complete_work_unit(duration)
        self.status.set(f"Behandlet {index} af {len(self.files)} billeder.")

    @staticmethod
    def _status_for_warnings(warnings):
        if not warnings:
            return "OK", "ok"
        if any(category == "error" for category, _ in warnings):
            return "Fejl", "error"
        return "Gennemse", "warning"

    @staticmethod
    def _summary_for_record(parsed_label, raw_text):
        varenavn = parsed_label.get("varenavn", "")
        mk_varenummer = parsed_label.get("mk_varenummer", "")
        stand = parsed_label.get("stand", "")
        parts = [
            value
            for value in (varenavn, mk_varenummer, f"Stand {stand}" if stand else "")
            if value
        ]
        if parts:
            return " · ".join(parts)
        text = (raw_text or "").strip()
        if text:
            return (text[:80] + "…") if len(text) > 80 else text
        return "Ingen tekst genkendt"

    def upsert_result_row(self, path, parsed_label, warnings, raw_text):
        """Add (or, on a retry, update in place) this image's row in the
        result list - filename, a status tag driving its color, and a short
        human-readable summary. The full raw OCR text/JSON never appears
        here; it's only shown in the review window or via --debug."""
        status_text, tag = self._status_for_warnings(warnings)
        summary = self._summary_for_record(parsed_label, raw_text)
        iid = str(path)
        values = (path.name, status_text, summary)
        # No .see() - the list must not scroll away from whatever row is
        # being read while new results come in
        if self.results_tree.exists(iid):
            self.results_tree.item(iid, values=values, tags=(tag,))
        else:
            self.results_tree.insert("", tk.END, iid=iid, values=values, tags=(tag,))

    def complete_work_unit(self, duration):
        self.work_completed += 1
        self.work_durations.append(duration)
        self.progress.configure(
            value=self.work_completed,
            maximum=max(self.work_total, self.work_completed),
        )
        self.update_progress_info()

    def update_progress_info(self):
        if not self.work_total:
            # Nothing going on - hide the whole progress row rather than
            # showing an idle "Ingen behandling i gang" message.
            self.progress_frame.pack_forget()
            self.progress_info.set("")
            return
        self.progress_frame.pack(fill="x", pady=(0, 12), before=self.results_frame)
        completed = min(self.work_completed, self.work_total)
        if completed >= self.work_total:
            self.progress_info.set(f"Færdig: {completed}/{self.work_total}")
            return
        if not self.work_durations:
            self.progress_info.set(
                f"Starter behandling: {completed}/{self.work_total}"
            )
            return
        average = sum(self.work_durations) / len(self.work_durations)
        remaining_seconds = average * (self.work_total - completed)
        eta = format_duration(remaining_seconds)
        percentage = completed / self.work_total * 100
        self.progress_info.set(
            f"{completed}/{self.work_total} ({percentage:.0f} %) | "
            f"Ca. {eta} tilbage"
        )

    def processing_finished(self):
        self.processing = False
        if self.review_total:
            self.status.set("Behandling færdig. Tryk \"Gennemse labels\" for at tjekke de sidste labels.")
            self.maybe_enable_export_controls()
        else:
            self.status.set("Færdig. Klar til eksport.")
            self.enable_export_controls()

    def processing_failed(self, error):
        self.stop_busy_indicator()
        self.processing = False
        self.status.set("Behandlingen fejlede.")
        messagebox.showerror("Kunne ikke behandle billederne", str(error), parent=self.root)
        if self.records:
            self.enable_export_controls()

    def start_busy_indicator(self):
        """Switch the progress bar to an indeterminate 'something is
        happening' animation - used while waiting for the first image of a
        batch to come back. A slow step interval (Tk's own default is 50ms)
        keeps this a smooth back-and-forth slide instead of a rapid,
        flickery re-draw."""
        self.stop_busy_indicator()
        self.progress.configure(mode="indeterminate")
        self.progress.start(50)

    def stop_busy_indicator(self):
        self.progress.stop()
        self.progress.configure(mode="determinate")

    def check_review_queue(self):
        """Runs every 200 ms for the app's whole lifetime: keeps the
        "Gennemse labels (N)" button in step with how many labels are
        waiting. It never opens the review window by itself - that only
        happens when the button is pressed (open_review). Wrapped in
        try/finally so one error can't stop the polling for the rest of
        the session."""
        try:
            pending = self.review_total - self.review_completed
            if pending > 0:
                self.review_button.configure(text=f"🔍  Gennemse labels ({pending})")
                if not self.review_bar_visible:
                    self.review_bar.pack(fill="x", pady=(0, 12), before=self.results_frame)
                    self.review_bar_visible = True
            elif self.review_bar_visible:
                self.review_bar.pack_forget()
                self.review_bar_visible = False
            if self.review_window is not None and self.review_window.record is not None:
                self.review_window.update_review_count()
        finally:
            self.root.after(200, self.check_review_queue)

    def open_review(self):
        """"Gennemse labels": show the review window - on the item it was
        closed on, if any, otherwise on the next one in the queue."""
        if self.review_window is not None and self.review_window.record is not None:
            self.review_window.show()
            return
        record = self._pop_next_review_record()
        if record is None:
            self.status.set("Ingen labels venter lige nu - de dukker op, når de er læst.")
            return
        try:
            if self.review_window is None:
                self.review_window = ReviewWindow(self, record)
            else:
                self.review_window.load_record(record)
            self.review_window.show()
        except Exception:
            # Don't let a record vanish from the queue just because showing
            # it blew up - put it back so the button can try again, instead
            # of leaving review_total stuck one ahead of review_completed.
            self.review_queue.put(record)
            raise

    def _pop_next_review_record(self):
        try:
            return self.review_queue.get_nowait()
        except queue.Empty:
            return None

    def advance_review_window(self):
        """Move the (persistent) review window on to the next queued item in
        place, instead of hiding it and waiting for the next poll to open a
        new one - keeps back-to-back reviews from flickering."""
        if self.review_window is None:
            return
        record = self._pop_next_review_record()
        if record is None:
            self.review_window.hide()
            return
        try:
            self.review_window.load_record(record)
        except Exception:
            # Same reasoning as check_review_queue's finally block: don't
            # let a record vanish from the queue just because showing it
            # blew up - put it back so the next poll retries it instead of
            # leaving review_total stuck one ahead of review_completed.
            self.review_queue.put(record)
            raise

    def reopen_record_review(self, event):
        """Double-click handler on results_tree: reopens the editor for an
        already-processed row, so a mistake spotted after the fact (or
        just after the whole batch is done) doesn't require re-running
        anything. Refuses to open one for a record still sitting in
        review_queue (record["review_pending"]) - that one is already
        going through the live review popup, and opening a second editor
        for the same record could let both resolve it and double-count
        review_completed."""
        row_id = self.results_tree.identify_row(event.y)
        if not row_id:
            return
        record = next(
            (r for r in self.records if r is not None and str(r["path"]) == row_id),
            None,
        )
        if record is None:
            return
        if record.get("review_pending"):
            # Still waiting in the batch review - open that instead of a
            # second editor on the same record
            self.open_review()
            return
        if self.review_window is not None and self.review_window.record is record:
            self.review_window.window.deiconify()
            self.review_window.window.lift()
            return
        existing = self.open_standalone_windows.get(id(record))
        if existing is not None:
            if existing.window.winfo_exists():
                # Already has its own editor open - bring that one forward
                # instead of opening a second editor on the same record.
                existing.window.deiconify()
                existing.window.lift()
            else:
                # Its "Prøv igen" retry closed the editor and is still
                # running in the background (see retry()/retry_finished) -
                # the record isn't settled yet, so don't race it with a
                # second editor; the row will update on its own once the
                # retry completes.
                self.status.set("Genlæser billedet i baggrunden - vent til det er færdigt.")
            return
        self.open_standalone_windows[id(record)] = ReviewWindow(self, record, standalone=True)

    def review_finished(self, record, updated_record):
        record["review_pending"] = False
        if updated_record is not None:
            debug_log("REVIEWED LABEL")
            debug_log(json.dumps(updated_record["parsed"], indent=2, ensure_ascii=False))
            self.upsert_result_row(
                updated_record["path"],
                updated_record["parsed"],
                updated_record["warnings"],
                updated_record.get("raw_text", ""),
            )
        self.review_completed += 1
        self.status.set(
            f"Gennemgang fuldført: {self.review_completed} af {self.review_total}."
        )
        self.maybe_enable_export_controls()
        self.advance_review_window()

    def maybe_enable_export_controls(self):
        if not self.processing and self.review_completed >= self.review_total:
            self.status.set("Færdig. Klar til eksport.")
            self.enable_export_controls()

    def enable_export_controls(self):
        self.auto_export_excel()
        self.export_ready_label.pack(anchor="w")
        self.actions_buttons.pack(anchor="w", pady=(6, 0))

    def excel_rows(self):
        rows = [[self.source_folder_name] + [""] * (len(EXCEL_COLUMNS) - 1)]

        # Group records that represent the same physical item (identical MK
        # Varenummer, MK ordrenr and STAND) into a single row, with STK set
        # to how many copies were found. Records where all three fields are
        # blank (e.g. failed OCR) are never merged with each other, since we
        # can't actually tell whether they're the same item.
        groups = {}
        group_order = []
        for record in self.records:
            parsed = record["parsed"]
            key = (
                parsed.get("mk_varenummer", ""),
                parsed.get("mk_ordrenr", ""),
                parsed.get("stand", ""),
            )
            if not any(key):
                key = ("__unique__", id(record))
            if key not in groups:
                groups[key] = {"first": record, "count": 0, "image_names": []}
                group_order.append(key)
            group = groups[key]
            group["count"] += 1
            group["image_names"].append(record["path"].name)

        for key in group_order:
            group = groups[key]
            record = group["first"]
            parsed = record["parsed"]
            comments = parsed.get("price", "")
            if record.get("warnings"):
                warning_text = " | ".join(warning_messages(record["warnings"]))
                comments = " | ".join(value for value in (comments, warning_text) if value)
            rows.append([
                "",
                parsed.get("varenavn", ""),
                parsed.get("mk_varenummer", ""),
                parsed.get("mk_ordrenr", ""),
                parsed.get("stand", ""),
                group["count"],
                "X",
                comments,
                "",
                "",
                parsed.get("url", ""),
                ", ".join(group["image_names"]),
            ])
        return rows

    def auto_export_excel(self):
        """Saves the Excel automatically as soon as there's nothing left to
        review - no dialog, no button - and again any time a record's data
        changes afterward (a standalone edit or rescan via
        reopen_record_review, see ReviewWindow.confirm/retry_finished), so
        the saved file never silently drifts out of date once you've
        started relying on it.

        self.export_path is resolved once (via unique_export_path, which
        picks a name that doesn't collide with an existing file) and then
        reused for every subsequent save, so re-running this overwrites
        that same file in place instead of picking a new "(1)" name each
        time the way a fresh unique_export_path() call would."""
        if not self.records:
            return
        try:
            from openpyxl import Workbook

            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "Label Miner"
            rows = self.excel_rows()
            worksheet.append(rows[0])
            worksheet.append(EXCEL_COLUMNS)
            for row in rows[1:]:
                worksheet.append(row)
            if self.export_path is None:
                self.export_path = unique_export_path(self.export_dir, self.source_folder_name)
            workbook.save(self.export_path)
        except Exception as error:
            self.export_ready_label.configure(
                text="⚠ Excel-eksport fejlede", foreground=COLOR_ERROR
            )
            messagebox.showerror("Excel eksport fejlede", str(error), parent=self.root)
            return
        self.exported = True
        self.status.set(f"Færdig. Excel gemt: {self.export_path.name}")
        self.export_ready_label.configure(
            text=f"✅ Excel gemt: {self.export_path.name}", foreground=COLOR_OK
        )

    def copy_result(self):
        rows = self.excel_rows()
        values = [
            "\t".join(str(value) for value in row[: len(COPY_COLUMNS)])
            for row in rows
        ]
        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(values))
        self.root.update()
        self.status.set("Resultat kopieret uden kolonnenavne, URL og billednavn.")

    def on_close(self):
        """Clean up any zip extraction temp dirs (see load_images_from_zip)
        before the window closes, so they don't pile up in %TEMP% run
        after run."""
        for extract_dir in self.temp_extract_dirs:
            shutil.rmtree(extract_dir, ignore_errors=True)
        self.root.destroy()

    def retry_review(self, record, working_image, standalone=False):
        self.work_total += 1
        self.progress.configure(maximum=self.work_total)
        self.update_progress_info()
        self.status.set("Genlæser label i baggrunden...")
        # A retry launched from a standalone-reopened record (see
        # reopen_record_review) must never touch the live review popup -
        # that record isn't in review_queue/review_total's bookkeeping, so
        # advancing/hiding the shared window here would yank it out from
        # under an unrelated, actually-in-progress batch review.
        if not standalone:
            self.advance_review_window()
        threading.Thread(
            target=self.retry_review_in_background,
            args=(record, working_image),
            daemon=True,
        ).start()

    def retry_review_in_background(self, record, working_image):
        """Only runs the OCR and reports the outcome back to the main
        thread - never touches review_queue/review_total/review_pending
        itself. All of that bookkeeping happens in retry_finished, on the
        main thread, regardless of whether this came from the live review
        queue or a standalone-reopened row (see reopen_record_review):
        centralizing it there is what makes it safe for two retries to
        finish around the same moment without racing on the shared
        counters (the same class of bug previously fixed for
        review_completed's plain +=, which briefly lived directly in this
        background method)."""
        temporary_path = None
        started_at = time.perf_counter()
        try:
            temporary_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            temporary_path = Path(temporary_file.name)
            temporary_file.close()
            working_image.save(temporary_path, format="PNG")
            raw_text, parsed_label, warnings, candidates = extract_label(
                self.converter,
                temporary_path,
                self.http_session,
                self.price_cache,
                self.price_cache_lock,
            )
            record.update(
                raw_text=raw_text,
                parsed=parsed_label,
                warnings=warnings,
                candidates=candidates,
                working_image=working_image,
            )
            debug_log(f"\n===== {record['path'].name} RETRY =====")
            debug_log("RAW TEXT")
            debug_log(raw_text or "<empty>")
            debug_log("PARSED LABEL")
            debug_log(json.dumps(parsed_label, indent=2, ensure_ascii=False))
            for warning in warning_messages(warnings):
                debug_log(f"Advarsel: {warning}")
            duration = time.perf_counter() - started_at
            self.root.after(0, self.show_retry_result, record, warnings, duration)
            self.root.after(0, self.retry_finished, record, warnings)
        except Exception as error:
            record["warnings"] = [("error", f"Genlæsning fejlede: {error}")]
            duration = time.perf_counter() - started_at
            self.root.after(0, self.show_retry_result, record, record["warnings"], duration)
            self.root.after(0, self.retry_finished, record, record["warnings"])
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except OSError as error:
                    debug_log(f"Advarsel: kunne ikke slette midlertidig fil {temporary_path}: {error}")

    def retry_finished(self, record, warnings):
        """A rescan that still turns up warnings always goes back up for
        review - whether it started from the live review queue or a
        standalone-reopened row - rather than silently leaving a
        half-read label with no way back into the review flow. Records
        reopened standalone start out not counted in review_total (see
        reopen_record_review's review_pending guard), so a fresh set of
        warnings here counts as newly pending rather than assuming it was
        already counted."""
        was_pending = record.get("review_pending", False)
        if warnings:
            record["review_pending"] = True
            if not was_pending:
                self.review_total += 1
            self.review_queue.put(record)
            self.status.set("Genlæsning kræver stadig gennemgang.")
        else:
            if was_pending:
                record["review_pending"] = False
                self.review_completed += 1
            self.status.set("Genlæsning lykkedes.")
        # Safe no-op if this retry didn't come from a standalone editor
        # (see reopen_record_review/open_standalone_windows) - now that the
        # background OCR is done and the record dict is settled, it's safe
        # for a double-click to open a fresh editor on it again.
        self.open_standalone_windows.pop(id(record), None)
        self.maybe_enable_export_controls()

    def show_retry_result(self, record, warnings, duration):
        self.upsert_result_row(
            record["path"], record["parsed"], warnings, record.get("raw_text", "")
        )
        self.complete_work_unit(duration)


class ReviewWindow:
    """The window the user uses to check a label that couldn't be approved
    automatically. Built to be hard to get wrong:

    - one plain question at the top says what to check (review_question),
      and the fields it concerns are marked
    - webshop suggestions are picture cards, not buttons that look like
      text fields, and picking one only fills in the fields - nothing is
      saved until "Godkend"
    - "Godkend" is the only green button; "Læs billedet igen" sits under
      the photo with the other photo tools, so it can't be mistaken for it
    - leaving with unsaved edits asks first
    """

    THUMBNAIL_SIZE = 72

    def __init__(self, app, record, standalone=False):
        """standalone=True is reopen_record_review's path back into an
        already-processed record: same window/UI, but confirm/skip/retry
        never touch review_completed/review_total/review_queue (see those
        methods below) - only the live batch-review flow (the queue-driven
        popup tracked as app.review_window) owns that bookkeeping."""
        self.app = app
        self.standalone = standalone
        self.record = None
        self.window = tk.Toplevel(app.root)
        self.window.title("Rediger label" if standalone else "Label-gennemgang")
        # Centred, and never bigger than the screen (laptops, 125-150 %
        # display scaling)
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        width = min(1180, screen_width - 60)
        height = min(800, screen_height - 100)
        x = max((screen_width - width) // 2, 0)
        y = max((screen_height - height) // 2 - 20, 0)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(min(980, width), min(640, height))
        # ✕ on the batch review just closes the window - the item stays
        # where it was (edits included) and "Gennemse labels" on the main
        # window brings it back. On a reopened row it means "Luk".
        self.window.protocol("WM_DELETE_WINDOW", self.skip if standalone else self.close_window)
        set_window_icon(self.window)

        # Rotation fill and canvas backdrop share this color so the border
        # added around a rotated image blends into the background instead
        # of standing out as a bright frame. Pulled from the active ttk
        # theme so it tracks light/dark mode instead of being hardcoded -
        # resolved to a real hex value (see resolve_tk_color) since PIL
        # can't parse the symbolic color names ttk themes sometimes report.
        style = ttk.Style(self.window)
        theme_background = style.lookup("TFrame", "background")
        self.canvas_background = resolve_tk_color(self.window, theme_background or "#202020")
        self.text_color = resolve_tk_color(
            self.window, style.lookup("TLabel", "foreground") or "#ffffff", "#ffffff"
        )
        self.card_background = shade_color(self.canvas_background, 0.08)
        self.card_border = shade_color(self.canvas_background, 0.25)
        # Disclosure toggles ("▶ Flere værktøjer") read as plain text links
        style.configure("Candidate.TButton", anchor="w")

        self.window.columnconfigure(0, weight=3, minsize=480)
        self.window.columnconfigure(1, weight=2, minsize=420)
        self.window.rowconfigure(0, weight=1, minsize=420)

        # ---- Left: the photo, and everything that acts on the photo -----
        self.image_frame = ttk.Frame(self.window, padding=12)
        self.image_frame.grid(row=0, column=0, sticky="nsew")
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.rowconfigure(0, weight=1)

        # Has to be set before the rotation slider below: its initial
        # .set(0) fires _on_rotation_slider synchronously, which calls
        # resize_image(), which reads self.original_image.
        self.original_image = None
        self.working_image = None
        self.crop_drag_start = None
        self.pending_crop_fraction = None
        self.image_bounds = None
        self.rotation_angle = 0.0
        # The label's area as fractions of the photo (found during OCR,
        # see extract_label) - shown zoomed in on by default
        self.zoom_box = None
        self.zoomed = False

        self.image_canvas = tk.Canvas(
            self.image_frame,
            background=self.canvas_background,
            highlightthickness=0,
        )
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", self.resize_image)
        self.image_canvas.bind("<ButtonPress-1>", self.crop_start_event)
        self.image_canvas.bind("<B1-Motion>", self.crop_drag_event)
        self.image_canvas.bind("<ButtonRelease-1>", self.crop_release_event)

        image_actions = ttk.Frame(self.image_frame)
        image_actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for column in range(4):
            image_actions.columnconfigure(column, weight=1)
        ttk.Button(
            image_actions, text="⟲ Drej", command=lambda: self.rotate_quarter_turn(-90)
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            image_actions, text="⟳ Drej", command=lambda: self.rotate_quarter_turn(90)
        ).grid(row=0, column=1, sticky="ew", padx=4)
        self.zoom_button = ttk.Button(image_actions, text="Vis hele billedet", command=self.toggle_zoom)
        self.zoom_button.grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(
            image_actions, text="↻ Læs billedet igen", command=self.retry
        ).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        self.tools_visible = False
        self.tools_toggle = ttk.Button(
            self.image_frame,
            text="▶ Flere værktøjer",
            command=self.toggle_tools,
            style="Candidate.TButton",
        )
        self.tools_toggle.grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.tools_frame = ttk.Frame(self.image_frame)
        self.tools_frame.grid(row=3, column=0, sticky="ew")
        self.tools_frame.columnconfigure(1, weight=1)

        ttk.Label(self.tools_frame, text="Rotation").grid(row=0, column=0, padx=(0, 8))
        # The label has to exist before the slider: the slider's initial
        # .set(0) fires its -command callback immediately, and that
        # callback updates this label.
        self.rotation_label = ttk.Label(self.tools_frame, text="0°", width=5, anchor="e")
        self.rotation_slider = ttk.Scale(
            self.tools_frame,
            from_=-180,
            to=180,
            orient="horizontal",
            command=self._on_rotation_slider,
        )
        self.rotation_slider.set(0)
        self.rotation_slider.grid(row=0, column=1, sticky="ew")
        self.rotation_label.grid(row=0, column=2, padx=(8, 0))
        tool_buttons = ttk.Frame(self.tools_frame)
        tool_buttons.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        tool_buttons.columnconfigure(0, weight=1)
        tool_buttons.columnconfigure(1, weight=1)
        ttk.Button(tool_buttons, text="Spejl", command=self.mirror).grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(tool_buttons, text="Nulstil billede", command=self.reset_image).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Label(
            self.tools_frame,
            text="Træk på billedet for at beskære det, og tryk så \"Læs billedet igen\".",
            foreground=COLOR_MUTED,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(self.tools_frame, text="Tekst læst fra labelen:", foreground=COLOR_MUTED).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 2)
        )
        self.raw_text_widget = tk.Text(self.tools_frame, height=3, wrap="word")
        self.raw_text_widget.configure(state="disabled")
        self.raw_text_widget.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.tools_frame.grid_remove()

        # ---- Right: what to check, the choices, the fields, the verdict --
        panel = ttk.Frame(self.window, padding=(18, 18, 18, 0))
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        panel.rowconfigure(1, weight=0)

        # Scrollable middle: the question/choices/fields can grow taller
        # than the window without ever pushing the footer buttons off-screen.
        scroll_area = ttk.Frame(panel)
        scroll_area.grid(row=0, column=0, sticky="nsew")
        scroll_area.columnconfigure(0, weight=1)
        scroll_area.rowconfigure(0, weight=1)

        self.content_canvas = tk.Canvas(scroll_area, highlightthickness=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        content_scrollbar = ttk.Scrollbar(
            scroll_area, orient="vertical", command=self.content_canvas.yview
        )
        content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=content_scrollbar.set)

        content = ttk.Frame(self.content_canvas)
        content.columnconfigure(0, weight=1)
        self.content_window = self.content_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.bind(
            "<Configure>",
            lambda event: self.content_canvas.configure(
                scrollregion=self.content_canvas.bbox("all")
            ),
        )
        self.content_canvas.bind("<Configure>", self._on_content_resize)
        self.content_canvas.bind(
            "<Enter>",
            lambda event: self.content_canvas.bind_all(
                "<MouseWheel>", self._on_content_mousewheel
            ),
        )
        self.content_canvas.bind(
            "<Leave>", lambda event: self.content_canvas.unbind_all("<MouseWheel>")
        )

        header = ttk.Frame(content)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Rediger label" if standalone else "Gennemse label",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.review_count = tk.StringVar()
        if not standalone:
            # A live-batch-queue position - meaningless for a record
            # reopened after the fact
            ttk.Label(header, textvariable=self.review_count, foreground=COLOR_MUTED).grid(
                row=0, column=1, sticky="e"
            )
        ttk.Button(header, text="?  Hjælp", command=lambda: open_manual(self.window)).grid(
            row=0, column=2, sticky="e", padx=(12, 0)
        )

        # The question: a coloured box, the first thing the eye lands on
        self.question_frame = tk.Frame(
            content,
            background=self.card_background,
            highlightthickness=2,
            highlightbackground=COLOR_WARNING,
            padx=12,
            pady=10,
        )
        self.question_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.question_frame.columnconfigure(0, weight=1)
        self.question_title = tk.Label(
            self.question_frame,
            font=("Segoe UI", 13, "bold"),
            background=self.card_background,
            foreground=COLOR_WARNING,
            justify="left",
            anchor="w",
        )
        self.question_title.grid(row=0, column=0, sticky="ew")
        self.question_detail = tk.Label(
            self.question_frame,
            font=("Segoe UI", 10),
            background=self.card_background,
            foreground=self.text_color,
            justify="left",
            anchor="w",
        )
        self.question_detail.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.question_others = tk.Label(
            self.question_frame,
            font=("Segoe UI", 9),
            background=self.card_background,
            foreground=COLOR_MUTED,
            justify="left",
            anchor="w",
        )
        self.question_others.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.wrapping_labels = [self.question_title, self.question_detail, self.question_others]

        # Webshop suggestions as picture cards (see show_candidates)
        self.candidates = []
        self.candidate_cards = []
        self.selected_candidate = None
        self.blank_thumbnail = tk.PhotoImage(width=self.THUMBNAIL_SIZE, height=self.THUMBNAIL_SIZE)
        self.choices_frame = ttk.Frame(content)
        self.choices_frame.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        self.choices_frame.columnconfigure(0, weight=1)

        fields = ttk.Frame(content)
        fields.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        fields.columnconfigure(0, weight=1)

        # One free-text price/comment field rather than a separate "Pris"
        # + "Sammenligningspris" pair - excel_rows() only ever reads
        # "price", straight into the "Vejledende priser"
        # column, and format_combined_price() already folds any discount
        # into it as "1234 kr (4321 kr)".
        self.entries = {}
        self.entry_labels = {}
        self.add_entry(fields, 0, "MK Varenummer", "mk_varenummer")
        self.add_entry(fields, 1, "Varenavn", "varenavn")
        self.add_entry(fields, 2, "MK ordrenr", "mk_ordrenr")
        self.add_entry(fields, 3, "Stand", "stand")
        self.add_entry(fields, 4, "Pris / kommentar", "price")
        self.loaded_fields = {}

        # Re-runs just the webshop lookup with whatever's currently typed
        # above - for a corrected number/name, without redoing OCR.
        self.price_refresh_in_progress = False
        price_actions = ttk.Frame(content)
        price_actions.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.price_refresh_button = ttk.Button(
            price_actions, text="🔍  Søg på hjemmesiden igen", command=self.refresh_price
        )
        self.price_refresh_button.pack(side="left")
        self.price_refresh_status = ttk.Label(price_actions, text="", foreground=COLOR_MUTED)
        self.price_refresh_status.pack(side="left", padx=(8, 0))

        # Pinned footer: always visible regardless of how tall the
        # scrollable content above ends up being.
        footer = ttk.Frame(panel, padding=(0, 12, 0, 18))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)
        # A plain tk.Button, not ttk: ttk themes don't allow a custom fill
        # colour, and this has to be unmistakably *the* button to press
        tk.Button(
            footer,
            text="✓  Gem ændringer" if standalone else "✓  Godkend og næste",
            command=self.confirm,
            font=("Segoe UI", 12, "bold"),
            background=COLOR_APPROVE,
            foreground="#ffffff",
            activebackground=COLOR_APPROVE_ACTIVE,
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            cursor="hand2",
            pady=9,
        ).grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            footer,
            text="Luk" if standalone else "Spring over – ret senere",
            command=self.skip,
        ).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        ttk.Button(footer, text="Åbn hjemmeside", command=self.open_website).grid(
            row=1, column=1, sticky="ew", padx=(4, 0), pady=(8, 0)
        )

        self.load_record(record)

    def add_entry(self, parent, row, label, key):
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row * 2, column=0, sticky="w", pady=(0, 2))
        entry = ttk.Entry(parent)
        entry.grid(row=row * 2 + 1, column=0, sticky="ew", pady=(0, 9))
        self.entries[key] = entry
        self.entry_labels[key] = (label_widget, label)

    def mark_fields(self, fields):
        """Flag the entry fields the current question is about."""
        for key, (label_widget, text) in self.entry_labels.items():
            if key in fields:
                label_widget.configure(text=f"⚠  {text} – tjek", foreground=COLOR_WARNING)
            else:
                # "" = the theme's own label colour
                label_widget.configure(text=text, foreground="")

    def _on_content_resize(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)
        # Wrap long texts to the panel's width instead of a fixed guess
        for label in self.wrapping_labels:
            label.configure(wraplength=max(event.width - 40, 200))

    def _on_content_mousewheel(self, event):
        self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def show_question(self, warnings):
        question = review_question(warnings, (self.record or {}).get("parsed", {}).get("part", ""))
        if question is None:
            color = COLOR_OK
            title, detail, others, fields = "Ingen problemer fundet", "Ret felterne hvis noget er forkert.", [], []
        else:
            color = question["color"]
            title, detail = question["title"], question["detail"]
            others, fields = question["others"], question["fields"]
        self.question_frame.configure(highlightbackground=color)
        self.question_title.configure(text=title, foreground=color)
        self.question_detail.configure(text=detail)
        if others:
            self.question_others.configure(text="Også: " + " · ".join(others))
            self.question_others.grid()
        else:
            self.question_others.grid_remove()
        self.mark_fields(fields)
        return fields

    def show_candidates(self, candidates):
        """Webshop suggestions as picture cards: product photo, name, price
        and whether it was found via the number or the name. Clicking a
        card selects it (see select_candidate)."""
        for widget in self.choices_frame.winfo_children():
            widget.destroy()
        self.candidates = list(candidates)
        self.candidate_cards = []
        self.selected_candidate = None
        if not self.candidates:
            self.choices_frame.grid_remove()
            return
        self.choices_frame.grid()
        ttk.Label(
            self.choices_frame, text="Hvilken vare er det?", font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.choices_frame,
            text="Klik på den vare der passer til labelen. Klik igen for at fortryde.",
            foreground=COLOR_MUTED,
        ).grid(row=1, column=0, sticky="w", pady=(0, 6))
        is_part = bool(self.record and self.record["parsed"].get("part"))
        for index, candidate in enumerate(self.candidates):
            card = tk.Frame(
                self.choices_frame,
                background=self.card_background,
                highlightthickness=2,
                highlightbackground=self.card_border,
                cursor="hand2",
            )
            card.grid(row=index + 2, column=0, sticky="ew", pady=(0, 6))
            card.columnconfigure(1, weight=1)
            thumbnail = tk.Label(card, image=self.blank_thumbnail, background=self.card_background)
            thumbnail.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
            name = tk.Label(
                card,
                text=candidate["name"],
                font=("Segoe UI", 10, "bold"),
                background=self.card_background,
                foreground=self.text_color,
                justify="left",
                anchor="w",
                wraplength=260,
            )
            name.grid(row=0, column=1, sticky="sw", pady=(8, 0))
            price = candidate["price"] or "ingen pris"
            if is_part:
                price = "delvis vare – ingen pris"
            found_by = candidate.get("found_by")
            details = price + (f"   ·   fundet via {found_by}" if found_by else "")
            meta = tk.Label(
                card, text=details, background=self.card_background, foreground=COLOR_MUTED, anchor="w"
            )
            meta.grid(row=1, column=1, sticky="nw", pady=(0, 8))
            check = tk.Label(
                card,
                text="",
                font=("Segoe UI", 16, "bold"),
                background=self.card_background,
                foreground=COLOR_OK,
                width=2,
            )
            check.grid(row=0, column=2, rowspan=2, padx=(0, 10))
            for widget in (card, thumbnail, name, meta, check):
                widget.bind("<Button-1>", lambda event, i=index: self.select_candidate(i))
            self.candidate_cards.append({"frame": card, "thumbnail": thumbnail, "check": check})
        threading.Thread(
            target=self._fetch_thumbnails,
            args=(self.record, [candidate.get("image", "") for candidate in self.candidates]),
            daemon=True,
        ).start()

    def _fetch_thumbnails(self, record, image_urls):
        """Background thread: download the shop's product photos for the
        cards. Cached on the app, so the same product isn't fetched twice."""
        for index, url in enumerate(image_urls):
            if not url:
                continue
            image = self.app.thumbnail_cache.get(url)
            if image is None:
                try:
                    separator = "&" if "?" in url else "?"
                    response = self.app.http_session.get(
                        f"{url}{separator}width=200", headers={"User-Agent": "Mozilla/5.0"}, timeout=10
                    )
                    response.raise_for_status()
                    image = Image.open(io.BytesIO(response.content)).convert("RGB")
                    image.thumbnail((self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
                    self.app.thumbnail_cache[url] = image
                except Exception as error:
                    debug_log(f"Kunne ikke hente produktbillede {url}: {error}")
                    continue
            try:
                self.window.after(0, self._show_thumbnail, record, index, image)
            except (RuntimeError, tk.TclError):
                return  # window closed meanwhile

    def _show_thumbnail(self, record, index, image):
        if self.record is not record or index >= len(self.candidate_cards):
            return
        photo = ImageTk.PhotoImage(image)
        card = self.candidate_cards[index]
        card["thumbnail"].configure(image=photo)
        card["photo"] = photo  # Tk only shows it while a reference exists

    def select_candidate(self, index):
        """A card was clicked: fill the name and price fields from it and
        mark it chosen - or, if it was already chosen, undo that and put
        back what the label said. Nothing is written to the record until
        "Godkend" (see confirm) - skipping after picking leaves the record
        as it was. A part of an item (1/2 etc.) never gets a price, not even
        here: its comment field keeps the "OBS: delvis vare" note."""
        candidate = self.candidates[index]
        if candidate is self.selected_candidate:
            self.selected_candidate = None
            for key in ("varenavn", "price"):
                self.entries[key].delete(0, tk.END)
                self.entries[key].insert(0, self.loaded_fields.get(key, ""))
        else:
            # Checked before the name is replaced: the part marker may only
            # be in the label's name ("2/2 Vega ..."), never in the shop's
            fields = self.read_fields()
            part = item_part(fields["mk_varenummer"], fields["varenavn"]) or (
                self.record["parsed"].get("part", "") if self.record is not None else ""
            )
            self.entries["varenavn"].delete(0, tk.END)
            self.entries["varenavn"].insert(0, candidate["name"])
            self.entries["price"].delete(0, tk.END)
            if part:
                self.entries["price"].insert(0, part_note(part))
            else:
                self.entries["price"].insert(
                    0, format_combined_price(candidate["price"], candidate["compare_price"])
                )
            self.selected_candidate = candidate
        for card, shown in zip(self.candidate_cards, self.candidates):
            chosen = shown is self.selected_candidate
            card["frame"].configure(highlightbackground=COLOR_OK if chosen else self.card_border)
            card["check"].configure(text="✓" if chosen else "")

    def toggle_tools(self):
        self.tools_visible = not self.tools_visible
        if self.tools_visible:
            self.tools_frame.grid()
            self.tools_toggle.configure(text="▼ Færre værktøjer")
        else:
            self.tools_frame.grid_remove()
            self.tools_toggle.configure(text="▶ Flere værktøjer")

    def toggle_zoom(self):
        self.zoomed = not self.zoomed and self.zoom_box is not None
        self.pending_crop_fraction = None
        self.update_zoom_button()
        self.resize_image()

    def update_zoom_button(self):
        if self.zoom_box is None:
            self.zoom_button.configure(text="Vis hele billedet", state="disabled")
        else:
            self.zoom_button.configure(
                text="Vis hele billedet" if self.zoomed else "Zoom ind på labelen", state="normal"
            )

    def fields_changed(self):
        return self.record is not None and self.read_fields() != self.loaded_fields

    def refresh_price(self):
        """Re-run just the webshop lookup using whatever's currently typed
        in the fields - for fixing a typo without redoing OCR on the image."""
        if self.price_refresh_in_progress or self.record is None:
            return
        fields = self.read_fields()
        target_record = self.record
        self.price_refresh_in_progress = True
        self.price_refresh_button.configure(state="disabled", text="Søger...")
        self.price_refresh_status.configure(text="")
        threading.Thread(
            target=self._refresh_price_in_background,
            args=(target_record, fields),
            daemon=True,
        ).start()

    def _refresh_price_in_background(self, target_record, fields):
        shop_result, shop_warnings = lookup_shop_price(
            self.app.http_session,
            self.app.price_cache,
            self.app.price_cache_lock,
            fields["mk_varenummer"],
            fields["varenavn"],
            item_part(fields["mk_varenummer"], fields["varenavn"]),
        )
        self.window.after(0, self._apply_price_refresh, target_record, shop_result, shop_warnings)

    def _apply_price_refresh(self, target_record, shop_result, shop_warnings):
        self.price_refresh_in_progress = False
        self.price_refresh_button.configure(state="normal", text="🔄  Genindlæs pris")

        # Always correct the underlying record, even if the review window
        # has already moved on to a different item by the time this
        # returns - see the identical concern in retry_review_in_background.
        if shop_result["varenavn"]:
            target_record["parsed"]["varenavn"] = shop_result["varenavn"]
        target_record["parsed"]["price"] = format_combined_price(
            shop_result["price"], shop_result["compare_price"]
        )
        part = item_part(target_record["parsed"].get("mk_varenummer", ""), target_record["parsed"].get("varenavn", ""))
        part = part or target_record["parsed"].get("part", "")
        if part:
            target_record["parsed"]["price"] = part_note(part)
        target_record["parsed"]["compare_price"] = shop_result["compare_price"]
        target_record["parsed"]["url"] = shop_result["url"]
        target_record["candidates"] = shop_result["candidates"]
        target_record["warnings"] = [
            w for w in target_record["warnings"] if w[0] not in SHOP_WARNING_CATEGORIES
        ] + shop_warnings

        if self.record is not target_record:
            return

        for key in ("varenavn", "price"):
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, target_record["parsed"].get(key, ""))
            # Now the record's own values - not unsaved edits
            self.loaded_fields[key] = self.entries[key].get().strip()
        self.show_question(target_record["warnings"])
        self.show_candidates(target_record["candidates"])
        if shop_warnings:
            self.price_refresh_status.configure(
                text="Intet præcist match - vælg en vare herover eller ret felterne.",
                foreground=COLOR_WARNING,
            )
        else:
            self.price_refresh_status.configure(text="Pris opdateret.", foreground=COLOR_OK)

    def load_record(self, record):
        """Populate the (already-built) window with a new record in place,
        instead of destroying/recreating the window for each review item."""
        self.record = record
        self.crop_drag_start = None
        self.pending_crop_fraction = None
        self.rotation_angle = 0.0
        self.rotation_slider.set(0)
        self.rotation_label.configure(text="0°")
        self.zoom_box = record["parsed"].get("label_box")
        self.zoomed = self.zoom_box is not None
        self.update_zoom_button()

        # Reset any in-flight-refresh visuals from whatever record was open
        # before - a late result for that old record is a no-op anyway
        # (see _apply_price_refresh's identity check), but the button/status
        # itself needs to reflect *this* record's idle state regardless.
        self.price_refresh_in_progress = False
        self.price_refresh_button.configure(state="normal", text="🔍  Søg på hjemmesiden igen")
        self.price_refresh_status.configure(text="")

        self.raw_text_widget.configure(state="normal")
        self.raw_text_widget.delete("1.0", tk.END)
        self.raw_text_widget.insert("1.0", record.get("raw_text", ""))
        self.raw_text_widget.configure(state="disabled")

        for key, entry in self.entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, record["parsed"].get(key, ""))
        self.loaded_fields = self.read_fields()

        attention = self.show_question(record["warnings"])
        self.show_candidates(record.get("candidates", []))

        self.window.deiconify()
        self.window.lift()
        self.content_canvas.yview_moveto(0)
        if attention:
            entry = self.entries[attention[0]]
            entry.focus_set()
            entry.icursor(tk.END)

        self.show_image()
        self.update_review_count()

    def hide(self):
        self.record = None
        self.window.withdraw()

    def close_window(self):
        """✕ on the batch review: hide, but keep the current item and any
        typed edits, to continue later via "Gennemse labels"."""
        self.window.withdraw()

    def show(self):
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def open_website(self):
        # `url` is set as soon as a product was found at all - matched
        # exactly or not - so this only falls back to a text search when
        # nothing was found on the shop, and does so using the varenavn
        # (the OCR'd/human-corrected name), not the MK-varenummer.
        parsed = self.record["parsed"]
        url = (self.selected_candidate or {}).get("url") or parsed.get("url", "")
        if not url:
            search_value = (
                parsed.get("varenavn")
                or parsed.get("mk_varenummer")
                or parsed.get("mk_ordrenr")
                or ""
            )
            url = f"{SHOP_URL}/search?q={quote(search_value)}" if search_value else SHOP_URL
        webbrowser.open(url)

    def update_review_count(self):
        if self.standalone:
            return
        current = min(self.app.review_completed + 1, self.app.review_total)
        self.review_count.set(f"Gennemgang: {current}/{self.app.review_total}")

    def show_image(self):
        try:
            with Image.open(self.record["path"]) as opened:
                # Phone photos (HEIC/JPEG) commonly carry an EXIF
                # orientation tag rather than storing pixels upright -
                # without correcting for it here, the review window can
                # show (and crop/rotate) the image sideways relative to
                # how the photo actually looks.
                corrected = ImageOps.exif_transpose(opened)
                self.original_image = corrected.convert("RGB")
            # A sideways label was read turned upright (see extract_label) -
            # show it that way, which is also what label_box refers to
            turn = self.record["parsed"].get("photo_turn", 0)
            if turn:
                self.original_image = self.original_image.rotate(turn, expand=True)
            self.working_image = self.record.get("working_image")
            self.resize_image()
        except Exception as error:
            self.image_canvas.delete("all")
            self.image_canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Kunne ikke vise billede:\n{error}",
                fill="white",
            )

    def resize_image(self, event=None):
        if self.original_image is None:
            # <Configure> fires as soon as the canvas is created/resized -
            # including before show_image() has loaded anything into
            # original_image (e.g. the initial layout pass right after this
            # window opens). Nothing to draw yet.
            return
        try:
            width = max(self.image_frame.winfo_width() - 24, 100)
            height = max(self.image_frame.winfo_height() - 24, 100)
            image = self.display_base().copy()
            if self.rotation_angle:
                # Shrink the thumbnail target first so the live-rotated
                # preview (whose bounding box grows once it's off a right
                # angle) still comfortably fits the canvas. This is only
                # ever a preview - see build_edited_image for the one place
                # the full-resolution image actually gets rotated.
                margin = 0.75
                image.thumbnail((int(width * margin), int(height * margin)), Image.Resampling.LANCZOS)
                image = image.rotate(
                    -self.rotation_angle,
                    expand=True,
                    resample=Image.BICUBIC,
                    fillcolor=self.canvas_background,
                )
            else:
                image.thumbnail((width, height), Image.Resampling.LANCZOS)
            self.preview = ImageTk.PhotoImage(image)
            canvas_width = self.image_canvas.winfo_width()
            canvas_height = self.image_canvas.winfo_height()
            left = max((canvas_width - image.width) // 2, 0)
            top = max((canvas_height - image.height) // 2, 0)
            self.image_bounds = (left, top, image.width, image.height)
            self.image_canvas.delete("all")
            self.image_canvas.create_image(left, top, image=self.preview, anchor="nw")
            if self.pending_crop_fraction:
                self.show_committed_crop_overlay()
        except Exception as error:
            # A redraw failing here used to fail *silently* (Tk swallows
            # exceptions raised inside widget callbacks), which made
            # rotation look like it simply did nothing and left every
            # later button press hitting the same broken redraw. Now it's
            # visible instead, on the canvas and in the status bar.
            debug_log(f"resize_image fejlede: {error!r}")
            self.image_canvas.delete("all")
            self.image_canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Kunne ikke opdatere billedvisningen:\n{error}",
                fill="white",
            )

    def display_base(self):
        """The image as shown before rotation: the photo (or its edited
        copy), cut down to the label area while zoomed in."""
        image = self.working_image or self.original_image
        if self.zoomed and self.zoom_box:
            width, height = image.size
            left, top, right, bottom = self.zoom_box
            image = image.crop((
                round(left * width), round(top * height), round(right * width), round(bottom * height)
            ))
        return image

    def read_fields(self):
        values = {}
        for key, widget in self.entries.items():
            if isinstance(widget, tk.Text):
                values[key] = widget.get("1.0", tk.END).strip()
            else:
                values[key] = widget.get().strip()
        return values

    def confirm(self):
        """Save the fields (and a picked suggestion's link) and move on.
        Asks first when the answer still looks unfinished."""
        if self.record is None:
            return
        fields = self.read_fields()
        fields["stand"] = fields["stand"].upper()
        if fields["stand"] not in VALID_STANDS and not messagebox.askyesno(
            "Tjek STAND",
            (f"STAND står som \"{fields['stand']}\"." if fields["stand"] else "Der står ingen STAND.")
            + " Den skal normalt være A, B eller C.\n\nGodkend alligevel?",
            parent=self.window,
        ):
            return
        is_part = bool(item_part(fields["mk_varenummer"], fields["varenavn"]) or self.record["parsed"].get("part"))
        if (
            self.candidates
            and self.selected_candidate is None
            and not fields["price"]
            and not is_part
            and not messagebox.askyesno(
                "Ingen vare valgt",
                "Du har ikke valgt en vare fra hjemmesiden, så der er ingen pris.\n\nGodkend alligevel?",
                parent=self.window,
            )
        ):
            return
        self.record["parsed"].update(fields)
        if self.selected_candidate is not None:
            self.record["parsed"]["url"] = self.selected_candidate["url"]
        self.record["candidates"] = []
        self.record["warnings"] = []
        record = self.record
        self.record = None
        if self.standalone:
            self.app.upsert_result_row(
                record["path"], record["parsed"], record["warnings"], record.get("raw_text", "")
            )
            # A standalone edit doesn't go through review_finished, which
            # is what would otherwise trigger a re-export via
            # maybe_enable_export_controls - so if a file's already been
            # saved once, refresh it here under the same name rather than
            # leaving the correction out of it.
            if self.app.exported:
                self.app.auto_export_excel()
            self.app.open_standalone_windows.pop(id(record), None)
            self.window.destroy()
            return
        self.app.review_finished(record, record)

    def ensure_working_image(self):
        if self.working_image is None:
            self.working_image = self.original_image.copy()
        self.record["working_image"] = self.working_image

    def mirror(self):
        self.ensure_working_image()
        self.working_image = self.working_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        self.record["working_image"] = self.working_image
        self.pending_crop_fraction = None
        # The label area no longer matches the mirrored photo
        self.zoom_box = None
        self.zoomed = False
        self.update_zoom_button()
        self.resize_image()

    def build_edited_image(self):
        """The image actually sent for re-OCR: base image, rotated to the
        slider's angle and cropped to any pending selection. This is the
        only place a real full-resolution rotate happens - everything
        interactive (dragging the slider, dragging a crop box) works off a
        cheap thumbnail and scale-independent fractions instead."""
        # A crop drawn while zoomed in is relative to the zoomed view; with
        # no crop, the whole photo is re-read (the zoom is only for viewing)
        image = self.display_base() if self.pending_crop_fraction else (self.working_image or self.original_image)
        if self.rotation_angle:
            image = image.rotate(
                -self.rotation_angle,
                expand=True,
                resample=Image.BICUBIC,
                fillcolor=self.canvas_background,
            )
        if self.pending_crop_fraction:
            width, height = image.size
            fx1, fy1, fx2, fy2 = self.pending_crop_fraction
            image = image.crop((
                round(fx1 * width),
                round(fy1 * height),
                round(fx2 * width),
                round(fy2 * height),
            ))
        return image

    def _on_rotation_slider(self, value):
        self.rotation_angle = float(value)
        self.pending_crop_fraction = None
        self.rotation_label.configure(text=f"{round(self.rotation_angle)}°")
        self.resize_image()

    def rotate_quarter_turn(self, delta):
        """Nudge the rotation by a quick +/-90 degrees, wrapping into the
        slider's -180..180 range. Routed through the slider's own .set()
        so it stays perfectly in sync with the slider position and reuses
        the exact same update path (_on_rotation_slider) as a manual drag."""
        new_angle = ((self.rotation_angle + delta + 180) % 360) - 180
        self.rotation_slider.set(new_angle)

    def crop_start_event(self, event):
        if not self.image_bounds:
            return
        self.crop_drag_start = (event.x, event.y)
        self.show_crop_overlay(event.x, event.y, event.x, event.y)

    def crop_drag_event(self, event):
        if self.crop_drag_start:
            self.show_crop_overlay(
                self.crop_drag_start[0],
                self.crop_drag_start[1],
                event.x,
                event.y,
            )

    def crop_release_event(self, event):
        if not self.crop_drag_start or not self.image_bounds:
            self.crop_drag_start = None
            return
        start_x, start_y = self.crop_drag_start
        end_x, end_y = event.x, event.y
        self.crop_drag_start = None
        left, top, display_width, display_height = self.image_bounds
        x1 = max(min(start_x, end_x), left)
        y1 = max(min(start_y, end_y), top)
        x2 = min(max(start_x, end_x), left + display_width)
        y2 = min(max(start_y, end_y), top + display_height)
        if x2 - x1 > 5 and y2 - y1 > 5:
            # Stored as fractions (0-1) of the currently displayed image
            # rather than absolute pixels: a fraction of the rotated
            # bounding box means exactly the same region whether it's
            # measured on the small preview or the full-resolution photo,
            # so there's no scale-factor math (and no chance of it being
            # wrong) between drawing the crop and actually applying it in
            # build_edited_image.
            self.pending_crop_fraction = (
                (x1 - left) / display_width,
                (y1 - top) / display_height,
                (x2 - left) / display_width,
                (y2 - top) / display_height,
            )
        self.resize_image()

    def show_committed_crop_overlay(self):
        left, top, display_width, display_height = self.image_bounds
        fx1, fy1, fx2, fy2 = self.pending_crop_fraction
        self.show_crop_overlay(
            left + fx1 * display_width,
            top + fy1 * display_height,
            left + fx2 * display_width,
            top + fy2 * display_height,
        )

    def show_crop_overlay(self, x1=None, y1=None, x2=None, y2=None):
        if not self.image_bounds:
            return
        left, top, width, height = self.image_bounds
        if x1 is None:
            x1, y1, x2, y2 = left, top, left + width, top + height
        x1, x2 = sorted((max(left, min(x1, left + width)), max(left, min(x2, left + width))))
        y1, y2 = sorted((max(top, min(y1, top + height)), max(top, min(y2, top + height))))
        self.image_canvas.delete("crop-overlay")
        self.image_canvas.create_rectangle(left, top, left + width, y1, fill="#000000", stipple="gray50", outline="", tags="crop-overlay")
        self.image_canvas.create_rectangle(left, y2, left + width, top + height, fill="#000000", stipple="gray50", outline="", tags="crop-overlay")
        self.image_canvas.create_rectangle(left, y1, x1, y2, fill="#000000", stipple="gray50", outline="", tags="crop-overlay")
        self.image_canvas.create_rectangle(x2, y1, left + width, y2, fill="#000000", stipple="gray50", outline="", tags="crop-overlay")
        self.image_canvas.create_rectangle(x1, y1, x2, y2, outline="#ffffff", width=2, tags="crop-overlay")

    def reset_image(self):
        self.working_image = None
        self.record.pop("working_image", None)
        self.pending_crop_fraction = None
        self.zoom_box = self.record["parsed"].get("label_box")
        self.zoomed = self.zoom_box is not None
        self.update_zoom_button()
        self.rotation_angle = 0.0
        self.rotation_slider.set(0)
        self.rotation_label.configure(text="0°")
        self.resize_image()

    def retry(self):
        if self.record is None:
            return
        if self.fields_changed() and not messagebox.askokcancel(
            "Læs billedet igen?",
            "Dine rettelser i felterne bliver overskrevet, når billedet læses igen.",
            parent=self.window,
        ):
            return
        record = self.record
        working_image = self.build_edited_image().copy()
        self.record = None
        self.pending_crop_fraction = None
        standalone = self.standalone
        if standalone:
            # Fire-and-forget, same as the live-queue path below: OCR
            # keeps running in the background and the row updates in
            # place once it's done (see retry_review/show_retry_result) -
            # there's no "next item" to move on to here, so just close.
            self.window.destroy()
        self.app.retry_review(record, working_image, standalone=standalone)

    def skip(self):
        """Move on without saving - but never silently throw away edits:
        if any field was changed, ask whether to keep them."""
        if self.fields_changed():
            answer = messagebox.askyesnocancel(
                "Gem ændringerne?",
                "Du har rettet i felterne. Vil du gemme ændringerne?",
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                self.confirm()
                return
        record = self.record
        self.record = None
        if self.standalone:
            self.app.open_standalone_windows.pop(id(record), None)
            self.window.destroy()
            return
        self.app.review_finished(record, None)

@dataclass
class TextBlock:
    """One block of OCR'd text (a line or a paragraph) and where it sits in
    the photo, in pixels from the top-left corner."""

    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def center_x(self):
        return (self.left + self.right) / 2

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2


def load_label_photo(image_path):
    """The photo as upright RGB, no larger than MAX_IMAGE_SIDE.

    Phone photos (HEIC and plain JPEG alike) very commonly store pixels
    "sideways" plus an EXIF Orientation tag rather than upright pixels -
    ReviewWindow.show_image() corrects for this before *displaying* a
    photo (via the identical ImageOps.exif_transpose() call), and Docling
    opens images with plain PIL.Image.open(...).convert("RGB") and never
    looks at EXIF, so it has to happen here before OCR as well - otherwise
    a rotated-by-EXIF label gets OCR'd sideways or upside down while
    looking perfectly upright in the review window."""
    with Image.open(image_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return image


def save_temporary_png(image, temporary_paths):
    temporary_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temporary_path = Path(temporary_file.name)
    temporary_file.close()
    temporary_paths.append(temporary_path)
    image.save(temporary_path, format="PNG")
    return temporary_path


def read_text_blocks(converter, image_path):
    """OCR one image with Docling, as TextBlocks in Docling's own order.

    One Docling text item can be several pieces of text from different
    places in the photo, merged: it then has one `prov` entry per piece,
    each with its own box and a `charspan` saying which characters of the
    item's text are in it. Each piece becomes its own TextBlock here.
    (Using only the first box for the whole item once put the entire label
    of IMG_0277 at the position of a 2-letter scrap of packaging text: the
    zoom showed that scrap, and the packaging text was read as part of the
    label.)"""
    document = converter.convert(str(image_path)).document
    page = next(iter(document.pages.values()), None)
    blocks = []
    for item in document.texts:
        if not item.text or not item.prov:
            continue
        pieces = [(item.text, item.prov[0].bbox)]
        if len(item.prov) > 1:
            pieces = [
                (item.text[prov.charspan[0]:prov.charspan[1]], prov.bbox)
                for prov in item.prov
            ]
        for piece_text, box in pieces:
            text = WHITESPACE_RE.sub(" ", piece_text).strip()
            if not text:
                continue
            if page is not None:
                box = box.to_top_left_origin(page.size.height)
            blocks.append(TextBlock(text, box.l, box.t, box.r, box.b))
    return blocks


def estimated_line_height(block, along_span):
    """Height of one line of text in `block` (in the direction lines
    advance). A block can hold several lines - IMG_0277's whole label is a
    single block - so its full height is often far more than a line.
    Estimated from how much area its characters fill: with this font a
    character is about half a line high wide, and lines fill roughly 70 %
    of the block's width, so area ≈ chars × 0.55 h × h / 0.7."""
    low, high = along_span(block)
    extent = high - low
    area_estimate = (1.27 * block.width * block.height / max(len(block.text), 1)) ** 0.5
    return min(extent, area_estimate)


def label_blocks_in_reading_order(blocks):
    """(label_blocks, turn): only the text blocks that belong to the
    label, in the order the label's lines are meant to be read - and, for
    a label photographed sideways, the rotation (degrees, PIL's
    counter-clockwise convention) that turns the photo upright, else 0.

    Photos often contain other text next to the label - a second sticker,
    a shipping label, printing on the box, handwriting - which used to end
    up inside the parsed fields. And Docling's own block order sometimes
    puts "OUTLET" before the product name, which breaks parsing too.

    Labels are printed with every line centred, so the label is found by
    starting from the block(s) containing OUTLET/STAND and growing outward
    to blocks that are (a) close to the label along the direction its lines
    advance and (b) centred on the same axis. A sticker right next to the
    label fails (b); text further down the box fails (a).

    A label photographed sideways is read by the OCR line by line just
    fine - only the order comes out wrong - so the same logic runs with the
    axes swapped instead of re-reading a rotated copy of the photo. The
    direction (which end is the first line) follows from STAND coming after
    OUTLET, or OUTLET coming after the item number.

    Ordering is only as good as the blocks, though: Docling sometimes
    merges several sideways lines into one block with the lines in the
    wrong order inside it (IMG_0285), which no reordering can fix. So
    extract_label re-reads a sideways photo turned upright (see `turn`).

    Falls back to all blocks in Docling's order when there is no OUTLET or
    STAND to anchor on."""
    anchors = [block for block in blocks if LABEL_ANCHOR_RE.search(block.text)]
    if not anchors:
        return blocks, 0
    # Orientation is judged only from short, single-line anchors ("OUTLET",
    # "STAND: B"), which are much taller than wide when sideways. A block
    # holding several lines can be any shape (IMG_9168's whole upright
    # label is one block, slightly taller than wide) - with no short
    # anchor the label is taken to be upright, the normal case.
    single_lines = [block for block in anchors if len(block.text) <= 15]
    sideways = bool(single_lines) and (
        sum(1 for block in single_lines if block.height > block.width) * 2 > len(single_lines)
    )

    def along(block):  # position in the direction the label's lines advance
        return block.center_x if sideways else block.center_y

    def along_span(block):
        return (block.left, block.right) if sideways else (block.top, block.bottom)

    def across(block):  # position along a line
        return block.center_y if sideways else block.center_x

    def across_size(block):
        return block.height if sideways else block.width

    axis = sum(across(block) for block in anchors) / len(anchors)
    line_size = min(estimated_line_height(block, along_span) for block in anchors)
    label = list(anchors)
    added = True
    while added:
        added = False
        start = min(along_span(block)[0] for block in label)
        end = max(along_span(block)[1] for block in label)
        label_width = max(across_size(block) for block in label)
        for block in blocks:
            if block in label:
                continue
            low, high = along_span(block)
            gap = max(low - end, start - high, 0)
            off_axis = abs(across(block) - axis)
            if gap <= 2 * line_size and off_axis <= 0.2 * max(label_width, across_size(block)):
                label.append(block)
                added = True

    outlet = next((block for block in label if OUTLET_RE.search(block.text)), None)
    stand = next((block for block in label if STAND_RE.search(block.text)), None)
    number = next(
        (block for block in label if LABEL_NUMBER_RE.search(block.text) and block not in (outlet, stand)),
        None,
    )
    if outlet is not None and stand is not None and outlet is not stand:
        direction = 1 if along(stand) > along(outlet) else -1
    elif number is not None:
        direction = 1 if along(outlet or stand) > along(number) else -1
    else:
        direction = -1 if sideways else 1

    def reading_position(block):
        low, high = along_span(block)
        return low if direction == 1 else -high

    # Sideways text whose lines advance to the left was turned clockwise -
    # turn it back counter-clockwise (+90), and the other way round
    turn = (90 if direction == -1 else -90) if sideways else 0
    return sorted(label, key=reading_position), turn


def label_area(label_blocks, image_size):
    """The label's area as fractions (left, top, right, bottom) of the
    photo, with a margin around its text - the review window zooms in on
    this. Fractions, so they apply to the full-resolution photo too."""
    width, height = image_size
    left = min(block.left for block in label_blocks)
    top = min(block.top for block in label_blocks)
    right = max(block.right for block in label_blocks)
    bottom = max(block.bottom for block in label_blocks)
    margin = 0.35 * max(right - left, bottom - top)
    return (
        max(0.0, (left - margin) / width),
        max(0.0, (top - margin) / height),
        min(1.0, (right + margin) / width),
        min(1.0, (bottom + margin) / height),
    )


def clean_varenavn(varenavn):
    varenavn = varenavn.replace("×", "x")
    varenavn = PIPE_RE.sub(" | ", varenavn)
    varenavn = DIAMETER_DIGITS_RE.sub(r"Ø\1", varenavn)
    return WHITESPACE_RE.sub(" ", varenavn).strip(" ,|-")


def part_note(part):
    """Put in the price/comment field of a part of an item instead of a price, so
    the export still says so even if the "1/2" disappears from the name
    (e.g. when the user picks the shop's name for it)."""
    return f"OBS: delvis vare (del {part})"


def item_part(mk_varenummer, varenavn):
    """"1/2" when the label marks the item as only part of it - in the
    number ("101338_1/2") or at the start of the name ("1/2 Vega ...")."""
    match = PART_SUFFIX_RE.search((mk_varenummer or "").strip()) or PART_PREFIX_RE.search(
        (varenavn or "").strip()
    )
    return match["part"] if match else ""


def parse_label_fields(text):
    """Split a label's text (lines in reading order, see
    label_blocks_in_reading_order) into its fields:

        <number>, <name> OUTLET <order number> STAND: <A/B/C>

    Returns (fields, keywords_missing) - keywords_missing is True when
    OUTLET or STAND wasn't found, so the split is less certain. Fields
    that couldn't be read are left empty. The raw STAND value is kept even
    if it isn't A/B/C (e.g. "AP"), so the user sees what the label says."""
    parsed_label = {
        "mk_varenummer": "",
        "varenavn": "",
        "mk_ordrenr": "",
        "stand": "",
        "part": "",
    }
    outlet = OUTLET_RE.search(text)
    stand = STAND_RE.search(text)
    header_end = min(
        [match.start() for match in (outlet, stand) if match is not None] or [len(text)]
    )
    header = text[:header_end].strip()

    number = LABEL_NUMBER_RE.search(header)
    if number:
        parsed_label["mk_varenummer"] = WHITESPACE_RE.sub(" ", number["number"])
        # Anything before the number is either stray text or (rarely) a
        # line read out of order - the name normally follows the number
        after = header[number.end():].strip()
        name = after or header[:number.start()]
    else:
        name = header
    parsed_label["varenavn"] = clean_varenavn(name)

    if outlet is not None:
        order_end = stand.start() if stand is not None and stand.start() > outlet.end() else len(text)
        parsed_label["mk_ordrenr"] = DIGIT_GROUP_DOT_RE.sub("", text[outlet.end():order_end].strip(" ,:"))
    if stand is not None and stand["stand"]:
        parsed_label["stand"] = stand["stand"].upper()

    parsed_label["part"] = item_part(parsed_label["mk_varenummer"], parsed_label["varenavn"])
    return parsed_label, outlet is None or stand is None


def label_warnings(parsed_label, keywords_missing):
    """Warnings about the label text itself (the webshop lookup adds its own)."""
    warnings = [
        ("missing", f"Kunne ikke læse {label}")
        for key, label in MISSING_FIELD_NAMES.items()
        if not parsed_label[key]
    ]
    if keywords_missing:
        warnings.append(("partial", "Fandt ikke både OUTLET og STAND på labelen"))
    if parsed_label["stand"] and parsed_label["stand"] not in VALID_STANDS:
        warnings.append(("suspicious", f"Usædvanlig STAND på labelen: \"{parsed_label['stand']}\""))
    if parsed_label["mk_ordrenr"] and len(parsed_label["mk_ordrenr"].split()) > 4:
        warnings.append(("suspicious", "Mistænkelig tekst før MK-ordrenummer"))
    if parsed_label["part"]:
        warnings.append((
            "part",
            f"Delvis vare (del {parsed_label['part']}) - der sættes ingen pris. "
            "Tjek at labelen er læst rigtigt.",
        ))
    return warnings


def extract_label(converter, image_path, session, cache, cache_lock):
    """Called from up to OCR_WORKERS worker threads at once, all sharing
    one DocumentConverter instance (see process_in_background) - verified
    safe by running it concurrently against a set of real label photos and
    diffing the extracted text against a single-threaded run (identical
    output, no exceptions, real wall-clock speedup - consistent with the
    torch/RapidOCR backend releasing the GIL during inference)."""
    temporary_paths = []
    try:
        image = load_label_photo(image_path)
        blocks = read_text_blocks(converter, save_temporary_png(image, temporary_paths))
        label_blocks, turn = label_blocks_in_reading_order(blocks)
        if turn:
            # A sideways label: read it again upright. Costs one extra OCR,
            # only for these photos (typically 1-2 in 30) - see
            # label_blocks_in_reading_order for why ordering alone isn't enough.
            upright = image.rotate(turn, expand=True)
            upright_blocks = read_text_blocks(converter, save_temporary_png(upright, temporary_paths))
            upright_label, _ = label_blocks_in_reading_order(upright_blocks)
            if upright_label is not upright_blocks:  # found OUTLET/STAND
                debug_log(f"{image_path.name}: sideways label, re-read turned {turn} degrees")
                image, blocks, label_blocks = upright, upright_blocks, upright_label
            else:
                turn = 0
        ignored = [block.text for block in blocks if block not in label_blocks]
        if ignored:
            debug_log(f"{image_path.name}: ignored text outside the label: {ignored}")
        text = " ".join(block.text for block in label_blocks)

        parsed_label, keywords_missing = parse_label_fields(text)
        # The review window shows the photo turned the same way, so the
        # label is upright there too (and label_box matches it)
        parsed_label["photo_turn"] = turn
        if label_blocks is not blocks and label_blocks:
            parsed_label["label_box"] = label_area(label_blocks, image.size)
        warnings = label_warnings(parsed_label, keywords_missing)

        shop_result, shop_warnings = lookup_shop_price(
            session,
            cache,
            cache_lock,
            parsed_label["mk_varenummer"],
            parsed_label["varenavn"],
            parsed_label["part"],
        )
        warnings.extend(shop_warnings)
        if shop_result["varenavn"]:
            # A confident shop match wins over the raw OCR text - it's the
            # shop's own canonical name, and it's what both the review
            # window and the export end up showing.
            parsed_label["varenavn"] = shop_result["varenavn"]
        parsed_label["price"] = format_combined_price(shop_result["price"], shop_result["compare_price"])
        if parsed_label["part"]:
            parsed_label["price"] = part_note(parsed_label["part"])
        parsed_label["compare_price"] = shop_result["compare_price"]
        parsed_label["url"] = shop_result["url"]
        return text, parsed_label, warnings, shop_result["candidates"]
    finally:
        for temporary_path in temporary_paths:
            try:
                os.unlink(temporary_path)
            except OSError as error:
                debug_log(f"Advarsel: kunne ikke slette midlertidig fil {temporary_path}: {error}")


def main():
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    install_error_reporting(root)
    theme_applied = apply_theme(root)
    app = DoclingImageMiner(root, theme_applied=theme_applied)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()