"""The review window, driven like a user would. Needs a display; OCR and
webshop answers are replayed from the fixtures.

Run only these with ``pytest -m gui``; skip them with ``pytest -m "not gui"``.
"""

import threading
import tkinter as tk

import pytest

from conftest import FIXTURES
from harness import CachedSession

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def root():
    """One Tk root for the module: creating many Tk interpreters in a row is
    unreliable on Windows."""
    try:
        root = tk.Tk()
    except tk.TclError as error:  # no display
        pytest.skip(f"no display: {error}")
    root.withdraw()
    yield root
    root.destroy()


class FakeApp:
    """The parts of the main window a ReviewWindow talks to."""

    def __init__(self, root):
        self.root = root
        self.http_session = CachedSession(FIXTURES / "shop")
        self.thumbnail_cache = {}
        self.price_cache = {}
        self.price_cache_lock = threading.Lock()
        self.review_completed, self.review_total = 0, 20
        self.open_standalone_windows = {}
        self.exported = False
        self.finished, self.retried = [], []

    def review_finished(self, record, updated):
        self.finished.append((record["path"].name, updated is not None))

    def retry_review(self, record, working_image, standalone=False):
        self.retried.append(record["path"].name)

    def upsert_result_row(self, *args):
        pass


@pytest.fixture
def review(root, pipeline_results):
    from label_miner.ui.review_window import ReviewWindow

    def record(name):
        result = pipeline_results[name]
        return {
            "path": FIXTURES / "photos" / name,
            "raw_text": result["text"],
            "parsed": dict(result["parsed"]),
            "warnings": [tuple(w) for w in result["warnings"]],
            "candidates": result["candidates"],
            "review_pending": True,
        }

    app = FakeApp(root)
    window = ReviewWindow(app, record("24-9/IMG_0302.HEIC"))
    root.update()
    yield window, app, record
    window.window.destroy()


def test_picking_a_suggestion_only_fills_the_fields(review, dialogs):
    window, app, record = review
    rec = window.record
    before = dict(rec["parsed"])
    window.select_candidate(0)
    assert window.entries["varenavn"].get().startswith("Manhattan")
    assert window.entries["price"].get() == "17.999 kr (24.999 kr)"
    assert rec["parsed"] == before  # nothing saved yet


def test_skipping_with_edits_asks_and_can_discard(review, dialogs):
    window, app, _ = review
    asked, answers = dialogs
    rec = window.record
    before = dict(rec["parsed"])
    window.select_candidate(0)
    answers["askyesnocancel"] = None  # "Annuller"
    window.skip()
    assert asked[-1] == "Gem ændringerne?" and window.record is rec
    answers["askyesnocancel"] = False  # "Nej": discard
    window.skip()
    assert app.finished[-1] == ("IMG_0302.HEIC", False)
    assert rec["parsed"] == before


def test_approving_saves_the_picked_product(review, dialogs):
    window, app, record = review
    window.load_record(record("24-9/IMG_0302.HEIC"))
    rec = window.record
    window.select_candidate(0)
    window.confirm()
    assert rec["parsed"]["varenavn"].startswith("Manhattan")
    assert "manhattan" in rec["parsed"]["url"]
    assert rec["warnings"] == [] and rec["candidates"] == []


def test_unpicking_restores_the_label(review, dialogs):
    window, app, record = review
    window.load_record(record("24-9/IMG_0292.HEIC"))
    before = window.read_fields()
    window.select_candidate(1)
    window.select_candidate(1)
    assert window.read_fields() == before and not window.fields_changed()


def test_unusual_stand_asks_before_approving(review, dialogs):
    window, app, record = review
    asked, answers = dialogs
    window.load_record(record("15-9/IMG_9176.HEIC"))
    rec = window.record
    answers["askyesno"] = False
    window.confirm()
    assert asked[-1] == "Tjek STAND" and window.record is rec
    window.entries["stand"].delete(0, tk.END)
    window.entries["stand"].insert(0, "a")
    window.confirm()
    assert rec["parsed"]["stand"] == "A"


def test_part_of_item_never_gets_a_price(review, dialogs):
    window, app, record = review
    window.load_record(record("24-9/IMG_0283.HEIC"))
    window.select_candidate(0)
    assert window.entries["price"].get() == "OBS: delvis vare (del 1/2)"


def test_zoom_and_upright_display(review, dialogs):
    window, app, record = review
    window.load_record(record("24-9/IMG_0302.HEIC"))
    full = window.original_image.size
    assert window.zoomed and window.display_base().size < full
    assert window.build_edited_image().size == full  # re-reading uses the whole photo
    window.toggle_zoom()
    assert window.display_base().size == full
    window.load_record(record("24-9/IMG_0285.HEIC"))  # photographed sideways
    width, height = window.original_image.size
    assert width < height


def test_closing_keeps_the_item_and_its_edits(review, dialogs):
    window, app, record = review
    window.load_record(record("24-9/IMG_0302.HEIC"))
    rec = window.record
    window.entries["mk_ordrenr"].insert(tk.END, "7")
    window.close_window()
    window.show()
    assert window.record is rec and window.entries["mk_ordrenr"].get() == "1490227"


class InlineThread:
    """Runs a "background" job right away, on the test's thread: Tk can only
    be called from the thread that runs it."""

    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def run_refresh(window, monkeypatch):
    """Press "Søg på hjemmesiden igen" and let the lookup finish."""
    from label_miner.ui import review_window

    monkeypatch.setattr(review_window.threading, "Thread", InlineThread)
    window._refresh_price()
    window.window.update()
    assert not window.price_refresh_in_progress


def test_typed_number_fills_in_the_fields(review, dialogs, monkeypatch):
    """New labels can arrive with no number or name read: typing the
    number and searching fills in name and price - for the user to approve."""
    window, app, record = review
    window.load_record(record("4-11/IMG_0978.JPG"))
    for key in ("mk_varenummer", "varenavn", "price"):
        window.entries[key].delete(0, tk.END)
    window.entries["mk_varenummer"].insert(0, "106854")
    run_refresh(window, monkeypatch)
    assert window.entries["varenavn"].get() == "Oris sofabord, marmor emperador | Ø82"
    assert window.entries["price"].get() == "1.999 kr (2.999 kr)"
    assert "/products/oris-sofabord-marmor-emperador-o82" in window.record["parsed"]["url"]
    assert [category for category, _ in window.record["warnings"]] == ["by_number"]
    assert window.question_title.cget("text") == "Er det den rigtige vare?"


def test_typed_number_with_variants_offers_cards(review, dialogs, monkeypatch):
    window, app, record = review
    window.load_record(record("4-11/IMG_0978.JPG"))
    for key in ("mk_varenummer", "varenavn"):
        window.entries[key].delete(0, tk.END)
    window.entries["mk_varenummer"].insert(0, "103284")
    run_refresh(window, monkeypatch)
    assert sorted(candidate["name"] for candidate in window.candidates) == [
        "Nord spisebordsstol Beige",
        "Nord spisebordsstol Taupe",
    ]
    window.select_candidate(0)
    assert window.entries["varenavn"].get() == window.candidates[0]["name"]
    assert window.entries["price"].get() == "899 kr (1.399 kr)"
