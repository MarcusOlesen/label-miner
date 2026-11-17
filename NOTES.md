# Label Miner: notes (15 November 2026, version 2.3)

Notes from reading the whole codebase at commit `000e2f6`. The test suite passed (153 tests, about
100 s) and `ruff check` was clean when these were written.

## What the program does

Label Miner is a Windows desktop app (Tkinter, Danish interface) for a furniture outlet. It turns a
batch of phone photos of price labels into rows for the shop's Google Sheet. The sister program
Label Printer later prints price labels from that sheet.

One batch, start to finish:

1. **Input.** The user picks or drops photos, or a zip downloaded from Google Drive (one folder per
   delivery, such as `24-9`). A zip is extracted to a temp folder that is marked with the process
   id, so another running copy never deletes it.
2. **OCR** (`ocr.py`). Each photo is turned upright from its EXIF tag, scaled to at most 2500 px and
   read once by RapidOCR. Text pieces on the same printed line are joined again.
3. **Finding the label** (`ocr.py`, `designs.py`). A photo also holds shipping labels, box print and other
   stickers. Only the label's own lines are kept, using its layout:
   - the old design has every line centred around the words OUTLET and STAND;
   - the new design (November 2026) is left-aligned, and the words VARENR., ORDRE NR., Antal and
     Stand mark out its area.
   A sideways label is detected from the same geometry and read a second time, turned upright.
4. **Parsing** (`designs.py`, `parsing.py`). The text is split into item number, product name, order number,
   STAND (A, B or C) and, on the new design, Antal. It handles MK numbers, parts of an item
   (`101338_1/2`), supplier codes (`UE 45990201`) and a few OCR slips that are safe to undo.
5. **Webshop lookup** (`shop.py`). The Shopify store's whole catalogue (moebelkompagniet.dk,
   about 730 products in three requests) is downloaded once per batch, and each label is matched
   against it locally. A price is filled in only when it is certain: the item number is the SKU of
   a variant, and every word of the label's name is in that variant's name. Parts of an item never get a price. Anything else becomes a question
   with candidate products.
6. **Review** (`review.py`, `ui/review_window.py`). Each doubtful label gets one plain question,
   the photo zoomed in on the label, and the webshop's candidates as picture cards. A product the
   user picked before for the same number and name comes first, already selected (`choices.py`,
   stored in `%APPDATA%\Label Miner\choices.json`). Nothing is approved without "Godkend".
7. **Export** (`batch.py`, `ui/app.py`). When nothing is left to review, an `.xlsx` is saved next
   to the zip without a dialog, and saved again whenever a row is edited afterwards. Photos with
   the same number, order number and STAND become one row with STK counted (one per photo, or the
   label's Antal as a setting). "Kopiér resultat" puts the rows on the clipboard for pasting into
   the sheet.

Up to three photos are processed at once on worker threads that share one OCR engine and one
webshop cache.

Around the app:

- **Tests.** OCR results and webshop responses for the fixture photos are recorded, so the real
  pipeline runs without the OCR engine or the internet. A regression test scores every fixture
  photo against a hand-checked answer key; no wrong label may be approved automatically.
- **Packaging.** PyInstaller plus an Inno Setup per-user installer, with the OCR models bundled.
- **Manual.** `docs/manual/` generates a Danish PDF manual, a photo guide, a label guide and a
  practice pack.
- **CI.** GitHub Actions runs lint and tests on Windows with Python 3.12 and 3.13.

## Possible improvements

Everything below comes from reading the code. None of the bugs was reproduced by running the app.

### Bugs and risks to the data

Items 1, 2 and 5 were fixed on 15 November 2026: a search now
only fills in the fields, like picking a card, and the record is written on "Godkend".

1. **"Søg på hjemmesiden igen" changes the record before "Godkend"**
   (`ui/review_window.py:618`, `_apply_price_refresh`). The search writes name, price, link,
   candidates and warnings straight into the record. The typed number is not written. If the user
   corrects the number, searches, then skips without saving, the export row has the old number
   with the new product's name and price, and the shop warnings are gone. This also contradicts
   the rule that nothing is saved until "Godkend".
2. **The same search throws away what the user typed** (`ui/review_window.py:646`). After a search
   the name and price fields are reset from the record. A corrected name that gives no certain
   match is replaced by the old OCR text, and a price typed by hand is erased. A failed search
   (HTTP 429) also wipes a price and candidates that were found earlier.
3. **Closing the main window loses the batch without asking** (`ui/app.py:889`). There is no
   confirmation and no saved session. The Excel file is only written once every review is done, so
   closing (or a crash) part-way through a long review loses all of it.
4. **Merged rows drop warnings** (`batch.py:210`). A row for several photos of one item takes its
   name, price and warnings from the first photo only. A skipped label's warning is lost from the
   comments column when an earlier photo of the same item was fine.
5. **The search button changes its name** (`ui/review_window.py:620`). After the first search it
   reads "Genindlæs pris", while the 429 message tells the user to press "Søg på hjemmesiden igen".
6. **A hand-edited `choices.json` can make every matching photo fail** (`choices.py:59`). Only
   `url` and `variant_id` are checked on load; a missing `name` or `count` raises a `KeyError`
   later inside the pipeline.
7. **Antal "0" is accepted** in the review window (`ui/review_window.py:868`) and then counted as 1.

### Webshop rate limit

Done on 17 November 2026: the app downloads the whole catalogue
once per batch instead of searching, so a batch makes three requests instead of hundreds.

8. The shop allows a burst and then about one search a minute (comment in `shop.py:91`). There is
   no throttling, no retry and no cache between runs. Once the limit is hit, every remaining photo
   goes to review with a "shop" warning, and the user must search again label by label. Options: a
   small on-disk product cache with an expiry, a pause that honours `Retry-After`, or one button
   that repeats all failed lookups.

### Usability

9. **One batch per launch.** A second batch means restarting the app and loading the OCR models
   again. The data folder shows several deliveries downloaded at once, and picking several zips
   uses only the first.
10. **No log file.** Diagnostics only go to the console with `--debug`. The installed app has no
    console, so a problem at the shop cannot be diagnosed afterwards. A small rotating log in
    `%APPDATA%\Label Miner` would fix that.
11. **Zip extraction and photo loading run on the UI thread** (`ui/app.py:424`,
    `ui/review_window.py:755`). The window freezes briefly on a large zip. The review photo is
    decoded at full resolution and scaled down again on every resize and rotation-slider step;
    keeping a screen-sized copy would make that smoother.
12. **No keyboard shortcuts in the review window** (Enter for "Godkend", number keys for the cards).
    Reviewing 20 labels is all mouse work today.

### Code health

Item 13 was done on 15 November 2026: the records are
dataclasses in `records.py`, and each label design is one class in `designs.py`.

13. **Records are untyped dicts.** `parsed` mixes label fields, the price, and display hints
    (`photo_turn`, `label_box`). A dataclass would make the fields and their owners clear.
14. **`review.py` reads meaning out of Danish message text** (`name in message`,
    `"STAND" in message`). The warning could carry the field key.
15. **`ui/app.py` comments have drifted.** Some name things that were renamed ("Prøv igen",
    `_check_review_queue's finally block`), and the review bookkeeping (`review_total`,
    `review_completed`, `review_pending`, `open_standalone_windows`) is spread over many methods.
    A small class that owns the queue would be easier to trust.
16. **The version is written in three places** (`pyproject.toml`, `__init__.py`,
    `packaging/installer.iss`). `pyproject.toml` can read it from the package.
17. **One test takes a third of the run.** The setup of `test_regression.py` takes about 34 s of
    the roughly 100 s total.
18. **CI tests 3.12 and 3.13 only**, while `requires-python` says 3.10 and up.

### Housekeeping

19. `docling-models/` (358 MB) is left from the Docling era, is untracked and is not in
    `.gitignore`. The app no longer uses Docling.
20. The installer is not code-signed, so Windows SmartScreen warns on install. There is no release
    workflow; the installer is built by hand.
