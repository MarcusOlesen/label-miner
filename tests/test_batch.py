"""Zip extraction, batch naming and the clean-up of leftover temp folders."""

import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from label_miner.batch import (
    EXTRACT_OWNER_FILE,
    clean_up_old_extract_dirs,
    derive_source_folder,
    excel_rows,
    format_duration,
    load_images_from_zip,
    unique_export_path,
)


def make_folder(base: Path, name: str, owner=None, age_hours: float = 0) -> Path:
    folder = base / name
    (folder / "24-9").mkdir(parents=True)
    (folder / "24-9" / "IMG_0001.HEIC").write_bytes(b"photo")
    if owner is not None:
        (folder / EXTRACT_OWNER_FILE).write_text(str(owner), encoding="ascii")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(folder, (old, old))
    return folder


@pytest.fixture
def processes():
    """The id of a process that has exited, and one that is still running."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    yield finished.pid, running.pid
    running.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="process check uses the Windows API")
def test_clean_up_only_removes_folders_nobody_uses(tmp_path, processes):
    finished, running = processes
    cases = {
        # folder: should it be removed?
        make_folder(tmp_path, "label_miner_a", running, age_hours=72): False,  # its program is running
        make_folder(tmp_path, "label_miner_b", os.getpid()): False,  # this program's own folder
        make_folder(tmp_path, "label_miner_c", finished): True,  # its program has exited
        make_folder(tmp_path, "label_miner_d", age_hours=48): True,  # unmarked (v1.0) and old
        make_folder(tmp_path, "label_miner_e", age_hours=1): False,  # unmarked but recent
        make_folder(tmp_path, "label_miner_f", "not-a-number"): False,  # can't tell whose it is
        make_folder(tmp_path, "something_else", age_hours=72): False,  # not ours at all
    }
    removed = set(clean_up_old_extract_dirs(tmp_path))
    for folder, should_go in cases.items():
        assert (not folder.exists()) == should_go, folder.name
        assert (folder in removed) == should_go, folder.name


def test_zip_extraction_and_batch_name(tmp_path):
    zip_path = tmp_path / "9-20260924T112830Z-1-001.zip"  # a Google Drive download
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("24-9/IMG_0001.HEIC", b"photo")
        archive.writestr("24-9/notes.txt", b"not a photo")
        archive.writestr("__MACOSX/24-9/._IMG_0001.HEIC", b"mac metadata")
    files, folder = load_images_from_zip(zip_path)
    try:
        assert [f.name for f in files] == ["IMG_0001.HEIC"]
        assert (folder / EXTRACT_OWNER_FILE).read_text() == str(os.getpid())
        name, export_dir = derive_source_folder(files, extraction_root=folder, zip_path=zip_path)
        assert (name, export_dir) == ("24-9", tmp_path)
    finally:
        import shutil

        shutil.rmtree(folder)


def test_unique_export_path_never_overwrites(tmp_path):
    (tmp_path / "24-9.xlsx").write_bytes(b"")
    (tmp_path / "24-9 (1).xlsx").write_bytes(b"")
    assert unique_export_path(tmp_path, "24-9") == tmp_path / "24-9 (2).xlsx"


@pytest.mark.parametrize(("seconds", "text"), [(5, "5 sek"), (125, "2 min 5 sek"), (3720, "1 t 2 min")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


def record(name, number="", order="", stand="", price="", warnings=()):
    parsed = {"varenavn": "Vega sofa", "mk_varenummer": number, "mk_ordrenr": order, "stand": stand}
    parsed["price"] = price
    return {"path": Path(name), "parsed": parsed, "warnings": list(warnings)}


def test_excel_rows_groups_photos_of_the_same_item():
    records = [
        record("IMG_1.HEIC", "101338", "200101", "A", "8.999 kr"),
        record("IMG_2.HEIC", "101338", "200101", "A", "8.999 kr"),
        record("IMG_3.HEIC", "101338", "200102", "A"),
        # Unreadable photos are never merged, as they can't be told apart
        record("IMG_4.HEIC"),
        record("IMG_5.HEIC", warnings=[("missing", "Kunne ikke læse standen")]),
    ]
    rows = excel_rows(records, "24-9")

    assert rows[0][0] == "24-9"
    items = rows[1:]
    assert [row[5] for row in items] == [2, 1, 1, 1]  # STK
    assert items[0][11] == "IMG_1.HEIC, IMG_2.HEIC"
    assert items[0][7] == "8.999 kr"
    # A warning left on a record ends up in the comments column
    assert items[3][7] == "Kunne ikke læse standen"
