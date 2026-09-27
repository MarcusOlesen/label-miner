"""The example photos the manuals use, and what the program makes of them.

The manual, the guides and the practice pack all use the test fixture photos
(tests/fixtures/photos), with the OCR and webshop answers recorded there, so
they can be rebuilt without Docling and always show what the program really
does with those photos.
"""

import functools
import json
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from harness import CachedConverter, CachedSession  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PHOTOS = FIXTURES / "photos"
BUILD = ROOT / "build"
ICON = ROOT / "src" / "label_miner" / "assets" / "label_miner.ico"


def converter() -> CachedConverter:
    """Stands in for Docling, with the recorded OCR results."""
    return CachedConverter(FIXTURES / "ocr")


class ReplaySession:
    """Recorded webshop answers; anything else (the product photos shown in
    the review window) is fetched live."""

    def __init__(self):
        self.recorded = CachedSession(FIXTURES / "shop")

    def get(self, url, params=None, headers=None, timeout=None):
        try:
            return self.recorded.get(url, params=params, headers=headers, timeout=timeout)
        except LookupError:
            import requests

            return requests.get(url, params=params, headers=headers, timeout=timeout)


def answers() -> dict:
    """The verified answer key (tests/fixtures/answers.json)."""
    data = json.loads((FIXTURES / "answers.json").read_text(encoding="utf-8"))
    return {name: answer for name, answer in data.items() if not name.startswith("_")}


@functools.cache
def pipeline_results() -> dict:
    """Every fixture photo run through the real pipeline: {name: {"parsed",
    "warnings", "candidates"}}. "parsed" includes label_box and photo_turn."""
    from label_miner.pipeline import extract_label

    ocr, session = converter(), CachedSession(FIXTURES / "shop")
    cache, lock = {}, threading.Lock()
    results = {}
    for name in answers():
        _, parsed, warnings, candidates = extract_label(ocr, PHOTOS / name, session, cache, lock)
        results[name] = {"parsed": parsed, "warnings": warnings, "candidates": candidates}
    return results
