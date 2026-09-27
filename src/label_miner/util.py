"""Small helpers: debug output and finding bundled resources."""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]


def resource_path(name: str) -> Path:
    """Find a bundled resource (the icon, the docling-models folder).

    Frozen with PyInstaller, bundled files live in sys._MEIPASS - not next
    to the .exe (a "--onedir" build puts them in "_internal"). From source,
    the icon is in the package's assets folder and docling-models (fetched
    by packaging/fetch_models.ps1) in the repository root.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / name
    for candidate in (PACKAGE_DIR / "assets" / name, REPO_ROOT / name):
        if candidate.exists():
            return candidate
    return REPO_ROOT / name


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
