# Case study: from 38 to 20 photos in review, with zero wrong approvals

This is the engineering story behind Label Miner 2.0: how a working but tiring tool was reworked
by measuring first, and why some ideas that sounded good were dropped.

## The problem

A furniture outlet receives returned and damaged items. Each item carries a printed label:

```
106466 - Sevilla U-sofa, venstrevendt
Loop Mørk Beige
OUTLET
142415
STAND: B
```

That is an item number, the product name, an order number and a condition grade. Someone
photographs every label, and the photos end up in the shop's Google Sheet together with the
webshop price. Label Miner 1.0 automated this: OCR each photo, parse the fields, look up the price
on the webshop and export a spreadsheet. Anything it wasn't sure about went to a review window.

It worked, but **38 of 62 photos in a test batch still needed a human**, and many of those
reviews were unnecessary. The person using it every day is not a programmer, so every review
costs real effort.

## The hard rule

The goal was fewer reviews, but not at any cost:

> An unnecessary review is annoying. A wrong price that gets approved automatically is not
> acceptable.

Every change below was judged against that rule first.

## Step 0: measure before changing anything

Two batches of real photos (62 in total) were turned into an **answer key**. For each photo it
records the correct fields, whether the photo *should* go through automatically or to review (and
why), and the exact webshop variant. It was cross-checked against the spreadsheets the user had
already approved by hand. They agreed on 40 of 62; the other 22 were explained one by one, and some
of them turned out to be mistakes in the hand-approved sheets.

A harness then ran the real pipeline over all 62 photos and scored each one:

- **WRONG:** approved automatically, but a field or the product is wrong. This must stay at 0.
- **AUTO ok:** approved automatically and correct.
- **REVIEW ok:** sent to review, as the answer key expects.
- **REVIEW:** sent to review unnecessarily.

It also checked whether the right product was among the suggestions shown in the review window.

OCR takes a few seconds per photo and the webshop is a live site, so both are **recorded**. OCR
results are cached per image, keyed by the image's pixels. Any change in what is sent to OCR (crop,
rotation, resize) is then a cache miss instead of a silently stale hit. Webshop responses are cached
per request. A full run takes about 25 seconds, and the same harness runs in CI on the fixture
photos in this repository.

Baseline for 1.0: **0 wrong, 38 in review (18 unnecessary), 24 correct automatic approvals, and
in 21 reviews the right product wasn't even offered.**

## The experiment that failed: auto-crop

The obvious fix for stray text (shipping labels, box print, other stickers) is to find the white
label first and OCR only that. An OpenCV detector did this in about 30 ms per photo, with a fallback
to the whole photo when a field was missing.

Measured: **40 of 62 in review, worse than before.** The crop removed stray text on some photos,
but it clipped labels on others ("Toled venstrever Montreal Be"), and it changed how the OCR engine
grouped the lines, which scrambled their order. The fallback only fired on *missing* fields, not on
truncated ones. The detector also missed about a third of the labels.

It was reverted. It remains in the history as version 1.1, marked as an experiment.

## What worked instead: let the label's layout pick the text

Every label has the same structure: centred lines around the words **OUTLET** and **STAND**. So
instead of cropping the image, the whole photo is OCR'd once. Then only the text blocks that belong
to the label are kept (`ocr.label_blocks_in_reading_order`):

1. Find the anchor blocks, OUTLET and STAND.
2. Grow outwards to blocks that are centred on the same axis and close in line height.
3. Order the kept blocks by their position on the label, not by the OCR engine's order.

This removed the stray text *and* fixed the line order, with no extra OCR. Sideways labels come out
of the same geometry: if the short anchor blocks are much taller than wide, the lines run along the
other axis.

The parser learned the real number formats found in the photos: parts of an item (`101338_1/2`),
supplier codes (`UE 45990201`, `LI 600200049`, and `VN 19987-330`, where the dash belongs to the
number). It also learned the OCR slips that are safe to undo (`090` → `Ø90`).

## Matching the webshop without guessing

A price is only filled in when it is certain. That means **one exact variant, found via the label's
number, that contains every word of the label's name** (`shop.lookup_shop_price`).

Two findings shaped this:

- **The name search always returns something.** When the product isn't on the shop, it suggests
  a near miss: Bali → *Cali*, Siesta 140×200 → Siesta *200×300*. So a product found only by name is
  never approved automatically, and it is only offered at all if it contains every word of the
  label's name.
- **Labels can have the wrong number.** One label read "106839 Ground TV-stander", but 106839 is the
  *Frame* TV stand on the shop. When the number and the name point to different products, the
  program asks, and it offers both.

The word comparison (`name_tokens` / `tokens_match`) tolerates what OCR gets wrong: `|` read as
`1`/`I`, punctuation, quantity tags like "- 6 stk.", and small slips in long words. Numbers and
sizes must match exactly.

## A root cause, not a stop-gap

After the rework, one photo zoomed in on a two-letter scrap of packaging at the edge of the photo
instead of on the label. It also read that packaging text as part of the label. The first fix was a
stop-gap: no zoom when the label area is implausibly small.

The real cause was in how Docling reports text. One text item can combine **several pieces from
different places in the photo**, each with its own box. The code used only the first piece's box,
which put the whole label at the scrap's position. The fix was to make every piece its own block
(`ocr.read_text_blocks`). The stop-gap was then removed. A related change estimates line height from
the character count rather than from a whole multi-line block.

The same investigation explained a garbled product name on one sideways label. The OCR engine had
merged its lines in the wrong order *inside* one block, which no reordering can fix. Sideways labels
are now read a second time, turned upright. That costs one extra OCR for about 1 or 2 photos in 30.

## A review window that is hard to get wrong

The review window was rebuilt around the person using it:

- **One plain question per label** instead of a list of warnings (`review.review_question`), with the
  fields it concerns marked.
- **Product picture cards** for the suggestions. Picking one only fills in the fields; nothing is
  saved until "Godkend".
- **"Godkend" is the only green button.** Tools that change the photo (rotate, zoom, read again) sit
  under the photo, so they can't be mistaken for it.
- **No surprise pop-ups.** A "Gennemse labels (N)" button opens the window when the user is ready.
  Closing it keeps the current label and any edits for later.

These came from watching the first real use, not from guessing up front.

## Result

| | 1.0 | 2.0 |
|---|---|---|
| Wrong, but approved automatically | 0 | **0** |
| Sent to review (of 62) | 38 (18 unnecessary) | **20 (all expected)** |
| Correct, approved automatically | 24 | **42** |
| Reviews where the right product wasn't offered | 21 | **0** |

**Caveat:** these numbers come from the photos the rules were developed on. The next real batch is
the first unseen test, and it belongs in the answer key before the numbers are trusted further.

## Lessons

- **Build the answer key first.** Several "obvious" improvements, such as the auto-crop, made
  things worse, and only the measurement showed it.
- **Make "not sure" cheap and "wrong" impossible.** A confident rule (one variant, found by number,
  every word present) beats a clever score.
- **Fix the cause, then remove the stop-gap.** The stop-gap hid a bug that also affected parsing.
- **Record slow and external dependencies.** Recorded OCR and webshop answers made every change
  testable in seconds, including in CI.
