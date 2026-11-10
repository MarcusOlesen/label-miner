"""Builds the two one-page guides (Danish, portrait A4 - meant to be printed
and hung up):

    build/Fotoguide.pdf   - for the people taking the pictures
    build/Labelguide.pdf  - for the people writing the labels

    python docs/manual/make_guides.py

The examples are fixture photos. Label close-ups are cut out where the
program itself finds the label (label_box, see demo_data.pipeline_results),
and sideways photos are turned the way the program turns them. Same design as
the manual (manual_style.py).
"""

import sys
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import demo_data  # noqa: E402  (sets up the import paths)
import manual_style as M  # noqa: E402
from manual_style import p  # noqa: E402

from label_miner.ocr import load_label_photo  # noqa: E402

OUT_DIR = demo_data.BUILD
IMAGES = demo_data.BUILD / "guide-images"
RESULTS = demo_data.pipeline_results()

PAGE = A4
MARGIN = 40
W = PAGE[0] - 2 * MARGIN
GREEN = colors.HexColor("#2E8B3E")
RED = colors.HexColor("#C0392B")
GOOD_BG = colors.HexColor("#E6F2E8")
BAD_BG = colors.HexColor("#F8E3E1")
MEH_BG = colors.HexColor("#FBF1D6")
VERDICTS = {  # verdict: (mark, colour, background)
    "good": ("✓", "#2E8B3E", GOOD_BG),
    "bad": ("✗", "#C0392B", BAD_BG),
    "meh": ("⚠", "#B8860B", MEH_BG),
}

TITLE = ParagraphStyle("GTitle", parent=M.H1, fontSize=26, leading=31)
CAPTION = ParagraphStyle("Caption", parent=M.CELL, fontSize=9.5, leading=12.5)


def photo(name, crop=None, max_side=900, as_taken=False):
    """A test photo, optionally cut down to (l, t, r, b) fractions or to the
    label the app found ("label"), saved as JPEG for the PDF. Turned the way
    the app shows it (sideways labels upright) - unless as_taken, which shows
    it the way it was photographed."""
    kind = "label" if crop == "label" else "full" if crop is None else "crop"
    out = IMAGES / f"{Path(name).stem}_{kind}{'_taken' if as_taken else ''}.jpg"
    if not out.exists():
        image = load_label_photo(demo_data.PHOTOS / name)
        parsed = RESULTS[name]["parsed"]
        if parsed.get("photo_turn") and not as_taken:
            image = image.rotate(parsed["photo_turn"], expand=True)
        if crop == "label":
            crop = parsed["label_box"]
        if crop:
            w, h = image.size
            image = image.crop(
                (round(crop[0] * w), round(crop[1] * h), round(crop[2] * w), round(crop[3] * h))
            )
        image.thumbnail((max_side, max_side), PILImage.Resampling.LANCZOS)
        IMAGES.mkdir(parents=True, exist_ok=True)
        image.save(out, quality=88)
    return out


CARD_RATIO = 0.8  # photo height / width in the example cards


def square_photo(name, focus=0.5, as_taken=False, size=700):
    """The photo cut to the card shape (CARD_RATIO); `focus` (0-1) places
    the cut along the photo's long side, so every example has the same
    shape and shows what it is about."""
    out = IMAGES / f"{Path(name).stem}_card{round(focus * 100)}{'_taken' if as_taken else ''}.jpg"
    if not out.exists():
        image = load_label_photo(demo_data.PHOTOS / name)
        turn = RESULTS[name]["parsed"].get("photo_turn")
        if turn and not as_taken:
            image = image.rotate(turn, expand=True)
        w, h = image.size
        if h / w > CARD_RATIO:  # taller than the card: keep the width
            crop_h = round(w * CARD_RATIO)
            top = round((h - crop_h) * focus)
            image = image.crop((0, top, w, top + crop_h))
        else:  # wider than the card: keep the height
            crop_w = round(h / CARD_RATIO)
            left = round((w - crop_w) * focus)
            image = image.crop((left, 0, left + crop_w, h))
        image = image.resize((size, round(size * CARD_RATIO)), PILImage.Resampling.LANCZOS)
        IMAGES.mkdir(parents=True, exist_ok=True)
        image.save(out, quality=88)
    return out


def card(path, verdict, text, width, caption_height=44):
    """A square photo over a caption box of fixed height, so all cards in a
    grid are exactly the same size."""
    colour, background = VERDICTS[verdict][1], VERDICTS[verdict][2]
    t = Table(
        [[Image(str(path), width=width, height=width * CARD_RATIO)], [p(text, CAPTION)]],
        colWidths=[width],
        rowHeights=[width * CARD_RATIO, caption_height],
    )
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
                ("BACKGROUND", (0, 1), (-1, 1), background),
                ("LINEABOVE", (0, 1), (-1, 1), 2, colors.HexColor(colour)),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#C9CDD2")),
                ("LEFTPADDING", (0, 0), (-1, 0), 0),
                ("RIGHTPADDING", (0, 0), (-1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                ("LEFTPADDING", (0, 1), (-1, 1), 8),
                ("RIGHTPADDING", (0, 1), (-1, 1), 8),
            ]
        )
    )
    return t


class DrawnLabel(Flowable):
    """The new label design drawn as a printed label: white paper with a
    soft shadow, left-aligned lines, Antal and Stand on the bottom line,
    and numbered markers pointing at each part."""

    LINES = [  # (text lines, marker number); a tuple is a left and a right part
        (["OUTLET · VARENR.", "103469", "Roots sofabord, Ø80"], 1),
        (["ORDRE NR.", "156146"], 2),
        ([("Antal: 1", "Stand: B")], 3),
    ]

    def __init__(self, width=300, font_size=14.5):
        super().__init__()
        self.label_w, self.size = width, font_size
        self.leading = font_size * 1.32
        self.pad = 15
        rows = sum(len(lines) for lines, _ in self.LINES)
        self.label_h = rows * self.leading + (len(self.LINES) - 1) * 4 + 2 * self.pad + 8

    def wrap(self, *_):
        return self.label_w + 44, self.label_h + 6

    def draw(self):
        c = self.canv
        x0, y0 = 44, 6
        c.saveState()
        c.setFillColor(colors.HexColor("#D9DCE0"))  # shadow
        c.roundRect(x0 + 3, y0 - 4, self.label_w, self.label_h, 7, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#BFC4CA"))
        c.setLineWidth(0.8)
        c.roundRect(x0, y0, self.label_w, self.label_h, 7, stroke=1, fill=1)
        y = y0 + self.label_h - self.pad
        left, right = x0 + self.pad, x0 + self.label_w - self.pad
        for lines, number in self.LINES:
            if isinstance(lines[0], tuple):  # the bottom line, below a rule
                y -= 8
                c.setStrokeColor(M.INK)
                c.setLineWidth(0.6)
                c.setDash(3, 2)
                c.line(left, y + 2, right, y + 2)
                c.setDash()
            top = y
            for line in lines:
                y -= self.leading
                c.setFillColor(M.INK)
                c.setFont("UI", self.size)
                if isinstance(line, tuple):
                    c.drawString(left, y + self.leading * 0.25, line[0])
                    c.drawRightString(right, y + self.leading * 0.25, line[1])
                else:
                    c.drawString(left, y + self.leading * 0.25, line)
            middle = (top + y) / 2
            r = 10
            c.setStrokeColor(M.ORANGE)
            c.setLineWidth(1)
            c.setDash(2, 2)
            c.line(r * 2 + 4, middle, x0 + 8, middle)
            c.setDash()
            c.setFillColor(M.ORANGE)
            c.circle(r + 2, middle, r, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("UI-B", 11)
            c.drawCentredString(r + 2, middle - 4, str(number))
            y -= 4
        c.restoreState()


def label_card_photo(name, size=700):
    """The label the app found, cut out in the card shape (CARD_RATIO) with
    a little of its surroundings - so label examples line up like the
    Fotoguide cards."""
    out = IMAGES / f"{Path(name).stem}_labelcard.jpg"
    if not out.exists():
        image = load_label_photo(demo_data.PHOTOS / name)
        parsed = RESULTS[name]["parsed"]
        if parsed.get("photo_turn"):
            image = image.rotate(parsed["photo_turn"], expand=True)
        w, h = image.size
        left, top, right, bottom = parsed["label_box"]
        cx, cy = (left + right) / 2 * w, (top + bottom) / 2 * h
        box_w, box_h = (right - left) * w * 0.85, (bottom - top) * h * 0.85
        crop_w = max(box_w, box_h / CARD_RATIO)
        crop_h = crop_w * CARD_RATIO
        crop_w, crop_h = min(crop_w, w), min(crop_h, h)
        x0 = min(max(cx - crop_w / 2, 0), w - crop_w)
        y0 = min(max(cy - crop_h / 2, 0), h - crop_h)
        image = image.crop((round(x0), round(y0), round(x0 + crop_w), round(y0 + crop_h)))
        image = image.resize((size, round(size * CARD_RATIO)), PILImage.Resampling.LANCZOS)
        IMAGES.mkdir(parents=True, exist_ok=True)
        image.save(out, quality=88)
    return out


def headed_card(path, verdict, heading_text, text, width):
    """A card (see card()) with a coloured heading bar above it."""
    heading = ParagraphStyle(
        "CardHead",
        parent=M.H2,
        fontSize=11.5,
        leading=15,
        spaceBefore=0,
        spaceAfter=0,
        alignment=1,
        textColor=colors.white,
    )
    t = Table([[p(heading_text, heading)], [card(path, verdict, text, width)]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(VERDICTS[verdict][1])),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, 0), 4),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
            ]
        )
    )
    return t


def image(path, width=None, height=None):
    w, h = PILImage.open(path).size
    if height is not None and (width is None or width * h / w > height):
        width = height * w / h
    return Image(str(path), width=width, height=width * h / w)


def example(path, verdict, text, width, height):
    """Photo with a ✓ / ✗ / ⚠ caption bar underneath."""
    mark, colour, background = VERDICTS[verdict]
    caption = p(f'<font color="{colour}"><b>{mark}</b></font> {text}', CAPTION)
    t = Table([[image(path, width, height)], [caption]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 1), (-1, 1), background),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#C9CDD2")),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, 0), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                ("LEFTPADDING", (0, 1), (-1, 1), 6),
                ("TOPPADDING", (0, 1), (-1, 1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
            ]
        )
    )
    return t


def row(cells, gap=10):
    width = (W - gap * (len(cells) - 1)) / len(cells)
    t = Table([cells], colWidths=[width + (gap if i < len(cells) - 1 else 0) for i in range(len(cells))])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-2, -1), gap),
                ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def checklist(title, items, good, width):
    mark = f'<font color="{"#2E8B3E" if good else "#C0392B"}"><b>{"✓" if good else "✗"}</b></font>'
    rows = [[p(f"<b>{title}</b>", M.H2)]] + [[p(f"{mark}&nbsp;&nbsp;{item}", M.CELL)] for item in items]
    t = Table(rows, colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), GOOD_BG if good else BAD_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
            ]
        )
    )
    return t


def build(path, title, kicker, lead, story, footer):
    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(M.ORANGE)
        canvas.rect(0, PAGE[1] - 6, PAGE[0], 6, stroke=0, fill=1)
        canvas.setFont("UI", 8.5)
        canvas.setFillColor(M.MUTED)
        canvas.drawString(MARGIN, 22, footer)
        canvas.restoreState()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(path),
        pagesize=PAGE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=34,
        bottomMargin=40,
        title=title,
        author="Label Miner",
        lang="da-DK",
        displayDocTitle=True,
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="p",
                frames=[
                    Frame(
                        MARGIN,
                        40,
                        W,
                        PAGE[1] - 74,
                        id="f",
                        leftPadding=0,
                        rightPadding=0,
                        topPadding=0,
                        bottomPadding=0,
                    )
                ],
                onPage=page,
            )
        ]
    )
    doc.build([p(kicker.upper(), M.KICKER), Paragraph(title, TITLE), Spacer(1, 2), p(lead, M.LEAD)] + story)
    return path


def fotoguide():
    gap = 14
    col = (W - 2 * gap) / 3
    heading = ParagraphStyle(
        "ColHead",
        parent=M.H2,
        fontSize=12.5,
        leading=16,
        spaceBefore=0,
        spaceAfter=0,
        alignment=1,
        textColor=colors.white,
    )
    columns = [
        (
            "good",
            "✓  Godt",
            [
                (square_photo("24-9/IMG_0276.HEIC", 0.45), "Hele labelen, skarpt og retvendt."),
                (square_photo("15-9/IMG_9161.HEIC", 0.25), "Tæt på – labelen fylder billedet."),
            ],
        ),
        (
            "bad",
            "✗  Undgå",
            [
                (square_photo("24-9/IMG_0280.HEIC", 0.72), "Fragtseddel i billedet – gå tættere på."),
                (square_photo("24-9/IMG_0289.HEIC", 0.5), "For langt væk – teksten på kassen kommer med."),
            ],
        ),
        (
            "meh",
            "⚠  Helst ikke",
            [
                (
                    square_photo("15-9/IMG_9164.HEIC", 0.6, as_taken=True),
                    "På siden – programmet drejer de fleste, ellers tjekkes den i hånden.",
                ),
                (
                    square_photo("24-9/IMG_0267.HEIC", 0.18),
                    "Andet klistermærke over labelen – kan ikke altid undgås.",
                ),
            ],
        ),
    ]
    # Five columns - card, gap, card, gap, card - so the coloured headings
    # stop exactly at the card edges
    header = []
    cards = [[], []]
    for i, (verdict, title, examples) in enumerate(columns):
        if i:
            header.append("")
            cards[0].append("")
            cards[1].append("")
        header.append(p(title, heading))
        for r, (path, text) in enumerate(examples):
            cards[r].append(card(path, verdict, text, col))
    grid = Table([header, cards[0], cards[1]], colWidths=[col, gap, col, gap, col])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), gap - 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 0),
    ]
    for i, (verdict, _, _) in enumerate(columns):
        style.append(("BACKGROUND", (2 * i, 0), (2 * i, 0), colors.HexColor(VERDICTS[verdict][1])))
    grid.setStyle(TableStyle(style))

    item_style = ParagraphStyle("Item", parent=M.CELL, spaceAfter=4)

    def items(mark, colour, texts):
        return [
            p(f'<font color="{colour}"><b>{mark}</b></font>&nbsp;&nbsp;{text}', item_style) for text in texts
        ]

    half = (W - gap) / 2
    do = [p("Gør sådan", M.H2)] + items(
        "✓",
        "#2E8B3E",
        [
            "Få <b>hele labelen</b> med – alle fire kanter.",
            "Gå <b>tæt på</b>, så labelen fylder det meste af billedet.",
            "Hold telefonen <b>retvendt</b> og lige på labelen – ikke på siden eller skråt.",
            "Hold telefonen stille, og <b>tryk på labelen</b> på skærmen, så den er skarp.",
            "<b>Ét billede pr. vare.</b> To billeder af samme vare tælles som STK = 2 "
            "(med <i>STK = labelens Antal</i> slået til: ét billede pr. label).",
        ],
    )
    avoid = (
        [p("Undgå", M.H2)]
        + items(
            "✗",
            "#C0392B",
            [
                "<b>Fragtsedler og tekst på kassen</b> i billedet – gå tættere på.",
                "<b>Genskin</b> fra plastik eller lys hen over teksten.",
                "At labelen er <b>krøllet, foldet eller dækket</b> – ret den ud først.",
                "Billeder, hvor en del af labelen er <b>skåret af</b>.",
                "Mørke eller <b>uskarpe</b> billeder.",
            ],
        )
        + [
            Spacer(1, 4),
            p(
                "Et dårligt billede betyder, at varen skal tjekkes i hånden bagefter – "
                "tag hellere et nyt med det samme.",
                CAPTION,
            ),
        ]
    )
    lists = Table([[do, "", avoid]], colWidths=[half, gap, half])
    lists.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, 0), GOOD_BG),
                ("BACKGROUND", (2, 0), (2, 0), BAD_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story = [
        grid,
        Spacer(1, 16),
        lists,
    ]
    return build(
        OUT_DIR / "Fotoguide.pdf",
        "Sådan fotograferer du labels",
        "Guide til billeder",
        "Label Miner læser labels på billederne af sig selv. Et godt billede betyder, at "
        "varen er klar uden ekstra arbejde.",
        story,
        "Fotoguide  ·  Label Miner",
    )


def labelguide():
    gap = 14
    drawing = DrawnLabel(width=280, font_size=13.5)
    explain = M.callouts(
        [
            (
                1,
                "<b>OUTLET · VARENR.</b> øverst, så <b>nummeret</b> på sin egen linje og "
                "<b>varenavnet</b> under – gerne med farven, og gerne over to linjer.",
            ),
            (2, "<b>ORDRE NR.</b> og under det MK-ordrenummeret (eller fx <i>Lager</i>)."),
            (
                3,
                "Under stregen: <b>Antal:</b> og et tal til venstre, <b>Stand:</b> og ét bogstav "
                "– A, B eller C – yderst til højre.",
            ),
        ],
        W - drawing.wrap()[0] - 20,
    )
    top = Table([[drawing, explain]], colWidths=[drawing.wrap()[0] + 20, W - drawing.wrap()[0] - 20])
    top.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    how = M.note(
        "<b>Sådan læser programmet labelen:</b> ordene <b>VARENR.</b>, <b>ORDRE NR.</b>, <b>Antal</b> "
        "og <b>Stand</b> viser, hvor hvert felt står. Gamle, "
        "centrerede labels (OUTLET og STAND uden VARENR.) læses stadig."
    )

    # Rules as green/red pairs: what the program relies on / what breaks it
    half = (W - gap) / 2
    head = ParagraphStyle(
        "PairHead", parent=M.H2, fontSize=12, leading=15, spaceBefore=0, spaceAfter=0, textColor=colors.white
    )
    item = ParagraphStyle("PairItem", parent=M.CELL, fontSize=10, leading=13)
    pairs = [
        (
            "Ordene <b>OUTLET · VARENR.</b>, <b>ORDRE NR.</b>, <b>Antal</b> og <b>Stand</b> står "
            "på labelen, stavet sådan.",
            "Et af ordene mangler, er stavet anderledes eller er dækket – så skal labelen tjekkes i hånden.",
        ),
        (
            "<b>Antal:</b> og hele antallet på ordrelinjen, fx 24 stole.",
            "Intet tal efter Antal – så skal antallet skrives i hånden.",
        ),
        ("<b>Stand:</b> og ét bogstav.", "»Stand: AP« eller ingen Stand."),
        (
            "<b>Nummeret</b> – det fortæller både varen og farven på hjemmesiden.",
            "Står farven ikke i navnet, bruges nummerets farve – et forkert nummer giver forkert farve.",
        ),
        (
            "Kun en del af varen: <b>_1/2</b> lige efter nummeret, fx <i>101338_1/2</i>.",
            "Delen skrevet sidst i navnet – så bliver den ikke fanget.",
        ),
    ]
    rows = [
        [p("✓&nbsp;&nbsp;Det bygger programmet på", head), "", p("✗&nbsp;&nbsp;Det giver problemer", head)]
    ]
    for good, bad in pairs:
        rows.append([p(good, item), "", p(bad, item)])
    rules = Table(rows, colWidths=[half, gap, half])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (0, 0), colors.HexColor(VERDICTS["good"][1])),
        ("BACKGROUND", (2, 0), (2, 0), colors.HexColor(VERDICTS["bad"][1])),
        ("BACKGROUND", (0, 1), (0, -1), GOOD_BG),
        ("BACKGROUND", (2, 1), (2, -1), BAD_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (1, 0), (1, -1), 0),
        ("RIGHTPADDING", (1, 0), (1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
    ]
    for r in range(1, len(rows) - 1):  # thin white dividers between the pairs
        style.append(("LINEBELOW", (0, r), (0, r), 1.5, colors.white))
        style.append(("LINEBELOW", (2, r), (2, r), 1.5, colors.white))
    rules.setStyle(TableStyle(style))

    col = (W - 2 * gap) / 3
    examples = Table(
        [
            [
                headed_card(
                    label_card_photo("4-11/IMG_0978.JPG"),
                    "good",
                    "✓  Som den skal være",
                    "Nummer, navn, ordrenummer, Antal og Stand – godkendt af sig selv.",
                    col,
                ),
                "",
                headed_card(
                    label_card_photo("4-11/IMG_0974.JPG"),
                    "good",
                    "✓  Uden farve",
                    "Nummeret fortæller farven (Taupe) – godkendt af sig selv.",
                    col,
                ),
                "",
                headed_card(
                    label_card_photo("4-11/IMG_0983.JPG"),
                    "good",
                    "✓  Gammel udgave",
                    "Centreret, med OUTLET og STAND – læses stadig.",
                    col,
                ),
            ]
        ],
        colWidths=[col, gap, col, gap, col],
    )
    examples.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    story = [top, Spacer(1, 10), how, Spacer(1, 10), rules, Spacer(1, 12), examples]
    return build(
        OUT_DIR / "Labelguide.pdf",
        "Sådan skal en label se ud",
        "Guide til labels",
        "Label Miner læser labelen ud fra dens faste opbygning. Så længe den holdes, er "
        "varen klar uden ekstra arbejde.",
        story,
        "Labelguide  ·  Label Miner",
    )


if __name__ == "__main__":
    print("wrote", fotoguide())
    print("wrote", labelguide())
