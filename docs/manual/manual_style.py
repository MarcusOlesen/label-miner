"""Shared look of the Danish user manuals: fonts, colours and building blocks.

Landscape A4 pages that are read on screen: a numbered screenshot next to a
numbered explanation, notes, coloured tables and a clickable table of
contents with page numbers. The manual scripts build their content from
these pieces and call ``build_document``.
"""

from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
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

# Where picture()/framed() read the screenshots from (see use_screenshots)
SHOTS = Path(".")


def use_screenshots(folder: Path) -> None:
    global SHOTS
    SHOTS = Path(folder)


PAGE = landscape(A4)
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 42, 38, 46
CONTENT_W = PAGE[0] - 2 * MARGIN_X

ORANGE = colors.HexColor("#D9622B")
INK = colors.HexColor("#1F2328")
MUTED = colors.HexColor("#5F6670")
RULE = colors.HexColor("#E3E5E8")
TINT = colors.HexColor("#FBEDE6")
RED_BG = colors.HexColor("#F6D9D9")
YELLOW_BG = colors.HexColor("#F6EDC8")
# Coloured tables (colour_table): header colour and cell background per kind -
# the same green/red as the one-page guides
KINDS = {
    "good": (colors.HexColor("#2E8B3E"), colors.HexColor("#E6F2E8")),
    "bad": (colors.HexColor("#C0392B"), colors.HexColor("#F8E3E1")),
    "neutral": (colors.HexColor("#5F6670"), colors.HexColor("#F1F2F4")),
    "accent": (ORANGE, TINT),
}
# Page of each section, recorded while building (ManualDoc.afterFlowable) and
# shown in the table of contents on the second build pass
SECTION_PAGES = {}


# --------------------------------------------------------------------------
# Fonts and styles
# --------------------------------------------------------------------------


def _register_fonts():
    fonts = Path(r"C:\Windows\Fonts")
    for name, file in (("UI", "segoeui.ttf"), ("UI-B", "segoeuib.ttf"), ("UI-I", "segoeuii.ttf")):
        pdfmetrics.registerFont(TTFont(name, str(fonts / file)))
    pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-B", italic="UI-I", boldItalic="UI-B")
    pdfmetrics.registerFont(TTFont("Sym", str(fonts / "seguisym.ttf")))  # see SYMBOLS


_register_fonts()
H1 = ParagraphStyle("H1", fontName="UI-B", fontSize=22, leading=27, textColor=INK, spaceAfter=4)
KICKER = ParagraphStyle("Kicker", fontName="UI-B", fontSize=10, leading=13, textColor=ORANGE, spaceAfter=2)
LEAD = ParagraphStyle("Lead", fontName="UI", fontSize=12.5, leading=18, textColor=MUTED, spaceAfter=12)
BODY = ParagraphStyle(
    "Body", fontName="UI", fontSize=11.5, leading=16.5, textColor=INK, spaceAfter=7, alignment=TA_LEFT
)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=10, leading=14, textColor=MUTED)
H2 = ParagraphStyle(
    "H2", fontName="UI-B", fontSize=13, leading=17, textColor=INK, spaceBefore=6, spaceAfter=5
)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=10.5, leading=14.5, spaceAfter=0)
CELL_B = ParagraphStyle("CellB", parent=CELL, fontName="UI-B")
TOC = ParagraphStyle("Toc", parent=BODY, fontSize=12, leading=21, spaceAfter=0)
TOC_NUM = ParagraphStyle("TocNum", parent=TOC, alignment=TA_RIGHT, textColor=MUTED)
HEAD_WHITE = ParagraphStyle("HeadWhite", parent=CELL_B, textColor=colors.white)


# --------------------------------------------------------------------------
# Building blocks
# --------------------------------------------------------------------------


class Num(Flowable):
    """Orange numbered circle, matching the callouts on the screenshots."""

    def __init__(self, n, size=19):
        super().__init__()
        self.n, self.size = n, size

    def wrap(self, *_):
        return self.size, self.size

    def draw(self):
        c, r = self.canv, self.size / 2
        c.setFillColor(ORANGE)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("UI-B", self.size * 0.58)
        c.drawCentredString(r, r - self.size * 0.2, str(self.n))


def section(title, key, kicker=None, lead=None):
    """Page heading; also becomes a bookmark and a link target."""
    out = []
    if kicker:
        out.append(Paragraph(kicker.upper(), KICKER))
    h = Paragraph(f'<a name="{key}"/>{title}', H1)
    h._outline = (title, key)
    out.append(h)
    out.append(Spacer(1, 4))
    if lead:
        out.append(Paragraph(lead, LEAD))
    return out


SYMBOLS = "✓✗⚠✕"  # not in Segoe UI; drawn with Segoe UI Symbol


def p(text, style=BODY):
    for symbol in SYMBOLS:
        text = text.replace(symbol, f'<font name="Sym">{symbol}</font>')
    return Paragraph(text, style)


def picture(name, width):
    """A screenshot, embedded as a high-quality JPEG: the review window's
    shots contain photos, which as PNG would make the PDF several times
    larger."""
    png = SHOTS / f"{name}.png"
    jpg = SHOTS / "jpg" / f"{name}.jpg"
    if not jpg.exists() or jpg.stat().st_mtime < png.stat().st_mtime:
        jpg.parent.mkdir(exist_ok=True)
        PILImage.open(png).convert("RGB").save(jpg, quality=90, optimize=True)
    w, h = PILImage.open(jpg).size
    img = Image(str(jpg), width=width, height=width * h / w)
    img.hAlign = "LEFT"
    return img


def framed(name, width):
    """Screenshot with a thin border so dark UI shots sit well on white."""
    t = Table([[picture(name, width)]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#C9CDD2")),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    t.hAlign = "LEFT"
    return t


def callouts(items, width):
    """[(n, text)] -> numbered list whose numbers match the screenshot."""
    rows = [[Num(n), p(text, CELL)] for n, text in items]
    t = Table(rows, colWidths=[28, width - 28])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    return t


GAP = 40  # space between a screenshot and the numbered text next to it


def two_col(left, right, left_w, gap=GAP):
    """left_w is the width of the left content itself; the gap is added
    after it. (Padding inside a column doesn't shrink a fixed-width image,
    so the gap has to be part of the column width, not the padding alone.)"""
    t = Table([[left, right]], colWidths=[left_w + gap, CONTENT_W - left_w - gap])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, -1), gap),
                ("RIGHTPADDING", (1, 0), (1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return t


def note(text, bg=TINT):
    t = Table([[p(text, CELL)]], colWidths=[None])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return t


def grid_table(header, rows, col_widths, row_bgs=None):
    data = [[p(h, CELL_B) for h in header]] + [[p(c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, ORANGE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, bg in (row_bgs or {}).items():
        style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
    t.setStyle(TableStyle(style))
    return t


def colour_table(header, rows, col_widths, kinds):
    """A table whose columns are coloured by meaning: kinds[i] is one of
    KINDS for column i (header bar colour + light cell background)."""
    data = [[p(h, HEAD_WHITE) for h in header]] + [[p(c, CELL) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 1.5, colors.white),
        ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white),
    ]
    for i, kind in enumerate(kinds):
        head, background = KINDS[kind]
        style += [("BACKGROUND", (i, 0), (i, 0), head), ("BACKGROUND", (i, 1), (i, -1), background)]
    t.setStyle(TableStyle(style))
    t.hAlign = "LEFT"
    return t


def toc_table(sections, width):
    """Clickable contents with page numbers (filled in on the 2nd build pass)."""
    rows = [
        [
            p(f"{i + 1}.&nbsp;&nbsp;{link(k, title)}", TOC),
            p(f'<a href="#{k}">{SECTION_PAGES.get(k, "")}</a>', TOC_NUM),
        ]
        for i, (k, title) in enumerate(sections)
    ]
    t = Table(rows, colWidths=[width - 36, 36])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.5, RULE, None, (1, 2)),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    t.hAlign = "LEFT"
    return t


def front_title(name, ico_path):
    """The program's icon next to its name, for the front page."""
    icon = PILImage.open(ico_path)
    sizes = icon.info.get("sizes")
    if sizes:
        icon.size = max(sizes)
    png = SHOTS / f"_icon_{Path(ico_path).stem}.png"
    icon.convert("RGBA").save(png)
    t = Table(
        [
            [
                Image(str(png), width=48, height=48),
                Paragraph(name, ParagraphStyle("T", parent=H1, fontSize=40, leading=46)),
            ]
        ],
        colWidths=[62, None],
    )
    t.setStyle(
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
    t.hAlign = "LEFT"
    return t


def link(key, text):
    return f'<a href="#{key}" color="#D9622B"><u>{text}</u></a>'


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


class ManualDoc(BaseDocTemplate):
    def afterFlowable(self, flowable):
        outline = getattr(flowable, "_outline", None)
        if outline:
            title, key = outline
            SECTION_PAGES[key] = self.page
            self.canv.bookmarkPage(f"o_{key}")
            self.canv.addOutlineEntry(title, f"o_{key}", level=0)


def build_document(path, story, *, title: str, subject: str, footer: str) -> Path:
    """Build the PDF twice: the first pass finds each section's page, the
    second prints those page numbers in the table of contents. ``story`` is
    a function returning the list of flowables."""

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(ORANGE)
        canvas.rect(0, PAGE[1] - 6, PAGE[0], 6, stroke=0, fill=1)
        canvas.setFont("UI", 8.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(MARGIN_X, 22, footer)
        canvas.drawRightString(PAGE[0] - MARGIN_X, 22, f"Side {doc.page}")
        canvas.restoreState()

    frame_h = PAGE[1] - MARGIN_TOP - MARGIN_BOTTOM
    for _ in range(2):
        doc = ManualDoc(
            str(path),
            pagesize=PAGE,
            leftMargin=MARGIN_X,
            rightMargin=MARGIN_X,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title=title,
            author=title.split(" – ")[0],
            subject=subject,
            lang="da-DK",
            displayDocTitle=True,
        )
        frame = Frame(
            MARGIN_X,
            MARGIN_BOTTOM,
            CONTENT_W,
            frame_h,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        doc.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=decorate)])
        doc.build(story())
    return Path(path)
