import json
import threading
from pathlib import Path

import pytest

from harness import CachedOcrEngine, CachedSession

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_photos() -> list[str]:
    answers = json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))
    return [name for name in answers if not name.startswith("_")]


@pytest.fixture(scope="session")
def answers() -> dict:
    return json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def pipeline_results() -> dict:
    """Every fixture photo run through the real pipeline, with recorded OCR and webshop answers."""
    from label_miner.pipeline import extract_label

    engine = CachedOcrEngine(FIXTURES / "ocr")
    session = CachedSession(FIXTURES / "shop")
    cache, lock = {}, threading.Lock()
    results = {}
    for name in fixture_photos():
        text, parsed, warnings, candidates = extract_label(
            engine, FIXTURES / "photos" / name, session, cache, lock
        )
        results[name] = {"text": text, "parsed": parsed, "warnings": warnings, "candidates": candidates}
    return results


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
