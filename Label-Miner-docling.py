from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
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

from PIL import ExifTags, Image, ImageOps, ImageTk
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
NON_WORD_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
PIPE_RE = re.compile(r"\s*\|\s*")
DIAMETER_DIGITS_RE = re.compile(r"(?<!\d)0(\d{3})(?!\d)")
IMAGE_COMMENT_RE = re.compile(r"<!--\s*image\s*-->", flags=re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*#+\s*", flags=re.MULTILINE)

# Strips the timestamp/part suffix Google Drive appends when it zips a
# folder for download, e.g. "9-20260917T102218Z-1-001" -> "9". Only used
# as a fallback name when a zip's images sit directly at its root instead
# of inside a named subfolder (see derive_source_folder).
GOOGLE_DRIVE_ZIP_SUFFIX_RE = re.compile(r"-\d{8}T\d{6}Z-\d+-\d+$")

STRICT_LABEL_RE = re.compile(
    r"(?P<mk_varenummer>[^,\-]+?)\s*[-,]\s*"
    r"(?P<varenavn>.+?)\s+OUTLET\s+"
    r"(?P<mk_ordrenr>.+?)\s+STAND\s*:?\s*"
    r"(?P<stand>[ABC])",
    flags=re.IGNORECASE,
)
VARENAVN_PAIR_RE = re.compile(
    r"(?P<mk_varenummer>[^,\-]+?)\s*[-,]\s*(?P<varenavn>.+?)\s+OUTLET\b",
    flags=re.IGNORECASE,
)
MK_ORDRENR_STAND_RE = re.compile(
    r"OUTLET\s+(?P<mk_ordrenr>.+?)\s+STAND\s*:?\s*(?P<stand>[ABC])\b",
    flags=re.IGNORECASE,
)
STAND_ONLY_RE = re.compile(r"STAND\s*:?\s*(?P<stand>[ABC])\b", flags=re.IGNORECASE)


def warning_messages(warnings):
    """Flatten (category, message) warning tuples down to just the messages."""
    return [message for _, message in warnings]


WARNING_CATEGORY_ORDER = ["missing", "suspicious", "partial", "error", "shop"]
WARNING_CATEGORY_LABELS = {
    "missing": "Manglende felter",
    "suspicious": "Mistænkelige værdier",
    "partial": "Delvis genkendelse",
    "error": "Fejl",
    "shop": "Webshop",
}

# Shared severity colors, used by both the warnings list in the review
# window and the status Treeview on the main window, so the same problem
# reads the same way everywhere in the app.
COLOR_OK = "#3fb950"
COLOR_WARNING = "#d29922"
COLOR_ERROR = "#f85149"
COLOR_MUTED = "#9aa0a6"
WARNING_CATEGORY_COLORS = {
    "missing": COLOR_WARNING,
    "suspicious": COLOR_WARNING,
    "partial": COLOR_WARNING,
    "error": COLOR_ERROR,
    "shop": COLOR_WARNING,
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


def normalize_name_for_match(name):
    if not name:
        return ""

    # Treat × and x as the same
    name = name.replace("×", "x")

    # Remove "Default Title"
    name = DEFAULT_TITLE_RE.sub("", name)

    # Remove special characters
    name = NON_WORD_RE.sub("", name)

    # Normalize whitespace
    name = WHITESPACE_RE.sub(" ", name)

    return name.strip().casefold()


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


def _fetch_product_data(session, cache, cache_lock, search_values):
    """Fetch (and cache) the raw product lookup for a set of search values.

    Cached by the exact tuple of search values, since a repeated
    MK-varenummer/MK-ordrenummer pair (e.g. multiple photos of the same
    item) should not trigger new network requests. Only successful
    fetches are cached, so a transient failure doesn't get "stuck" for
    later images.

    cache_lock guards the dict since multiple images can be looked up
    concurrently (see OCR_WORKERS) - without it two threads could both
    miss the cache for the same key and fetch the same product twice.
    Harmless correctness-wise (dict writes are atomic), just wasted
    requests, but cheap to avoid.
    """
    cache_key = tuple(search_values)
    with cache_lock:
        cached = cache.get(cache_key)
    if cached is not None:
        return cached, None

    products = []
    last_error = None
    for search_value in search_values:
        try:
            response = session.get(
                f"{SHOP_URL}/search/suggest.json",
                params={
                    "q": search_value,
                    "resources[type]": "product",
                    "resources[limit]": 1,
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            products = response.json().get("resources", {}).get("results", {}).get("products", [])
        except Exception as error:
            last_error = error
            continue
        if products:
            break

    if len(products) != 1:
        if last_error and not products:
            return None, f"Prissøgning fejlede: {last_error}"
        return None, "Fandt ikke produkt på hjemmesiden"

    product = products[0]
    handle = product.get("handle", "")
    if not handle:
        return None, "Produktet mangler en hjemmesideadresse"

    try:
        product_response = session.get(
            f"{SHOP_URL}/products/{handle}.js",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        product_response.raise_for_status()
        product_data = product_response.json()
    except Exception as error:
        return None, f"Kunne ikke hente produktdata: {error}"

    product_title = product_data.get("title", product.get("title", "")).strip()
    product_url = f"{SHOP_URL}/products/{handle}"
    fetched = (product_title, product_data, product_url)
    with cache_lock:
        cache[cache_key] = fetched
    return fetched, None


def build_variant_candidates(product_title, product_data):
    """Flatten one fetched product into a list of {name, price,
    compare_price, id} candidates - one per variant, or a single
    id-less entry for a variant-less product. Used both for automatic
    exact matching and for the review window's "pick the right one"
    suggestion buttons, so the two always agree on what a "candidate"
    looks like. `id` is a variant id (or None) - see
    build_variant_url, which turns a matched candidate into a link that
    deep-links straight to that specific variant on the shop page."""
    variants = product_data.get("variants", [])
    if not variants:
        return [{
            "name": product_title,
            "price": format_shop_price(product_data.get("price")),
            "compare_price": format_shop_price(product_data.get("compare_at_price")),
            "id": None,
        }]

    candidates = []
    for variant in variants:
        variant_title = (variant.get("title") or "").strip()
        if not variant_title or variant_title.casefold() == "default title":
            name = product_title
        else:
            name = f"{product_title} {variant_title}"
        candidates.append({
            "name": name,
            "price": format_shop_price(variant.get("price")),
            "compare_price": format_shop_price(variant.get("compare_at_price")),
            "id": variant.get("id"),
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


def rank_candidates_by_similarity(candidates, ocr_varenavn, limit=5):
    """Rank candidates by text similarity to the OCR'd name, capped to
    `limit` - so a product with a dozen colour/size variants doesn't flood
    the review window with buttons."""
    target = normalize_name_for_match(ocr_varenavn)
    ranked = sorted(
        candidates,
        key=lambda candidate: difflib.SequenceMatcher(
            None, normalize_name_for_match(candidate["name"]), target
        ).ratio(),
        reverse=True,
    )
    return ranked[:limit]


def estimate_default_price(candidates):
    """When no exact variant match was found, suggest the most common price
    among *this specific product's own* variants as a rough starting
    point - free (no extra network calls, just counting what was already
    fetched) but never guessed across products, only within the one the
    MK-varenummer/MK-ordrenummer search already identified. Returns None
    if none of the candidates have a known price."""
    priced = [candidate["price"] for candidate in candidates if candidate["price"]]
    if not priced:
        return None
    price, _count = Counter(priced).most_common(1)[0]
    return price


def format_combined_price(current_price, compare_price):
    if current_price and compare_price and current_price != compare_price:
        return f"{current_price} ({compare_price})"
    return current_price


def format_default_price_note(default_price):
    """Marks a guessed price as unmistakably unconfirmed - this string is
    what ends up both in the review window's Pris field and, if approved
    as-is, in the exported comments column, so it must stay self-explanatory
    on its own without the rest of the app's context."""
    return f"~{default_price} (vejledende, ukendt variant)"


def lookup_shop_price(session, cache, cache_lock, mk_varenummer, mk_ordrenr, ocr_varenavn):
    """Look up a label's price on the webshop. Only ever fills in
    varenavn/price/compare_price when exactly one candidate's name matches
    the OCR'd text - anything else (no product found, or an ambiguous
    match) instead returns ranked `candidates` for a human to pick from,
    rather than silently attaching a price that might belong to the wrong
    product or variant.

    Warning messages here deliberately stay short - the OCR'd varenavn is
    already visible in the review window's own Varenavn field, the raw OCR
    text has its own (collapsible) panel, and any website candidates are
    shown right below the warning as pickable buttons, so repeating all of
    that inside the warning text would just be noise."""
    result = {
        "varenavn": "",
        "price": "",
        "compare_price": "",
        "url": "",
        "candidates": [],
        "default_price": None,
    }
    search_values = [value for value in (mk_varenummer, mk_ordrenr) if value]
    if not search_values:
        return result, ["Kunne ikke søge pris uden MK-varenummer eller MK-ordrenummer"]

    fetched, fetch_error = _fetch_product_data(session, cache, cache_lock, search_values)
    if fetched is None:
        return result, [fetch_error]

    product_title, product_data, product_url = fetched
    result["url"] = product_url
    candidates = build_variant_candidates(product_title, product_data)

    exact_matches = [
        candidate for candidate in candidates
        if normalize_name_for_match(candidate["name"]) == normalize_name_for_match(ocr_varenavn)
    ]
    if len(exact_matches) == 1:
        match = exact_matches[0]
        result["varenavn"] = match["name"]
        result["price"] = match["price"]
        result["compare_price"] = match["compare_price"]
        result["url"] = build_variant_url(product_url, match["id"])
        warnings = [] if result["price"] else ["Kunne ikke læse aktuel pris"]
        return result, warnings

    result["candidates"] = rank_candidates_by_similarity(candidates, ocr_varenavn)
    result["default_price"] = estimate_default_price(candidates)
    if len(candidates) == 1:
        warnings = ["Produktnavn matchede ikke det fundne produkt på hjemmesiden"]
    else:
        warnings = ["Produktnavn og farve matchede ikke præcist - vælg det rigtige nedenfor"]
    return result, warnings

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


def load_images_from_zip(zip_path):
    """Extract a Google-Drive-style folder zip to a temp directory and
    collect the supported images inside it, wherever they are nested.
    Returns (image_files, extract_dir) - the caller keeps extract_dir
    alive for the app's lifetime since images are re-opened by path
    throughout the review flow."""
    extract_dir = Path(tempfile.mkdtemp(prefix="label_miner_"))
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
        self.root.title("Label Miner")
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
        ttk.Label(
            header,
            textvariable=self.selected_count,
            font=("Segoe UI", 11, "bold"),
        ).pack(side="right")

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
        if self.results_tree.exists(iid):
            self.results_tree.item(iid, values=values, tags=(tag,))
        else:
            self.results_tree.insert("", tk.END, iid=iid, values=values, tags=(tag,))
        self.results_tree.see(iid)

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
            self.status.set("Behandling færdig. Godkend eller spring de resterende gennemgange over.")
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
        # This reschedules itself every 100ms for the app's whole
        # lifetime - it's the only thing that ever pops review_queue for
        # the live (non-standalone) flow. Before this try/finally, an
        # exception anywhere in this body (say, a corrupt image blowing up
        # ReviewWindow's construction) would skip the self.root.after()
        # call below and permanently stop the queue from being drained for
        # the rest of the session - install_error_reporting's dialog would
        # still fire (report_callback_exception catches it same as any
        # other Tk callback), but nothing about that dialog signals "and
        # also every review from now on will silently never pop up",
        # which is exactly what would happen next. The reschedule is now
        # guaranteed regardless; the error still surfaces the normal way.
        record = None
        try:
            if self.review_window is None:
                record = self._pop_next_review_record()
                if record is not None:
                    self.review_window = ReviewWindow(self, record)
                    record = None
            elif self.review_window.record is None:
                record = self._pop_next_review_record()
                if record is not None:
                    self.review_window.load_record(record)
                    record = None
            else:
                self.review_window.update_review_count()
        finally:
            if record is not None:
                # Popped off review_queue but never actually handed to a
                # ReviewWindow because something above raised - put it
                # back rather than losing it, which would otherwise leave
                # review_total permanently one item ahead of
                # review_completed and export controls stuck disabled.
                self.review_queue.put(record)
            self.root.after(100, self.check_review_queue)

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
        if record is None or record.get("review_pending"):
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
        self.window.geometry("1050x680")
        self.window.minsize(900, 600)
        self.window.protocol("WM_DELETE_WINDOW", self.skip)
        set_window_icon(self.window)

        # Rotation fill and canvas backdrop share this color so the border
        # added around a rotated image blends into the background instead
        # of standing out as a bright frame. Pulled from the active ttk
        # theme so it tracks light/dark mode instead of being hardcoded -
        # resolved to a real hex value (see resolve_tk_color) since PIL
        # can't parse the symbolic color names ttk themes sometimes report.
        theme_background = ttk.Style(self.window).lookup("TFrame", "background")
        self.canvas_background = resolve_tk_color(self.window, theme_background or "#202020")

        # Candidate/price buttons and the raw-text disclosure toggle are
        # full-width (sticky="ew") but ttk.Button centers its label by
        # default, so text of varying length would otherwise start at a
        # different x position on every row - anchor="w" keeps them all
        # flush against the same left edge instead.
        ttk.Style(self.window).configure("Candidate.TButton", anchor="w")

        self.window.columnconfigure(0, weight=3, minsize=480)
        self.window.columnconfigure(1, weight=2, minsize=340)
        self.window.rowconfigure(0, weight=1, minsize=420)

        self.image_frame = ttk.Frame(self.window, padding=12)
        self.image_frame.grid(row=0, column=0, sticky="nsew")
        self.image_frame.columnconfigure(0, weight=1)
        self.image_frame.rowconfigure(0, weight=0)
        self.image_frame.rowconfigure(1, weight=1)

        # Has to be set before the rotation slider below: its initial
        # .set(0) fires _on_rotation_slider synchronously (see the comment
        # on rotation_label just below), which calls resize_image(), which
        # reads self.original_image. Any later resize_image() trigger
        # (canvas <Configure>, the slider, a 90-degree button) is safe once
        # this exists - it just wouldn't have anything to draw yet.
        self.original_image = None
        self.working_image = None
        self.crop_drag_start = None
        self.pending_crop_fraction = None
        self.image_bounds = None

        # Free-rotation slider: drag to straighten the photo at any angle,
        # live-previewed on a small, cheap-to-rotate thumbnail. The crop
        # selection is stored as fractions of that preview (see
        # crop_release_event), and only the final build_edited_image()
        # call ever rotates the full-resolution photo.
        self.rotation_angle = 0.0
        rotate_row = ttk.Frame(self.image_frame)
        rotate_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        rotate_row.columnconfigure(1, weight=1)
        ttk.Label(rotate_row, text="Rotation").grid(row=0, column=0, padx=(0, 8))
        # The label has to exist before the slider is created below: the
        # slider's initial .set(0) fires its -command callback immediately
        # (synchronously, not just on user interaction), and that callback
        # updates this label - creating the slider first meant every single
        # review window threw an AttributeError on open. It was swallowed
        # silently before install_error_reporting existed, so it had no
        # visible effect (rotation_angle just stayed 0, and show_image's
        # own resize_image() call painted the image correctly regardless) -
        # but it surfaced as a real error dialog as soon as errors stopped
        # being swallowed.
        self.rotation_label = ttk.Label(rotate_row, text="0°", width=5, anchor="e")
        self.rotation_slider = ttk.Scale(
            rotate_row,
            from_=-180,
            to=180,
            orient="horizontal",
            command=self._on_rotation_slider,
        )
        self.rotation_slider.set(0)
        self.rotation_slider.grid(row=0, column=1, sticky="ew")
        self.rotation_label.grid(row=0, column=2, padx=(8, 0))
        # Quick 90-degree nudges - the free-rotation slider is great for
        # fine straightening, but a label that's simply sideways is faster
        # to fix with one click than a careful drag.
        ttk.Button(
            rotate_row, text="⟲ 90°", width=6, command=lambda: self.rotate_quarter_turn(-90)
        ).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(
            rotate_row, text="⟳ 90°", width=6, command=lambda: self.rotate_quarter_turn(90)
        ).grid(row=0, column=4, padx=(4, 0))

        self.image_canvas = tk.Canvas(
            self.image_frame,
            background=self.canvas_background,
            highlightthickness=0,
        )
        self.image_canvas.grid(row=1, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", self.resize_image)
        self.image_canvas.bind("<ButtonPress-1>", self.crop_start_event)
        self.image_canvas.bind("<B1-Motion>", self.crop_drag_event)
        self.image_canvas.bind("<ButtonRelease-1>", self.crop_release_event)

        panel = ttk.Frame(self.window, padding=(18, 18, 18, 0))
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        panel.rowconfigure(1, weight=0)

        # Scrollable middle: warnings/raw text/fields can grow arbitrarily
        # tall without ever pushing the footer buttons off-screen.
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
        self.content_canvas.bind(
            "<Configure>",
            lambda event: self.content_canvas.itemconfigure(
                self.content_window, width=event.width
            ),
        )
        self.content_canvas.bind(
            "<Enter>",
            lambda event: self.content_canvas.bind_all(
                "<MouseWheel>", self._on_content_mousewheel
            ),
        )
        self.content_canvas.bind(
            "<Leave>", lambda event: self.content_canvas.unbind_all("<MouseWheel>")
        )

        ttk.Label(
            content,
            text="Gennemse label",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.review_count = tk.StringVar()
        self.review_count_label = ttk.Label(
            content,
            textvariable=self.review_count,
        )
        self.review_count_label.grid(row=0, column=0, sticky="e")
        if standalone:
            # "Gennemgang: X/Y" is a live-batch-queue position - meaningless
            # for a record reopened after the fact.
            self.review_count_label.grid_remove()
        ttk.Label(
            content,
            text="Advarsler",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=1, column=0, sticky="w", pady=(14, 3))
        self.warnings_frame = ttk.Frame(content)
        self.warnings_frame.grid(row=2, column=0, sticky="ew")
        self.warnings_frame.columnconfigure(0, weight=1)

        # Collapsed by default - the raw OCR text is mostly only needed to
        # diagnose a misread, and showing it expanded for every single
        # image (as this used to, whenever a warning category "relevant"
        # to it was present) made the window noisier than the raw text was
        # usually worth. One click reveals it when it's actually needed.
        self.raw_text_visible = False
        self.raw_text_toggle = ttk.Button(
            content,
            text="▶ Vis rå tekst",
            command=self.toggle_raw_text,
            style="Candidate.TButton",
        )
        self.raw_text_toggle.grid(row=3, column=0, sticky="w", pady=(12, 2))
        self.raw_text_widget = tk.Text(content, height=4, wrap="word")
        self.raw_text_widget.configure(state="disabled")
        self.raw_text_widget.grid(row=4, column=0, sticky="ew")
        self.raw_text_widget.grid_remove()

        fields = ttk.Frame(content)
        fields.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        fields.columnconfigure(0, weight=1)

        # One free-text price/comment field rather than a separate "Pris"
        # + "Sammenligningspris" pair - the latter used to just repeat
        # whatever discount price format_combined_price() already folded
        # into this field as "1234 kr (4321 kr)", with nothing visually
        # tying the two together. It also never reached the export either
        # way (excel_rows() only ever reads "price", straight into the
        # "Vejledende priser" column), so there was no
        # structured data lost by dropping it, only a redundant echo of
        # the same numbers.
        self.entries = {}
        self.add_entry(fields, 0, "MK Varenummer", "mk_varenummer")
        self.add_entry(fields, 1, "Varenavn", "varenavn")
        self.add_entry(fields, 2, "MK ordrenr", "mk_ordrenr")
        self.add_entry(fields, 3, "Stand", "stand")
        self.add_entry(fields, 4, "Pris / kommentar", "price")

        # Re-runs just the webshop lookup with whatever's currently typed
        # above - for fixing a typo'd number/name without redoing OCR on
        # the image. Placed right under the fields it refreshes rather
        # than in the footer, so it stays visually tied to them.
        self.price_refresh_in_progress = False
        price_actions = ttk.Frame(content)
        price_actions.grid(row=6, column=0, sticky="ew", pady=(0, 4))
        self.price_refresh_button = ttk.Button(
            price_actions, text="🔄  Genindlæs pris", command=self.refresh_price
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
        confirm_text = "Gem ændringer" if standalone else "Godkend og næste"
        skip_text = "Luk" if standalone else "Spring over"
        ttk.Button(footer, text=confirm_text, command=self.confirm).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(footer, text=skip_text, command=self.skip).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
        )
        # The one "primary action" in this window - styled as the theme's
        # accent button (sv-ttk's Accent.TButton) so it stands out without
        # hardcoding colors that would clash once a theme is applied.
        retry_button = ttk.Button(
            footer,
            text="Prøv igen (gen-læs label)",
            command=self.retry,
            style=accent_button_style(),
        )
        retry_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(
            footer,
            text="Åbn hjemmeside",
            command=self.open_website,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        image_buttons = ttk.Frame(self.image_frame)
        image_buttons.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        for column in range(2):
            image_buttons.columnconfigure(column, weight=1)
        ttk.Button(image_buttons, text="Spejl", command=self.mirror).grid(row=0, column=0, sticky="ew")
        ttk.Button(image_buttons, text="Nulstil", command=self.reset_image).grid(row=0, column=1, sticky="ew")
        ttk.Label(
            self.image_frame,
            text="Træk på billedet for at beskære det · brug skyderen ovenfor for at rette det",
            foreground="#a0a0a0",
        ).grid(row=3, column=0, pady=(4, 0))

        self.load_record(record)

    def add_entry(self, parent, row, label, key):
        label_row = row * 2
        entry_row = row * 2 + 1
        ttk.Label(parent, text=label).grid(row=label_row, column=0, sticky="w", pady=(0, 2))
        entry = ttk.Entry(parent)
        entry.grid(
            row=entry_row,
            column=0,
            sticky="ew",
            pady=(0, 9),
        )
        self.entries[key] = entry

    def _on_content_mousewheel(self, event):
        self.content_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def set_warnings(self, warnings, candidates=()):
        for widget in self.warnings_frame.winfo_children():
            widget.destroy()

        if not warnings:
            ttk.Label(
                self.warnings_frame, text="Ingen advarsler", foreground=COLOR_OK
            ).grid(row=0, column=0, sticky="w")
            return

        grouped = {}
        for category, message in warnings:
            grouped.setdefault(category, []).append(message)

        row = 0
        for category in WARNING_CATEGORY_ORDER:
            messages = grouped.get(category)
            if not messages:
                continue
            category_color = WARNING_CATEGORY_COLORS.get(category, COLOR_WARNING)
            ttk.Label(
                self.warnings_frame,
                text=WARNING_CATEGORY_LABELS[category],
                font=("Segoe UI", 9, "bold"),
                foreground=category_color,
            ).grid(row=row, column=0, sticky="w", pady=(6 if row else 0, 2))
            row += 1
            for message in messages:
                ttk.Label(
                    self.warnings_frame,
                    text=f"• {message}",
                    wraplength=340,
                    justify="left",
                ).grid(row=row, column=0, sticky="w")
                row += 1

        # Suggested variants live right under the shop-mismatch warning
        # they resolve, instead of a separate always-empty panel elsewhere
        # in the layout - picking one clears the warning above it.
        if candidates:
            ttk.Label(
                self.warnings_frame,
                text="Vælg det rigtige match:",
                font=("Segoe UI", 9, "bold"),
                foreground=COLOR_MUTED,
            ).grid(row=row, column=0, sticky="w", pady=(8, 2))
            row += 1
            for candidate in candidates:
                label = candidate["name"]
                if candidate["price"]:
                    label += f" — {candidate['price']}"
                ttk.Button(
                    self.warnings_frame,
                    text=label,
                    command=lambda c=candidate: self.apply_candidate(c),
                    style="Candidate.TButton",
                ).grid(row=row, column=0, sticky="ew", pady=(0, 4))
                row += 1

    def apply_candidate(self, candidate):
        """Called when you click a suggested-variant button: fills in the
        matched name/price, drops the now-resolved shop warning, and
        upgrades the record's URL to a deep link straight to this specific
        variant (the record's `url` up to now is only the bare product
        page - see build_variant_url)."""
        self.entries["varenavn"].delete(0, tk.END)
        self.entries["varenavn"].insert(0, candidate["name"])
        self.entries["price"].delete(0, tk.END)
        self.entries["price"].insert(
            0, format_combined_price(candidate["price"], candidate["compare_price"])
        )
        if self.record is not None:
            self.record["parsed"]["url"] = build_variant_url(
                self.record["parsed"].get("url", ""), candidate["id"]
            )
            self.record["warnings"] = [w for w in self.record["warnings"] if w[0] != "shop"]
            self.record["candidates"] = []
            self.set_warnings(self.record["warnings"])

    def toggle_raw_text(self):
        self.raw_text_visible = not self.raw_text_visible
        if self.raw_text_visible:
            self.raw_text_widget.grid()
            self.raw_text_toggle.configure(text="▼ Skjul rå tekst")
        else:
            self.raw_text_widget.grid_remove()
            self.raw_text_toggle.configure(text="▶ Vis rå tekst")

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
            fields["mk_ordrenr"],
            fields["varenavn"],
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
        elif shop_result["default_price"]:
            target_record["parsed"]["price"] = format_default_price_note(shop_result["default_price"])
        else:
            target_record["parsed"]["price"] = format_combined_price(
                shop_result["price"], shop_result["compare_price"]
            )
        target_record["parsed"]["compare_price"] = shop_result["compare_price"]
        if shop_result["url"]:
            target_record["parsed"]["url"] = shop_result["url"]
        target_record["candidates"] = shop_result["candidates"]
        target_record["warnings"] = [
            w for w in target_record["warnings"] if w[0] != "shop"
        ] + [("shop", message) for message in shop_warnings]

        if self.record is not target_record:
            return

        for key in ("varenavn", "price"):
            self.entries[key].delete(0, tk.END)
            self.entries[key].insert(0, target_record["parsed"].get(key, ""))
        self.set_warnings(target_record["warnings"], target_record["candidates"])
        if shop_warnings:
            self.price_refresh_status.configure(
                text="Intet præcist match - vælg herover eller ret felterne.",
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

        warnings = record["warnings"]
        self.set_warnings(warnings, record.get("candidates", []))

        # Reset any in-flight-refresh visuals from whatever record was open
        # before - a late result for that old record is a no-op anyway
        # (see _apply_price_refresh's identity check), but the button/status
        # itself needs to reflect *this* record's idle state regardless.
        self.price_refresh_in_progress = False
        self.price_refresh_button.configure(state="normal", text="🔄  Genindlæs pris")
        self.price_refresh_status.configure(text="")

        # Always starts collapsed - see toggle_raw_text.
        self.raw_text_visible = False
        self.raw_text_widget.grid_remove()
        self.raw_text_toggle.configure(text="▶ Vis rå tekst")
        raw_text = record.get("raw_text", "")
        if raw_text:
            self.raw_text_toggle.grid()
        else:
            self.raw_text_toggle.grid_remove()

        self.raw_text_widget.configure(state="normal")
        self.raw_text_widget.delete("1.0", tk.END)
        self.raw_text_widget.insert("1.0", raw_text)
        self.raw_text_widget.configure(state="disabled")

        for key, entry in self.entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, record["parsed"].get(key, ""))

        self.window.deiconify()
        self.window.lift()
        self.content_canvas.yview_moveto(0)

        self.show_image()
        self.update_review_count()

    def hide(self):
        self.record = None
        self.window.withdraw()

    def open_website(self):
        # `url` is set as soon as a product was found at all - matched
        # exactly or not - so this only falls back to a text search when
        # nothing was found on the shop, and does so using the varenavn
        # (the OCR'd/human-corrected name), not the MK-varenummer.
        parsed = self.record["parsed"]
        url = parsed.get("url", "")
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
            image = (self.working_image or self.original_image).copy()
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

    def read_fields(self):
        values = {}
        for key, widget in self.entries.items():
            if isinstance(widget, tk.Text):
                values[key] = widget.get("1.0", tk.END).strip()
            else:
                values[key] = widget.get().strip()
        return values

    def confirm(self):
        self.record["parsed"].update(self.read_fields())
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
        self.resize_image()

    def build_edited_image(self):
        """The image actually sent for re-OCR: base image, rotated to the
        slider's angle and cropped to any pending selection. This is the
        only place a real full-resolution rotate happens - everything
        interactive (dragging the slider, dragging a crop box) works off a
        cheap thumbnail and scale-independent fractions instead."""
        image = self.working_image or self.original_image
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
        self.rotation_angle = 0.0
        self.rotation_slider.set(0)
        self.rotation_label.configure(text="0°")
        self.resize_image()

    def retry(self):
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
        record = self.record
        self.record = None
        if self.standalone:
            self.app.open_standalone_windows.pop(id(record), None)
            self.window.destroy()
            return
        self.app.review_finished(record, None)

def normalize_label_varenavn(varenavn):
    # Treat × and x as the same, as a display standard (not just for matching)
    varenavn = varenavn.replace("×", "x")

    varenavn = PIPE_RE.sub(" | ", varenavn).strip()

    if "bord" in varenavn.casefold():
        varenavn = DIAMETER_DIGITS_RE.sub(r"Ø\1", varenavn)

    return varenavn


def parse_label_fields(text):
    """Extract mk_varenummer/varenavn/mk_ordrenr/stand from OCR'd label text.

    Tries the strict pattern (both OUTLET and STAND keywords present)
    first. If that fails - e.g. one keyword was misread - falls back to
    independently-anchored patterns so fields anchored on the keyword
    that *did* read correctly are still recovered, rather than returning
    nothing at all.
    """
    parsed_label = {
        "mk_varenummer": "",
        "varenavn": "",
        "mk_ordrenr": "",
        "stand": "",
    }
    partial_match_used = False

    strict_match = STRICT_LABEL_RE.search(text)
    if strict_match:
        parsed_label.update(
            {key: value.strip() for key, value in strict_match.groupdict().items()}
        )
    else:
        partial_match_used = True
        varenavn_pair_match = VARENAVN_PAIR_RE.search(text)
        if varenavn_pair_match:
            parsed_label["mk_varenummer"] = varenavn_pair_match["mk_varenummer"].strip()
            parsed_label["varenavn"] = varenavn_pair_match["varenavn"].strip()

        mk_ordrenr_stand_match = MK_ORDRENR_STAND_RE.search(text)
        if mk_ordrenr_stand_match:
            parsed_label["mk_ordrenr"] = mk_ordrenr_stand_match["mk_ordrenr"].strip()
            parsed_label["stand"] = mk_ordrenr_stand_match["stand"].strip()
        else:
            stand_only_match = STAND_ONLY_RE.search(text)
            if stand_only_match:
                parsed_label["stand"] = stand_only_match["stand"].strip()

    if parsed_label["stand"]:
        parsed_label["stand"] = parsed_label["stand"].upper()
    if parsed_label["varenavn"]:
        parsed_label["varenavn"] = normalize_label_varenavn(parsed_label["varenavn"])

    return parsed_label, partial_match_used


def extract_label(converter, image_path, session, cache, cache_lock):
    """Called from up to OCR_WORKERS worker threads at once, all sharing
    one DocumentConverter instance (see process_in_background) - verified
    safe by running it concurrently against a set of real label photos and
    diffing the extracted text against a single-threaded run (identical
    output, no exceptions, real wall-clock speedup - consistent with the
    torch/RapidOCR backend releasing the GIL during inference)."""
    conversion_path = image_path
    temporary_path = None

    try:
        with Image.open(image_path) as opened:
            # Phone photos (HEIC and plain JPEG alike) very commonly store
            # pixels "sideways" plus an EXIF Orientation tag rather than
            # upright pixels - ReviewWindow.show_image() already corrects
            # for this before *displaying* a photo (via the identical
            # ImageOps.exif_transpose() call), but nothing did the same
            # before *OCR-ing* it. Docling opens images with plain
            # PIL.Image.open(...).convert("RGB") and never looks at EXIF,
            # so a rotated-by-EXIF label used to get OCR'd sideways or
            # upside down - silently read as "no text found" here while
            # looking perfectly upright and legible in the review window,
            # with nothing to explain why.
            orientation = opened.getexif().get(ExifTags.Base.Orientation, 1)
            needs_orientation_fix = orientation not in (1, None)
            image = ImageOps.exif_transpose(opened) if needs_orientation_fix else opened
            needs_conversion = (
                needs_orientation_fix
                or image_path.suffix.lower() in {".heic", ".heif"}
                or max(image.size) > MAX_IMAGE_SIDE
            )
            if needs_conversion:
                image = image.convert("RGB")
                image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
                temporary_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temporary_path = Path(temporary_file.name)
                temporary_file.close()
                image.save(temporary_path, format="PNG")
                conversion_path = temporary_path

        result = converter.convert(str(conversion_path))
        text_parts = [item.text for item in result.document.texts if item.text]
        text = " ".join(text_parts)
        if not text:
            text = result.document.export_to_markdown()
        if not text_parts:
            text = IMAGE_COMMENT_RE.sub(" ", text)
            text = HEADING_RE.sub(" ", text)
        text = WHITESPACE_RE.sub(" ", text).strip()

        parsed_label, partial_match_used = parse_label_fields(text)

        warning_names = {
            "mk_varenummer": "MK-varenummeret",
            "varenavn": "varenavnet",
            "mk_ordrenr": "MK-ordrenummeret",
            "stand": "standen",
        }
        warnings = [
            ("missing", f"Kunne ikke læse {label}")
            for key, label in warning_names.items()
            if not parsed_label[key]
        ]
        if partial_match_used:
            warnings.append(
                ("partial", "Delvis label-genkendelse (ikke alle nøgleord fundet samtidigt)")
            )
        mk_varenummer = parsed_label["mk_varenummer"]
        if mk_varenummer and (
            not re.search(r"\d", mk_varenummer)
            or len(mk_varenummer) > 40
            or len(mk_varenummer.split()) > 6
        ):
            warnings.append(("suspicious", "Mistænkeligt MK-varenummer"))
        if parsed_label["mk_ordrenr"] and len(parsed_label["mk_ordrenr"].split()) > 4:
            warnings.append(("suspicious", "Mistænkelig tekst før MK-ordrenummer"))
        shop_result, shop_warnings = lookup_shop_price(
            session,
            cache,
            cache_lock,
            parsed_label["mk_varenummer"],
            parsed_label["mk_ordrenr"],
            parsed_label["varenavn"],
        )
        warnings.extend(("shop", message) for message in shop_warnings)
        if shop_result["varenavn"]:
            # A confident shop match wins over the raw OCR text - it's the
            # shop's own canonical name, and it's the only "name" field
            # left (there's no separate website_name any more), so this is
            # what both the review window and the export end up showing.
            parsed_label["varenavn"] = shop_result["varenavn"]
            parsed_label["price"] = format_combined_price(shop_result["price"], shop_result["compare_price"])
        elif shop_result["default_price"]:
            # No exact variant matched, but this product's own variants
            # agree closely enough on a price to offer it as a starting
            # point - always clearly marked as a guess, never silently
            # treated as a confirmed price, and never guessed across
            # products (see estimate_default_price).
            parsed_label["price"] = format_default_price_note(shop_result["default_price"])
        else:
            parsed_label["price"] = format_combined_price(shop_result["price"], shop_result["compare_price"])
        parsed_label["compare_price"] = shop_result["compare_price"]
        parsed_label["url"] = shop_result["url"]
        return text, parsed_label, warnings, shop_result["candidates"]
    finally:
        if temporary_path is not None:
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