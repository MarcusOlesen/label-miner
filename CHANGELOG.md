# Changelog

## Unreleased
- Clicking a row that waits for review opens that label, not the next one in line.
- OCR runs on RapidOCR directly instead of through Docling: no PyTorch, no layout model and no
  model download (the models ship inside the rapidocr package), so the install and the installer
  are much smaller and the app starts faster. Each line of text is now its own block; the label is
  still picked out by its centred lines, with the centre-line limit retuned to 0.12 label widths.
  The fixture photos score as before; the larger private photo set needs a new
  `tools/run_label_tests.py --record-ocr` run.
- Code: clearer names in `ocr.py` and `shop.py`, and functions used only inside their own module
  start with `_`.

## 2.1 - 2026-09-25
- A Danish user manual, generated from screenshots of the real app, opened by "? Hjælp" buttons on
  the start screen, the main window and the review window. The installer includes it.
- The code is split into a package (`src/label_miner`), with offline tests, CI and a documented
  build.

## 2.0.1 - 2026-09-25
- At startup, extraction folders left in `%TEMP%` by sessions that didn't close normally are removed.
  Each session marks its folder with its process id, so a folder that a running Label Miner still
  uses is never touched.

## 2.0 - 2026-09-25
- The version number is shown in the title bar.
- Photos or a zip given on the command line (for example dropped onto the desktop icon) are
  processed right away.
- A new Danish installer that closes a running Label Miner and cleans up after v1.0.

## 2.0 beta 2-5 - 2026-09-24
Feedback from the first user test:
- Clicking a chosen suggestion again undoes the choice.
- No automatic pop-up: a "Gennemse labels (N)" button opens the review window, and closing it keeps
  the current label and its edits for later.
- The result list no longer jumps while photos are read.
- "1/2", "2/2" mean *only part of the item*: never priced, always reviewed, noted as
  "OBS: delvis vare (del 1/2)".
- Root-cause fix for a wrong zoom: one Docling text item can combine pieces of text from different
  places in the photo, and only the first piece's position was used. Every piece is now its own
  block.
- Sideways labels are read a second time turned upright (only those photos).

## 2.0 beta - 2026-09-24
A rework, measured against an answer key for 62 real photos
(see [docs/case-study.md](docs/case-study.md)):
- **Reading:** each photo is OCR'd once, and only the text lined up with the label's OUTLET/STAND
  lines is kept, in reading order. Stray stickers, box print and sideways labels no longer end up in
  the fields.
- **Parsing:** MK numbers, parts of an item (`101338_1/2`) and supplier codes (`UE 45990201`,
  `VN 19987-330`).
- **Webshop:** search by number, with the name search as a checked fallback; number/name conflicts
  are detected; no guessed prices.
- **Review window:** one plain question per label, product picture cards, a single green "Godkend"
  button, photo tools under the photo, and a zoom on the label.
- **Result:** 20 of 62 photos to review (was 38), 42 correct automatic approvals, 0 wrong ones.

## 1.1 - 2026-09-24 (experiment, reverted)
- Auto-crop: find the white label with OpenCV and OCR only the crop. It removed stray text but
  clipped some labels and scrambled the line order on others: 40 of 62 to review, worse than 1.0.
  Replaced by the approach in 2.0.

## 1.0 - 2026-09-18
- First version in use: Docling/RapidOCR reads each label photo, regex parsing, webshop lookup,
  review window and Excel export. 38 of 62 test photos needed review; 0 wrong automatic approvals.
