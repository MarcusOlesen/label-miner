"""A batch of photos: zip extraction, temp folder clean-up and the export.

Photos arrive as a zip downloaded from Google Drive (one folder per delivery,
e.g. "24-9"). The zip is extracted to a temp folder for the session, and the
result is exported as a spreadsheet next to the zip, named after the folder.
"""

import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .config import EXCEL_COLUMNS, SUPPORTED_EXTENSIONS
from .review import warning_messages

# The suffix Google Drive adds to a downloaded folder's zip,
# e.g. "9-20260917T102218Z-1-001" -> "9"
GOOGLE_DRIVE_ZIP_SUFFIX_RE = re.compile(r"-\d{8}T\d{6}Z-\d+-\d+$")

EXTRACT_DIR_PREFIX = "label_miner_"
# Written into each extraction folder: the process id of the Label Miner
# using it (see clean_up_old_extract_dirs)
EXTRACT_OWNER_FILE = "owner.pid"
# Folders without an owner file (from v1.0) are removed once this old
UNMARKED_EXTRACT_DIR_MAX_AGE = 24 * 60 * 60


def format_duration(seconds: float) -> str:
    """Remaining time in Danish: "2 min 5 sek"."""
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} t {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} sek"
    return f"{seconds} sek"


def derive_source_folder(
    image_files: list[Path], extraction_root: Path | None = None, zip_path: Path | None = None
) -> tuple[str, Path]:
    """The batch name (the export's first row and file name) and the folder
    to save the export in.

    From a zip: the name of the folder inside it (e.g. "15-9"), or the zip's
    own name if the photos sit at its root; saved next to the zip. From
    loose photos: their common folder is both the name and the place.
    """
    parents = {path.parent for path in image_files}
    try:
        # commonpath raises ValueError for paths on different drives
        # (e.g. photos picked from C:\ and a USB stick together)
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


def process_is_running(pid: int) -> bool:
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


def clean_up_old_extract_dirs(temp_dir: Path | None = None) -> list[Path]:
    """Delete extraction folders left behind by sessions that didn't close
    normally (a normal close removes its own). Returns the removed folders.

    A folder another running Label Miner is using is never touched: a folder
    is removed only if the program in its owner.pid has exited - however old
    the folder is, as a batch can stay open for days - or, without an owner
    file (v1.0), once it is more than a day old.
    """
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


def load_images_from_zip(zip_path: Path) -> tuple[list[Path], Path]:
    """Extract the zip to a new temp folder and find the photos in it.

    Returns (image_files, extract_dir). The folder stays for the whole
    session, as the review window reopens the photos from it.
    """
    extract_dir = Path(tempfile.mkdtemp(prefix=EXTRACT_DIR_PREFIX))
    # Mark the folder as this program's, so another Label Miner starting up
    # leaves it alone
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


def unique_export_path(directory: Path, base_name: str) -> Path:
    """A new file name for the export: "24-9.xlsx", else "24-9 (1).xlsx", ...
    An existing file is never overwritten."""
    candidate = directory / f"{base_name}.xlsx"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = directory / f"{base_name} ({counter}).xlsx"
        if not candidate.exists():
            return candidate
        counter += 1


def excel_rows(records: list[dict], batch_name: str) -> list[list]:
    """The export's rows: the batch name, then one row per item.

    Photos of the same item (same MK number, order number and STAND) become
    one row with STK = the number of photos. Rows whose three fields are all
    empty (e.g. an unreadable photo) are never merged, as they can't be told
    apart. Warnings left on a record go into the comments column, so an
    unchecked row is visible in the sheet.
    """
    rows = [[batch_name] + [""] * (len(EXCEL_COLUMNS) - 1)]
    groups = {}
    for record in records:
        parsed = record["parsed"]
        key = (parsed.get("mk_varenummer", ""), parsed.get("mk_ordrenr", ""), parsed.get("stand", ""))
        if not any(key):
            key = ("__unique__", id(record))
        group = groups.setdefault(key, {"first": record, "count": 0, "image_names": []})
        group["count"] += 1
        group["image_names"].append(record["path"].name)

    for group in groups.values():
        record = group["first"]
        parsed = record["parsed"]
        comments = parsed.get("price", "")
        if record.get("warnings"):
            warning_text = " | ".join(warning_messages(record["warnings"]))
            comments = " | ".join(value for value in (comments, warning_text) if value)
        rows.append(
            [
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
            ]
        )
    return rows
