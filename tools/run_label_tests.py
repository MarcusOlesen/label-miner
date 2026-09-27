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

from harness import CachedConverter, CachedSession, score  # noqa: E402
from label_miner.pipeline import extract_label  # noqa: E402

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
    parser.add_argument("--only", default="", help="only photos whose name contains this")
    parser.add_argument("-v", "--verbose", action="store_true", help="show every photo, not only problems")
    args = parser.parse_args()
    if not args.photos:
        parser.error("give the photo folder, or set LABEL_MINER_PHOTOS")

    photo_root = Path(args.photos)
    answers_path = Path(args.answers) if args.answers else photo_root / "answers.json"
    cache = Path(args.cache) if args.cache else photo_root / ".label-miner-cache"
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    names = [name for name in answers if not name.startswith("_") and args.only in name]

    converter = CachedConverter(cache / "ocr", live=args.record_ocr)
    session = CachedSession(cache / "shop", live=args.refresh_shop)
    shop_cache, lock = {}, threading.Lock()
    counts = Counter()
    not_offered = []
    started = time.perf_counter()

    for name in names:
        answer = answers[name]
        text, parsed, warnings, candidates = extract_label(
            converter, photo_root / name, session, shop_cache, lock
        )
        verdict, problems = score(answer, parsed, warnings, candidates)
        counts[verdict] += 1
        if any("not among the suggestions" in problem for problem in problems):
            not_offered.append(name)
        if problems or args.verbose or verdict == "WRONG":
            print(f"\n{verdict:9} {name}")
            print(f"          text: {text}")
            print(
                f"          read: {json.dumps({k: parsed.get(k) for k in SHOWN_FIELDS}, ensure_ascii=False)}"
            )
            for _, message in warnings:
                print(f"          ! {message}")
            for problem in problems:
                print(f"          x {problem}")

    reviews = counts["REVIEW"] + counts["REVIEW ok"]
    print("\n" + "=" * 60)
    print(f"Photos: {len(names)}   OCR runs: {converter.ocr_runs}   "
          f"webshop requests: {session.requests}   {time.perf_counter() - started:.0f} s")  # fmt: skip
    print(f"WRONG but auto-approved: {counts['WRONG']}   (must be 0)")
    print(f"Sent to review:          {reviews}   ({counts['REVIEW']} of them unnecessary)")
    print(f"Correct, auto-approved:  {counts['AUTO ok']}")
    print(f"Reviews where the right product was NOT offered: {len(not_offered)}")
    return 1 if counts["WRONG"] else 0


if __name__ == "__main__":
    sys.exit(main())
