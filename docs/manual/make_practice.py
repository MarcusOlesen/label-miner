"""Builds the practice pack for new Label Miner users (Danish):

    build/Øvelse/Øvelse.zip          - 12 label photos, as a Google-Drive-style zip
    build/Øvelse/Øvelse-facit.pdf    - what the program asks for each one, and the right answer

    python docs/manual/make_practice.py

The answers come from the verified answer key (tests/fixtures/answers.json),
and what the program asks comes from running it on the photos (see
demo_data.pipeline_results), so the answer sheet always matches the program.
"""

import sys
import zipfile
from pathlib import Path

from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import (
    BaseDocTemplate,
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
import make_guides as G  # noqa: E402  (photo(), styles)
import manual_style as M  # noqa: E402

from label_miner.review import review_question  # noqa: E402

ANSWERS = demo_data.answers()
RESULTS = G.RESULTS
OUT_DIR = demo_data.BUILD / "Øvelse"

# Labels that go through on their own, plus one of each kind of question.
PHOTOS = [
    "24-9/IMG_0276.HEIC",
    "24-9/IMG_0268.HEIC",
    "24-9/IMG_0289.HEIC",
    "24-9/IMG_0280.HEIC",
    "15-9/IMG_9164.HEIC",  # sideways - turned automatically
    "15-9/IMG_9167.HEIC",  # number and name disagree
    "24-9/IMG_0283.HEIC",  # part of an item
    "24-9/IMG_0275.HEIC",  # supplier number, found via the name
    "24-9/IMG_0296.HEIC",  # not on the webshop
    "24-9/IMG_0292.HEIC",  # German label - pick the right variant
    "24-9/IMG_0286.HEIC",  # no STAND on the label
    "15-9/IMG_9176.HEIC",  # STAND "AP"
]


def shop_name(name):
    """The correct webshop product name for a photo (from its suggestions)."""
    shop = ANSWERS[name]["shop"]
    if not shop:
        return None
    candidates = RESULTS[name]["candidates"]
    # The exact variant if the answer key names one - several variants of
    # the same product (colours) share a URL
    if shop.get("variant"):
        for candidate in candidates:
            if candidate.get("id") == shop["variant"]:
                return candidate["name"]
        raise LookupError(f"{name}: the right variant isn't among the suggestions")
    for candidate in candidates:
        if f"/products/{shop['handle']}" in candidate.get("url", ""):
            return candidate["name"]
    raise LookupError(f"{name}: the right product isn't among the suggestions")


def what_to_do(name):
    answer, result = ANSWERS[name], RESULTS[name]
    if not result["warnings"]:
        return "Ingenting – den bliver <b>OK</b> af sig selv."
    reason = answer.get("reason", "")
    if reason in ("number/name conflict", "box part", "supplier number, found by name", "German label"):
        product = shop_name(name)  # only these answers name the product to pick
    if reason == "number/name conflict":
        return (
            f"Vælg <b>{product}</b>. Nummeret på labelen hører til en anden vare – det er "
            "labelen, der er forkert."
        )
    if reason == "box part":
        return (
            f"Tjek nummer og navn. Vælg gerne <b>{product}</b> – den får ingen pris, og "
            "<i>OBS: delvis vare</i> står i kommentaren."
        )
    if reason == "supplier number, found by name":
        return f"Forslaget passer: vælg <b>{product}</b> og godkend."
    if reason == "not on shop":
        return "Varen findes ikke på hjemmesiden. Tjek felterne, skriv prisen hvis du kender den, og godkend."
    if reason == "German label":
        return f"Tysk label. Vælg <b>{product}</b> (»Grau/beige« = Grå/Beige, »rechts« = højrevendt)."
    if reason == "no STAND on label":
        return "Labelen har ingen STAND. Skriv den stand, varen står på, og godkend."
    if reason == "odd STAND":
        return "Labelen siger »AP«. Ret STAND til <b>A</b> og godkend."
    return "Se spørgsmålet i programmet."


def question(name):
    result = RESULTS[name]
    q = review_question([tuple(w) for w in result["warnings"]], result["parsed"].get("part", ""))
    return q["title"] if q else "–"


def build_zip():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "Øvelse.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for number, name in enumerate(PHOTOS, 1):
            # Renamed 01-12 so the answer sheet is easy to follow; the app sorts by name
            archive.write(demo_data.PHOTOS / name, f"Øvelse/{number:02d}_{Path(name).name}")
    return path


def build_facit():
    page_size = landscape(A4)
    width = page_size[0] - 2 * G.MARGIN
    rows = []
    for number, name in enumerate(PHOTOS, 1):
        answer = ANSWERS[name]
        thumb = Image(str(G.photo(name, "label", 500)), width=78, height=58, kind="bound")
        fields = f"{answer['mk_varenummer']}<br/>{answer['mk_ordrenr']} · STAND {answer['stand'] or '–'}"
        rows.append(
            [
                Paragraph(f"<b>{number:02d}</b>", M.CELL),
                thumb,
                G.p(question(name), M.CELL),
                G.p(what_to_do(name), M.CELL),
                Paragraph(fields, G.CAPTION),
            ]
        )
    auto = sum(1 for name in PHOTOS if not RESULTS[name]["warnings"])
    table = Table(
        [
            [
                G.p(h, M.CELL_B)
                for h in (
                    "Nr.",
                    "Label",
                    "Programmet spørger",
                    "Det rigtige svar",
                    "Nummer · ordrenr · stand",
                )
            ]
        ]
        + rows,
        colWidths=[30, 90, width * 0.2, width * 0.42, width - 120 - width * 0.62],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 1.2, M.ORANGE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, M.RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    intro = [
        G.p("ØVELSE", M.KICKER),
        Paragraph("Øvelse: 12 labels – facit", G.TITLE),
        G.p(
            f"Åbn Label Miner, vælg <b>Øvelse.zip</b>, og gennemse de labels, programmet spørger om. "
            f"Sammenlign bagefter med facit herunder. {auto} af de 12 bliver OK af sig selv – de "
            f"andre {12 - auto} skal du tage stilling til. Se også vejledningen (knappen "
            "<b>?&nbsp;Hjælp</b>).",
            M.LEAD,
        ),
        Spacer(1, 4),
    ]

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(M.ORANGE)
        canvas.rect(0, page_size[1] - 6, page_size[0], 6, stroke=0, fill=1)
        canvas.setFont("UI", 8.5)
        canvas.setFillColor(M.MUTED)
        canvas.drawString(G.MARGIN, 22, "Øvelse · facit  ·  Label Miner")
        canvas.drawRightString(page_size[0] - G.MARGIN, 22, f"Side {doc.page}")
        canvas.restoreState()

    path = OUT_DIR / "Øvelse-facit.pdf"
    doc = BaseDocTemplate(
        str(path),
        pagesize=page_size,
        leftMargin=G.MARGIN,
        rightMargin=G.MARGIN,
        topMargin=34,
        bottomMargin=40,
        title="Label Miner – øvelse, facit",
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
                        G.MARGIN,
                        40,
                        width,
                        page_size[1] - 74,
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
    doc.build(intro + [table])
    return path


if __name__ == "__main__":
    print("wrote", build_zip())
    print("wrote", build_facit())
