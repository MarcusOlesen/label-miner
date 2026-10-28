"""Settings and constants shared across the app."""

from . import __version__

APP_VERSION = __version__  # shown in the title bar

# The user manual, built by docs/manual/make_manual.py. The installer puts it
# next to Label-Miner.exe, and the "? Hjælp" buttons open it.
MANUAL_NAME = "Label-Miner-Vejledning.pdf"

# The webshop where products and prices are looked up (a Shopify store).
SHOP_URL = "https://moebelkompagniet.dk"

SUPPORTED_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# Photos are scaled down to this before OCR: phone photos are ~4000 px, and
# the labels read just as well at this size, much faster.
MAX_IMAGE_SIDE = 2500

# Photos processed at the same time. One OCR engine can safely be shared
# between threads, and overlapping the OCR of one photo with the webshop
# requests of another made a batch ~1.8x faster with 3 workers (Docling,
# 16-core CPU, no GPU). With RapidOCR alone, 3 workers still OCR ~1.25x
# faster than 1 on a 4-core CPU. More workers gained little for a desktop app.
OCR_WORKERS = 3

# The shop's Google Sheet, in column order. The export writes these columns
# and "Kopiér resultat" copies all but the last two, so the rows can be
# pasted straight into the sheet.
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

# ---- Look -----------------------------------------------------------------

DEFAULT_FONT_FAMILY = "Segoe UI"
DEFAULT_FONT = (DEFAULT_FONT_FAMILY, 10)

# Status colours, shared by the result list and the review window so the same
# problem looks the same everywhere.
COLOR_OK = "#3fb950"
COLOR_WARNING = "#d29922"
COLOR_ERROR = "#f85149"
COLOR_MUTED = "#9aa0a6"
# The review window's "approve" button: the only green, filled button there,
# so it can't be mistaken for anything else.
COLOR_APPROVE = "#238636"
COLOR_APPROVE_ACTIVE = "#2ea043"
