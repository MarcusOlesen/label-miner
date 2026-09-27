"""Settings and constants shared across the app."""

from . import __version__

APP_VERSION = __version__  # shown in the title bar

# The user manual, built by docs/manual/make_manual.py. Installed next to
# Label-Miner.exe; opened by the "? Hjælp" buttons.
MANUAL_NAME = "Label-Miner-Vejledning.pdf"


DEFAULT_FONT_FAMILY = "Segoe UI"


DEFAULT_FONT = (DEFAULT_FONT_FAMILY, 10)


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
