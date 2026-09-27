"""Small helpers: debug output and finding bundled resources."""

import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[1]

# Run with --debug (also works for the installed .exe, from a terminal) to see
# each photo's OCR text, the parsed fields and diagnostics on the console.
DEBUG = "--debug" in sys.argv


def debug_log(*args, **kwargs) -> None:
    """print(), but only with --debug. The windowed .exe has no console at
    all (sys.stdout is None), so printing there would crash."""
    if DEBUG and sys.stdout is not None:
        print(*args, **kwargs)


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
