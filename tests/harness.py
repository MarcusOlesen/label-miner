"""Replaying OCR and webshop answers, and scoring a label against the answer key.

The regression tests run the real ``extract_label`` pipeline, but with the two
slow or external dependencies replaced by recordings:

- ``CachedConverter`` stands in for Docling. Results are stored per image,
  keyed by the image's pixels, so any change in what the app sends to OCR
  (crop, rotation, resize) is a cache miss rather than a silently stale hit.
- ``CachedSession`` stands in for ``requests.Session`` with recorded webshop
  responses.

With ``live`` set, misses are filled by the real thing and recorded
(needs the ``ocr`` extra and internet); otherwise a miss is an error.
"""

import hashlib
import json
import re
import threading
from pathlib import Path

from PIL import Image


def image_key(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        digest = hashlib.sha1(f"{image.size}".encode())
        digest.update(image.tobytes())
    return digest.hexdigest()


class CachedConverter:
    def __init__(self, cache_dir: Path, live: bool = False):
        self.cache_dir = Path(cache_dir)
        self.live = live
        self.converter = None
        self.lock = threading.Lock()
        self.ocr_runs = 0

    def convert(self, source):
        from docling_core.types.doc import DoclingDocument

        cache_file = self.cache_dir / f"{image_key(Path(source))}.json"
        if cache_file.exists():
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            document = DoclingDocument.model_validate(data)
        elif self.live:
            with self.lock:
                if self.converter is None:
                    from label_miner.ocr import create_converter

                    self.converter = create_converter()
            document = self.converter.convert(str(source)).document
            self.ocr_runs += 1
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(document.export_to_dict(), ensure_ascii=False), encoding="utf-8")
        else:
            raise LookupError(f"No recorded OCR result for {source} ({cache_file.name})")
        return type("ConversionResult", (), {"document": document})()


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class CachedSession:
    """The one method of requests.Session the app uses: get()."""

    def __init__(self, cache_dir: Path, live: bool = False):
        self.cache_dir = Path(cache_dir)
        self.live = live
        self.requests = 0

    def get(self, url, params=None, headers=None, timeout=None):
        key = url + "?" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        cache_file = self.cache_dir / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.json"
        if cache_file.exists() and not self.live:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return _Response(cached["status"], cached["json"])
        if not self.live:
            raise LookupError(f"No recorded webshop response for {key}")
        import requests

        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        self.requests += 1
        try:
            payload = response.json()
        except ValueError:
            payload = None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        record = {"key": key, "status": response.status_code, "json": payload}
        cache_file.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return _Response(response.status_code, payload)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

LABEL_FIELDS = ("mk_varenummer", "mk_ordrenr", "stand")


def _compact(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _name_words(name) -> list[str]:
    name = str(name or "").casefold().replace("×", "x").replace("ø", "0")
    return re.findall(r"\w+", name)


def _variant_of(url):
    match = re.search(r"[?&]variant=(\d+)", url or "")
    return int(match.group(1)) if match else None


def _handle_of(url):
    match = re.search(r"/products/([^?]+)", url or "")
    return match.group(1) if match else None


def score(answer: dict, parsed: dict, warnings: list, candidates: list) -> tuple[str, list[str]]:
    """Compare one pipeline result with the answer key.

    Returns (verdict, problems). Verdicts:
      "WRONG"     approved automatically although something is wrong - must never happen
      "AUTO ok"   approved automatically and correct
      "REVIEW ok" sent to review, as the answer key expects
      "REVIEW"    sent to review although it could have been approved (annoying, not dangerous)
    For reviews, a problem is also recorded when the right product isn't among the suggestions.
    """
    problems = []
    for field in LABEL_FIELDS:
        if _compact(parsed.get(field)) != _compact(answer[field]):
            problems.append(f"{field}: read {parsed.get(field)!r}, label says {answer[field]!r}")

    shop = answer.get("shop")
    if not warnings:  # approved automatically
        if answer["expect"] == "review":
            problems.append(f"should have gone to review ({answer.get('reason', '')})")
        if shop is None:
            if parsed.get("price") or parsed.get("url"):
                problems.append("product is not on the shop, but a price/URL was filled in")
        else:
            if _handle_of(parsed.get("url")) != shop["handle"]:
                problems.append(f"wrong product: {parsed.get('url')!r}, expected {shop['handle']!r}")
            elif shop.get("variant") and _variant_of(parsed.get("url")) != shop["variant"]:
                problems.append(f"wrong variant: expected {shop['variant']}")
            if not parsed.get("price"):
                problems.append("approved without a price")
        return ("WRONG" if problems else "AUTO ok"), problems

    # In a review the user sees the name as read from the label
    if _name_words(parsed.get("varenavn")) != _name_words(answer["varenavn"]):
        problems.append(f"varenavn: read {parsed.get('varenavn')!r}, label says {answer['varenavn']!r}")
    if shop is not None:
        offered = (
            any(
                (shop.get("variant") and c.get("id") == shop["variant"])
                or _handle_of(c.get("url")) == shop["handle"]
                for c in candidates
            )
            or _handle_of(parsed.get("url")) == shop["handle"]
        )
        if not offered:
            problems.append("the right product was not among the suggestions")
    return ("REVIEW ok" if answer["expect"] == "review" else "REVIEW"), problems
