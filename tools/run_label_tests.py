"""Run the pipeline over a folder of label photos and score it against an answer key.

The pytest suite does this for the fixture photos in tests/fixtures. This
script is for larger, private photo sets that don't belong in the repository:

    python tools/run_label_tests.py D:/label-photos --answers D:/label-photos/answers.json

Photo names in the answer key are relative to the photo folder (e.g.
"24-9/IMG_0276.HEIC"); see tests/fixtures/answers.json for the format. The
folder can also be given as the LABEL_MINER_PHOTOS environment variable.

OCR results and webshop responses are recorded in --cache (default:
<photos>/.label-miner-cache) so that later runs take seconds:

    --record-ocr    OCR photos that aren't recorded yet (needs the ocr extra)
    --refresh-shop  ask the live webshop again instead of the recorded answers

Exits with status 1 if any label was approved automatically although wrong.

Reading the labels only (--labels-only): the webshop isn't used at all -
not even recordings - and only what the label says is compared: the item
number, name, order number, STAND, Antal (new design), and whether the label
itself has to be reviewed. This is the check to run after any change to how
labels are read (OCR, line order, parsing), on every photo there is:

    python tools/run_label_tests.py data/labels --labels-only

    --add-new       add photos that aren't in the answer key yet, with what
                    is read now and "checked": false - look them over, fix
                    what is wrong, and set "checked" to true

Exits with status 1 if any label is read differently from the answer key.
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from harness import CachedOcrEngine, CachedSession, _compact, score  # noqa: E402
from label_miner.config import SUPPORTED_EXTENSIONS  # noqa: E402
from label_miner.pipeline import extract_label  # noqa: E402
from label_miner.shop import _tokens_match, name_tokens  # noqa: E402

SHOWN_FIELDS = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "price")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("photos", nargs="?", default=os.environ.get("LABEL_MINER_PHOTOS"))
    parser.add_argument("--answers", help="answer key (default: <photos>/answers.json)")
    parser.add_argument("--cache", help="recorded OCR/webshop answers (default: <photos>/.label-miner-cache)")
    parser.add_argument("--record-ocr", action="store_true", help="OCR photos that aren't recorded yet")
    parser.add_argument("--refresh-shop", action="store_true", help="ask the live webshop again")
    parser.add_argument("--labels-only", action="store_true", help="no webshop: compare what the labels say")
    parser.add_argument("--add-new", action="store_true", help="with --labels-only: add unknown photos")
    parser.add_argument("--only", default="", help="only photos whose name contains this")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every photo, not only problems")
    args = parser.parse_args()
    if not args.photos:
        parser.error("give the photo folder, or set LABEL_MINER_PHOTOS")

    photo_root = Path(args.photos)
    answers_path = Path(args.answers) if args.answers else photo_root / "answers.json"
    cache = Path(args.cache) if args.cache else photo_root / ".label-miner-cache"
    if args.labels_only:
        return labels_only(photo_root, answers_path, cache, args)
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    names = [name for name in answers if not name.startswith("_") and args.only in name]

    engine = CachedOcrEngine(cache / "ocr", live=args.record_ocr)
    session = CachedSession(cache / "shop", live=args.refresh_shop)
    shop_cache, lock = {}, threading.Lock()
    counts = Counter()
    not_offered = []
    started = time.perf_counter()

    for name in names:
        answer = answers[name]
        record = extract_label(engine, photo_root / name, session, shop_cache, lock)
        verdict, problems = score(answer, record)
        counts[verdict] += 1
        if any("not among the suggestions" in problem for problem in problems):
            not_offered.append(name)
        if problems or args.verbose or verdict == "WRONG":
            print(f"\n{verdict:9} {name}")
            print(f"          text: {record.raw_text}")
            read = {k: v for k, v in record.editable_fields().items() if k in SHOWN_FIELDS}
            print(f"          read: {json.dumps(read, ensure_ascii=False)}")
            for _, message in record.warnings:
                print(f"          ! {message}")
            for problem in problems:
                print(f"          x {problem}")

    reviews = counts["REVIEW"] + counts["REVIEW ok"]
    print("\n" + "=" * 60)
    print(f"Photos: {len(names)}   OCR runs: {engine.ocr_runs}   "
          f"webshop requests: {session.requests}   {time.perf_counter() - started:.0f} s")  # fmt: skip
    print(f"WRONG but auto-approved: {counts['WRONG']}   (must be 0)")
    print(f"Sent to review:          {reviews}   ({counts['REVIEW']} of them unnecessary)")
    print(f"Correct, auto-approved:  {counts['AUTO ok']}")
    print(f"Reviews where the right product was NOT offered: {len(not_offered)}")
    return 1 if counts["WRONG"] else 0


# What a label says, as compared by --labels-only
LABEL_ONLY_FIELDS = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "antal")


class NoShop:
    """A webshop session that must never be used (see --labels-only)."""

    def get(self, *args, **kwargs):
        raise AssertionError("--labels-only must not ask the webshop")


def read_label(engine, path: Path) -> dict:
    """What the pipeline reads on a label, without the webshop: its fields,
    its design and whether the label itself goes to review."""
    record = extract_label(engine, path, NoShop(), {}, threading.Lock(), shop_lookup=False)
    reading = {field: getattr(record.label, field) for field in LABEL_ONLY_FIELDS}
    reading["design"] = record.design
    reading["review"] = bool(record.warnings)
    return reading


def same_name(read: str, says: str) -> bool:
    """The names have the same words, allowing the OCR slips the webshop
    matching allows too ("1200x100" for "| 200x100") - the name is only
    ever used to find the product."""
    read_tokens, said_tokens = name_tokens(read), name_tokens(says)
    # The read word is always the first one: the slips are the OCR's
    return all(any(_tokens_match(word, said) for said in said_tokens) for word in read_tokens) and all(
        any(_tokens_match(word, said) for word in read_tokens) for said in said_tokens
    )


def label_differences(answer: dict, reading: dict) -> list[str]:
    differences = []
    for field in LABEL_ONLY_FIELDS:
        if field not in answer:
            continue
        if field == "varenavn":
            same = same_name(reading[field], answer[field])
        else:
            same = _compact(reading[field]) == _compact(answer[field])
        if not same:
            differences.append(f"{field}: read {reading[field]!r}, answer {answer[field]!r}")
    for key in ("design", "review"):
        if key in answer and reading[key] != answer[key]:
            differences.append(f"{key}: {reading[key]!r}, answer {answer[key]!r}")
    return differences


def labels_only(photo_root: Path, answers_path: Path, cache: Path, args) -> int:
    answers = json.loads(answers_path.read_text(encoding="utf-8")) if answers_path.exists() else {}
    engine = CachedOcrEngine(cache / "ocr", live=args.record_ocr)
    photos = sorted(
        path.relative_to(photo_root).as_posix()
        for path in photo_root.rglob("*")
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and ".label-miner-cache" not in path.parts
    )
    photos = [name for name in photos if args.only in name]
    started = time.perf_counter()
    differing, unchecked, reviews, added = [], 0, 0, 0
    for name in photos:
        reading = read_label(engine, photo_root / name)
        reviews += reading["review"]
        answer = answers.get(name)
        if answer is None:
            if args.add_new:
                answers[name] = {**reading, "checked": False}
                added += 1
            else:
                print(f"\nNOT IN KEY {name}  (--add-new adds it)")
            continue
        unchecked += not answer.get("checked", True)
        differences = label_differences(answer, reading)
        if differences:
            differing.append(name)
        if differences or args.verbose:
            print(f"\n{'DIFFERENT' if differences else 'same':9} {name}")
            for difference in differences:
                print(f"          x {difference}")
    if added:
        answers_path.write_text(json.dumps(answers, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"Photos: {len(photos)}   OCR runs: {engine.ocr_runs}   {time.perf_counter() - started:.0f} s")
    print(f"Read differently from the answer key: {len(differing)}")
    print(f"Labels that go to review on their own: {reviews}")
    if unchecked:
        print(f"Answers not checked by hand yet: {unchecked}")
    if added:
        print(f"Added to the answer key (check them): {added}")
    return 1 if differing else 0


if __name__ == "__main__":
    sys.exit(main())
