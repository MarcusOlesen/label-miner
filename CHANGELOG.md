# Changelog

## Unreleased
- The new label design (left-aligned, "OUTLET · VARENR.", "ORDRE NR.", "Antal" and "Stand") is
  read. Both designs are supported side by side: each photo's design is told by the words on the
  label (VARENR. or ORDRE NR. in capitals mean the new one). The new design's lines are those that
  start inside the area its words mark out, measured along the label's own lines, so box print,
  handwriting and a tilted, curved or sideways label are handled. Old labels are read as before.
- "Antal" is read and becomes STK in the export. Photos of the same item (number, order number,
  STAND) still make one row; STK is the largest Antal among them, never the sum, as Antal is the
  whole order line's quantity. Old labels without Antal count one piece per photo, as before.
  The review window has an Antal field; a new label without a readable Antal is reviewed.
- An "à" in the name is read as "å" (RapidOCR reads "Blå" as "Blà").
- The 4-11 batch (28 new-design labels, 1 old) is in the test photos: every field of all 29 labels
  is read right, 3 are approved automatically (2 new, 1 old) and nothing wrong is approved.
- The label guide shows the new design.
- "Søg på hjemmesiden igen" with only an item number typed (no name) now fills in the fields: a
  product with one variant fills in name, price and link; several variants are all offered as
  cards. Before, an empty name matched nothing, so nothing was filled in. Such a result is only a
  suggestion - the label still needs "Godkend" - and a part of an item still gets no price.
- When the webshop refuses a search for being one too many (HTTP 429), the message says so and to
  wait a minute and search again, instead of a bare error. The review window shows why a search
  failed, under the button.

## 2.2 - 2026-11-02
- Clicking a row that waits for review opens that label, not the next one in line.
- OCR runs on RapidOCR directly instead of through Docling: no PyTorch, no layout model and no
  model download (the models ship inside the rapidocr package), so the install and the installer
  are much smaller, the app starts in seconds instead of about a minute and a half, and each photo
  is read about 4x faster.
- Finding the label among RapidOCR's text: a line the OCR splits in two pieces is joined again,
  and tilted print on the box is no longer taken for part of the label (it once ended up in the
  order number). Each line of text is its own block; the label is still picked out by its centred
  lines, with the centre-line limit retuned to 0.12 label widths.
- An order number that isn't a number (also "#DE 2021"), FRA LAGER or LAGERVARE - for example
  "Uden ordre nr." - is sent to review.
- Only part of an item ("1/2") is approved automatically when the webshop confirms the item by
  its number and name, like any other label: it gets the shop's name and link, and "OBS: delvis
  vare (del 1/2)" instead of a price. Other parts are still reviewed.
- Measured on 195 real photos against 2.1: nothing wrong is approved, 8 more labels are approved
  automatically and 3 more are reviewed.
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
