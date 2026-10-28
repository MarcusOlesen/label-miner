"""A whole batch in the main window, driven like a user would. Needs a display;
OCR and webshop answers are replayed from the fixtures.

Kept apart from test_ui.py: the app needs its own Tk root, and two Tk roots
alive at the same time mix up their images.
"""

import shutil
import sys
import time
import tkinter as tk
from pathlib import Path

import pytest

from conftest import FIXTURES
from harness import CachedOcrEngine, CachedSession

pytestmark = pytest.mark.gui


def run_until(root, condition, timeout=240):
    """Run Tk's main loop until condition() holds (the app's worker threads
    report back with root.after, which needs a running main loop)."""
    deadline = time.time() + timeout

    def poll():
        if condition() or time.time() > deadline:
            root.quit()
        else:
            root.after(100, poll)

    root.after(100, poll)
    root.mainloop()
    assert condition(), "timed out"


def test_a_whole_batch(tmp_path, monkeypatch, dialogs):
    """Four photos: two approved automatically (one sideways), two for review,
    reviewed through the button and exported to Excel."""
    from openpyxl import load_workbook

    from label_miner.ui import app as app_module

    photos = ["24-9/IMG_0302.HEIC", "24-9/IMG_0276.HEIC", "24-9/IMG_0283.HEIC", "15-9/IMG_9164.HEIC"]
    batch = tmp_path / "e2e"
    batch.mkdir()
    for name in photos:
        shutil.copy(FIXTURES / "photos" / name, batch / Path(name).name)

    engine = CachedOcrEngine(FIXTURES / "ocr")
    monkeypatch.setattr(
        app_module.LabelMinerApp, "load_ocr_engine", lambda self: setattr(self, "ocr_engine", engine)
    )
    monkeypatch.setattr(app_module, "clean_up_old_extract_dirs", lambda *args: [])  # leave %TEMP% alone
    # pytest's own arguments (e.g. tests/test_app.py) would be taken for dropped photos
    monkeypatch.setattr(sys, "argv", sys.argv[:1])
    try:
        root = app_module.TkinterDnD.Tk() if app_module.TkinterDnD is not None else tk.Tk()
    except tk.TclError as error:
        pytest.skip(f"no display: {error}")
    try:
        app = app_module.LabelMinerApp(root)
        app.http_session = CachedSession(FIXTURES / "shop")
        app.ocr_engine_ready()
        app.begin_processing(sorted(batch.iterdir()))
        run_until(root, lambda: not app.processing and app.work_completed >= len(photos))

        assert app.review_window is None  # no automatic popup
        run_until(root, lambda: app.review_bar_visible)
        assert "(2)" in app.review_button.cget("text")

        app.open_review()
        first = app.review_window.record
        app.review_window.close_window()  # ✕ keeps the place
        app.open_review()
        assert app.review_window.record is first

        # Double-clicking the other waiting row opens that one, not the next in line
        second = next(r for r in app.records if r["review_pending"] and r is not first)
        root.update()
        _, y, _, height = app.results_tree.bbox(str(second["path"]))
        app.reopen_record_review(type("Event", (), {"y": y + height // 2})())
        assert app.review_window.record is second
        assert app.review_queue.queue[0] is first

        for _ in range(2):
            window = app.review_window
            if window.candidates:
                window.select_candidate(0)
            window.confirm()
        run_until(root, lambda: app.exported and not app.review_bar_visible)

        rows = list(load_workbook(app.export_path).active.iter_rows(values_only=True))[2:]
        by_image = {row[11]: row for row in rows}
        assert by_image["IMG_0302.HEIC"][1].startswith("Manhattan")  # the conflict, resolved
        assert by_image["IMG_0283.HEIC"][7] == "OBS: delvis vare (del 1/2)"  # part of an item: no price
        assert by_image["IMG_9164.HEIC"][1].startswith("Paris U-sofa")  # sideways, approved on its own
    finally:
        root.destroy()
