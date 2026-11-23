import json
import os
import threading
import tkinter as tk
from pathlib import Path

import pytest

from harness import CachedOcrEngine, CachedSession

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def invisible_windows():
    """The UI tests use real Tk windows - made fully transparent, so a test
    run doesn't flash windows across the screen. They are still there for
    Windows (mapped, sized, clickable by the tests), so the tests run as
    before. LABEL_MINER_SHOW_WINDOWS=1 shows them, for debugging a test."""
    if os.environ.get("LABEL_MINER_SHOW_WINDOWS") == "1":
        yield
        return
    originals = {cls: cls.__init__ for cls in (tk.Tk, tk.Toplevel)}

    def hidden(original):
        def __init__(self, *args, **kwargs):
            original(self, *args, **kwargs)
            try:
                self.wm_attributes("-alpha", 0.0)
            except tk.TclError:
                pass

        return __init__

    for cls, original in originals.items():
        cls.__init__ = hidden(original)
    yield
    for cls, original in originals.items():
        cls.__init__ = original


def fixture_photos() -> list[str]:
    answers = json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))
    return [name for name in answers if not name.startswith("_")]


@pytest.fixture(scope="session")
def answers() -> dict:
    return json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def pipeline_results() -> dict:
    """Every fixture photo run through the real pipeline, with recorded OCR
    and webshop answers: {name: Record}."""
    from label_miner.pipeline import extract_label

    engine = CachedOcrEngine(FIXTURES / "ocr")
    session = CachedSession(FIXTURES / "shop")
    cache, lock = {}, threading.Lock()
    return {
        name: extract_label(engine, FIXTURES / "photos" / name, session, cache, lock)
        for name in fixture_photos()
    }


@pytest.fixture
def dialogs(monkeypatch):
    """Answer the yes/no dialogs automatically; records what was asked."""
    from label_miner.ui import review_window

    asked, answers = [], {}

    def fake(kind):
        def ask(title, message, **kwargs):
            asked.append(title)
            return answers.get(kind, True)

        return ask

    for kind in ("askyesno", "askyesnocancel", "askokcancel"):
        monkeypatch.setattr(review_window.messagebox, kind, fake(kind))
    return asked, answers


@pytest.fixture(autouse=True)
def own_settings_file(tmp_path, monkeypatch):
    """The tests never read or write the user's real settings (see label_miner.settings)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@pytest.fixture(autouse=True)
def no_waiting_for_the_shop(monkeypatch):
    """The catalogue download pauses between pages and when the shop is
    busy (see shop.download_catalogue); the tests note the pauses instead."""
    from label_miner import shop

    waits = []
    monkeypatch.setattr(shop, "_wait", waits.append)
    return waits
