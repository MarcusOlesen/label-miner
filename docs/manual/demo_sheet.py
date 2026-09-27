"""Draws a Google-Sheets-style picture of the shop's spreadsheet from demo data.

The manuals explain how rows are copied out of (and pasted into) the sheet.
A screenshot of the real sheet would publish real order numbers, so this
draws a look-alike from made-up rows instead - and because it is drawn, the
position of every cell is known exactly for placing the manual's callouts.
"""

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

HEADERS = [
    "Varenummer", "Varenavn", "MK Varenummer", "MK ordrenr", "STAND", "STK", "Modtaget",
    "Vejledende priser", "Pris pr stk", "Pris samlet",
]  # fmt: skip
COLUMN_WIDTHS = [112, 336, 150, 108, 64, 45, 90, 305, 124, 128]

# Product names and item numbers are the webshop's public catalogue data; the
# order numbers are made up.
DEMO_ROWS = [
    # varenr, name, MK number, order no., stand, stk, comment / recommended price, price
    ("1234", "Vika spisebordsstol, egetræ | stof", "106753", "200101", "B", "1", "699 kr (999 kr)", "450"),
    ("1235", "Marmoris spisebord, keramik | 200x100 Pandora/Hvid", "101388", "200102", "B", "1",
     "4.999 kr (6.999 kr)", "2.500"),
    ("1236", "Leonora spisebordsstol, armlæn | Røget egetræ", "103291", "200103", "B", "1",
     "1.499 kr (1.999 kr)", "800"),
    ("1237", "Undra tæppe, 250x350 Beige", "VN 19987-330", "200104", "B", "1", "3.499 kr", "1.800"),
    ("1238", "Plush spisebordsstol, Kobber", "107154", "200105", "B", "1", "1.499 kr", "700"),
    ("1239", "Leonora spisebordsstol, røget egetræ", "103289", "200106", "C", "1", "1.399 kr", "700"),
    ("1240", "Eden spisebordsstol, Egetræ | Naturflet", "103023", "200107", "A", "2", "1.999 kr", "1.000"),
    ("1241", "Carno spisebord, Ø120", "UE 45990201", "200108", "A", "1", "4.999 kr", "2.500"),
    ("1242", "Sevilla U-sofa, venstrevendt Loop Mørk Beige", "106466", "200109", "B", "1",
     "15.999 kr (21.999 kr)", "9.000"),
    ("1243", "Siesta tæppe, 140x200 Lyseblå/Mørkeblå", "LI 600200049", "200110", "A", "1", "", "900"),
    ("1244", "Vega sofabord, hvid marmor | Ø90", "101339", "200111", "B", "1",
     "3.499 kr (4.499 kr)", "1.800"),
    ("1245", "Lana lænestol, drejefunktion Aragon Beige", "105432", "200112", "B", "1",
     "5.999 kr (7.499 kr)", "3.000"),
    ("1246", "Argilla sofabord, keramik | Ø80 Grå/Sort", "101385", "200113", "A", "1",
     "1.499 kr (2.299 kr)", "800"),
    ("1247", "Vega sofabord, latte marmor | Ø90", "101338_2/2", "200114", "B", "1",
     "OBS: delvis vare (del 2/2)", ""),
    ("1248", "Vega sofabord, latte marmor | 90x90", "96919", "200115", "B", "1",
     "OBS: delvis vare (del 2/2)", ""),
    ("1249", "Venice 3 personers sovesofa, opbevaring", "102105", "LAGER", "A", "1", "8.999 kr", "4.500"),
    ("1250", "Madrid XL chaiselong sofa højrevendt Modesto", "107050", "200116", "B", "1", "15.999 kr",
     "8.000"),
    ("1251", "Plush spisebordsstol, Amiya Bouclé Brun", "103490", "#DE 1001", "A", "1", "1.499 kr", "700"),
]  # fmt: skip

# Google Sheets' look
BAR_H, HEAD_H, ROW_H, INDEX_W = 36, 26, 25, 44
GRID = (226, 227, 227)
HEADER_BG = (248, 249, 250)
SELECT_BG = (232, 240, 254)
SELECT_EDGE = (26, 115, 232)
RED = (230, 16, 26)
YELLOW = (255, 255, 0)
TEXT = (32, 33, 36)


def _font(size: int, bold: bool = False):
    for name in (("arialbd.ttf",) if bold else ("arial.ttf",)) + ("DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


@dataclass
class SheetPicture:
    image: Image.Image
    namebox: tuple[int, int, int, int]
    _col_x: list[int]
    _first_row_y: int

    def cell(self, col: int, row: int, col2: int | None = None, row2: int | None = None):
        """Box of the cell range (0-based column, 1-based sheet row), in pixels."""
        col2 = col if col2 is None else col2
        row2 = row if row2 is None else row2
        y0 = self._first_row_y + (row - 1) * ROW_H
        y1 = self._first_row_y + row2 * ROW_H
        return (self._col_x[col], y0, self._col_x[col2 + 1], y1)


def render(
    rows=DEMO_ROWS, batch="24-9", selected=True, red_without_price=True, batch_price="4500"
) -> SheetPicture:
    """The sheet with a header row, a batch-name row and ``rows`` below it.

    With ``red_without_price``, rows without a price are coloured red as in
    the shop's sheet; with ``selected`` the data rows are shown selected
    (A3 to the last column). ``batch_price`` is what the batch-name row holds
    in column I."""
    col_x = [INDEX_W]
    for width in COLUMN_WIDTHS:
        col_x.append(col_x[-1] + width)
    first_row_y = BAR_H + HEAD_H
    last_row = 2 + len(rows)
    image = Image.new("RGB", (col_x[-1] + 1, first_row_y + (last_row + 2) * ROW_H), "white")
    draw = ImageDraw.Draw(image)
    body, bold, small = _font(15), _font(15, bold=True), _font(13)

    # Name box and formula bar
    namebox = (4, 5, 128, 31)
    draw.rectangle((0, 0, image.width, BAR_H), fill="white")
    draw.line((0, BAR_H - 1, image.width, BAR_H - 1), fill=GRID)
    selection_name = f"A3:J{last_row}" if selected else "A3"
    draw.text((16, BAR_H // 2), selection_name, font=body, fill=TEXT, anchor="lm")
    draw.polygon([(110, 15), (120, 15), (115, 21)], fill=(95, 99, 104))  # name box dropdown
    draw.text((150, BAR_H // 2), "fx", font=_font(16), fill=(128, 134, 139), anchor="lm")
    draw.text((180, BAR_H // 2), rows[0][0] if rows else "", font=body, fill=TEXT, anchor="lm")

    def cell(col, row):
        y = first_row_y + (row - 1) * ROW_H
        return col_x[col], y, col_x[col + 1], y + ROW_H

    sheet_rows = [HEADERS, [batch] + [""] * 7 + [batch_price, ""]]
    sheet_rows += [[v, n, mk, order, stand, stk, "X", comment, price, ""]
                   for v, n, mk, order, stand, stk, comment, price in rows]  # fmt: skip
    sheet_rows += [[""] * 10, [""] * 10]
    for r, values in enumerate(sheet_rows, start=1):
        in_selection = selected and 3 <= r <= last_row
        missing_price = red_without_price and 3 <= r <= last_row and not values[8]
        for c, value in enumerate(values):
            x0, y0, x1, y1 = cell(c, r)
            fill = "white"
            if in_selection:
                fill = SELECT_BG
            if missing_price:
                fill = RED
            if r == 2 and c == 0:
                fill = YELLOW
            draw.rectangle((x0, y0, x1, y1), fill=fill, outline=GRID)
            font = bold if r == 1 else body
            numeric = value.replace(".", "").isdigit() and c in (0, 5, 8)
            colour = "white" if missing_price else TEXT
            if numeric:
                draw.text((x1 - 5, (y0 + y1) // 2), value, font=font, fill=colour, anchor="rm")
            else:
                clipped = value
                while clipped and draw.textlength(clipped, font=font) > x1 - x0 - 8:
                    clipped = clipped[:-1]
                draw.text((x0 + 4, (y0 + y1) // 2), clipped, font=font, fill=colour, anchor="lm")

    # Column letters and row numbers
    for c in range(len(COLUMN_WIDTHS)):
        x0, x1 = col_x[c], col_x[c + 1]
        draw.rectangle((x0, BAR_H, x1, first_row_y), fill=HEADER_BG, outline=GRID)
        draw.text(((x0 + x1) // 2, BAR_H + HEAD_H // 2), "ABCDEFGHIJ"[c], font=small,
                  fill=(95, 99, 104), anchor="mm")  # fmt: skip
    draw.rectangle((0, BAR_H, INDEX_W, first_row_y), fill=HEADER_BG, outline=GRID)
    for r in range(1, len(sheet_rows) + 1):
        _, y0, _, y1 = cell(0, r)
        draw.rectangle((0, y0, INDEX_W, y1), fill=HEADER_BG, outline=GRID)
        draw.text((INDEX_W // 2, (y0 + y1) // 2), str(r), font=small, fill=(95, 99, 104), anchor="mm")

    if selected:
        x0, y0, _, _ = cell(0, 3)
        _, _, x1, y1 = cell(len(COLUMN_WIDTHS) - 1, last_row)
        draw.rectangle((x0, y0, x1, y1), outline=SELECT_EDGE, width=2)
        draw.rectangle((x1 - 4, y1 - 4, x1 + 3, y1 + 3), fill=SELECT_EDGE, outline="white")
    return SheetPicture(image, namebox, col_x, first_row_y)


if __name__ == "__main__":
    render().image.save("demo_sheet.png")
