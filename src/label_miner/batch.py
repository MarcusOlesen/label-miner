"""A batch of photos: zip extraction, temp folder clean-up and export naming."""

import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .config import SUPPORTED_EXTENSIONS

# Strips the timestamp/part suffix Google Drive appends when it zips a
# folder for download, e.g. "9-20260917T102218Z-1-001" -> "9". Only used
# as a fallback name when a zip's images sit directly at its root instead
# of inside a named subfolder (see derive_source_folder).
GOOGLE_DRIVE_ZIP_SUFFIX_RE = re.compile(r"-\d{8}T\d{6}Z-\d+-\d+$")


def format_duration(seconds):
    seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} t {minutes} min"
    if minutes:
        return f"{minutes} min {seconds} sek"
    return f"{seconds} sek"


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
