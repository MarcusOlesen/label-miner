"""Replaying OCR and webshop answers, and scoring a label against the answer key.

The regression tests run the real ``extract_label`` pipeline, but with the two
slow or external dependencies replaced by recordings:

- ``CachedOcrEngine`` stands in for RapidOCR. Results are stored per image,
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
from types import SimpleNamespace

from PIL import Image


def image_key(image: Image.Image) -> str:
    image = image.convert("RGB")
    digest = hashlib.sha1(f"{image.size}".encode())
    digest.update(image.tobytes())
    return digest.hexdigest()


class CachedOcrEngine:
    """Called like a RapidOCR engine: engine(image) -> result with
    ``boxes`` (four corner points per line), ``txts`` and ``scores``."""

    def __init__(self, cache_dir: Path, live: bool = False):
        self.cache_dir = Path(cache_dir)
        self.live = live
        self.engine = None
        self.lock = threading.Lock()
        self.ocr_runs = 0

    def __call__(self, image: Image.Image):
        cache_file = self.cache_dir / f"{image_key(image)}.json"
        recorded = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else None
        if recorded is not None and "txts" not in recorded:
            recorded = None  # recorded by an older OCR engine (Docling)
        if recorded is None:
            if not self.live:
                raise LookupError(f"No recorded OCR result for this image ({cache_file.name})")
            with self.lock:
                if self.engine is None:
                    from label_miner.ocr import create_ocr_engine

                    self.engine = create_ocr_engine()
            result = self.engine(image)
            self.ocr_runs += 1
            boxes = [] if result.boxes is None else result.boxes
            recorded = {
                "boxes": [[[float(x), float(y)] for x, y in box] for box in boxes],
                "txts": list(result.txts or ()),
                "scores": [float(score) for score in result.scores or ()],
            }
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(recorded, ensure_ascii=False), encoding="utf-8")
        return SimpleNamespace(
            boxes=recorded["boxes"] or None, txts=tuple(recorded["txts"]), scores=tuple(recorded["scores"])
        )


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
    The answer's "shop" is the product (handle, and variant when the label
    tells it), null when it isn't on the shop, or "unknown" when the
    recordings can't tell - then only the label's fields are scored, and an
    automatic approval counts as WRONG, as it can't be checked. "antal" is
    only in the answers for the new label design.
    """
    problems = []
    for field in LABEL_FIELDS + (("antal",) if "antal" in answer else ()):
        if _compact(parsed.get(field)) != _compact(answer[field]):
            problems.append(f"{field}: read {parsed.get(field)!r}, label says {answer[field]!r}")

    shop = answer.get("shop")
    if not warnings:  # approved automatically
        if answer["expect"] == "review":
            problems.append(f"should have gone to review ({answer.get('reason', '')})")
        if shop == "unknown":
            problems.append("approved, but the right product is unknown, so it can't be checked")
        elif shop is None:
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
    if shop not in (None, "unknown"):
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
