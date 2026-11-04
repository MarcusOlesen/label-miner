"""Builds the Danish user manual, build/Label-Miner-Vejledning.pdf.

    python docs/manual/make_manual.py            # new screenshots + PDF
    python docs/manual/make_manual.py --no-shots # reuse existing screenshots

The screenshots come from screenshots.py, which drives the real app on the
fixture photos, and the example rows are the app's own export of them, so the
manual always matches the program it ships with. The page design is shared
with the Label Printer manual (manual_style.py). The installer
(packaging/installer.iss) includes the PDF, and the "? Hjælp" buttons open it.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import demo_data  # noqa: E402  (sets up the import paths)
import manual_style as M  # noqa: E402
from manual_style import (  # noqa: E402
    BODY,
    CELL,
    GAP,
    H2,
    KICKER,
    LEAD,
    SMALL,
    Num,
    callouts,
    colour_table,
    framed,
    front_title,
    link,
    note,
    p,
    section,
    toc_table,
    two_col,
)  # fmt: skip
from screenshots import SHOTS  # noqa: E402

from label_miner.config import APP_VERSION, COLOR_WARNING, EXCEL_COLUMNS, MANUAL_NAME  # noqa: E402

M.use_screenshots(SHOTS)
OUT = demo_data.BUILD / MANUAL_NAME
ROWS_FILE = SHOTS / "export_rows.json"
COMMENTS_COLUMN = EXCEL_COLUMNS[7]
W = M.CONTENT_W
num = Num


def framed_fit(name, max_w, max_h):
    """A framed screenshot as large as fits in max_w x max_h; returns (flowable, width)."""
    w, h = PILImage.open(SHOTS / f"{name}.png").size
    width = min(max_w, max_h * w / h)
    return framed(name, width), width


# The review window's question box, as it looks in the app (dark, orange border)
APP_DARK = M.colors.HexColor("#1C1C1C")
APP_ORANGE = M.colors.HexColor(COLOR_WARNING)
QUESTION = ParagraphStyle("Question", parent=M.CELL_B, textColor=APP_ORANGE, fontSize=10.5, leading=13.5)


def question_box(title, width):
    t = Table([[p(title, QUESTION)]], colWidths=[width])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), APP_DARK),
                ("BOX", (0, 0), (-1, -1), 1.5, APP_ORANGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return t


def example_pair(left, right, max_h=430):
    """Two review examples side by side: each a cropped screenshot with its
    title and numbered explanation next to it."""
    half = (W - GAP) / 2

    def one(name, title, items):
        image, image_w = framed_fit(name, half * 0.6, max_h)
        text_w = half - image_w - 16
        cell = Table([[image, [p(title, H2), callouts(items, text_w)]]], colWidths=[image_w + 16, text_w])
        cell.setStyle(
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
        return cell

    row = Table([[one(*left), one(*right)]], colWidths=[half + GAP, half])
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), GAP),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return row


SECTIONS = [
    ("oversigt", "Programmet på én side"),
    ("kort", "Sådan gør du – kort fortalt"),
    ("trin1", "Trin 1: Vælg billederne"),
    ("trin2", "Trin 2: Programmet læser labels"),
    ("trin3", "Trin 3: Gennemse labels"),
    ("vaelg", "Vælg den rigtige vare"),
    ("spoergsmaal", "Spørgsmålene – og hvad du gør"),
    ("billede", "Billedet: zoom, drej og læs igen"),
    ("trin4", "Trin 4: Excel og kopiér"),
    ("trin5", "Trin 5: Sæt ind i Google Sheets"),
    ("ret", "Ret en label bagefter"),
    ("regler", "Hvornår godkender programmet selv?"),
    ("fejl", "Hvis noget går galt"),
    ("installation", "Installation og opdatering"),
]
TITLE = dict(SECTIONS)


def build_story(export_rows):
    s = []

    # ---- Front page -------------------------------------------------------------
    left = [
        Spacer(1, 10),
        p("VEJLEDNING", KICKER),
        front_title("Label Miner", demo_data.ICON),
        Spacer(1, 6),
        p(
            "Læser labels på billederne af varerne, finder varen og prisen på hjemmesiden og "
            "laver et Excel-ark til Google Sheets. Du skal kun kigge på de labels, programmet "
            "er i tvivl om.",
            LEAD,
        ),
        p(
            f"Version {APP_VERSION}. Klik på et emne for at springe dertil. Vejledningen "
            "åbnes også med knappen <b>?&nbsp;Hjælp</b> i programmet.",
            SMALL,
        ),
        Spacer(1, 4),
        toc_table(SECTIONS, W - 330 - GAP - 20),
    ]
    s.append(
        two_col(
            left,
            [
                Spacer(1, 60),
                framed("13_label", 330),
                Spacer(1, 6),
                p("Programmet læser labels som denne: varenummer, varenavn, MK-ordrenummer og STAND.", SMALL),
            ],
            W - 330 - GAP,
        )
    )
    s.append(PageBreak())

    # ---- Overview -----------------------------------------------------------------
    s += section(
        TITLE["oversigt"],
        "oversigt",
        "Overblik",
        "Hovedvinduet viser én række for hvert billede. De labels, programmet er i "
        "tvivl om, gennemser du i et vindue for sig.",
    )
    img_w = 520
    s.append(
        two_col(
            framed("03_liste", img_w),
            callouts(
                [
                    (1, "<b>Resultatlisten:</b> én række pr. billede med varenavn, nummer og stand."),
                    (
                        2,
                        "<b>Status:</b> <font color='#2e8b3e'><b>OK</b></font> er klar. "
                        "<font color='#b8860b'><b>Gennemse</b></font> skal du kigge på. "
                        "<font color='#c0392b'><b>Fejl</b></font> kunne ikke læses.",
                    ),
                    (
                        3,
                        "<b>Gennemse labels</b> åbner vinduet, hvor du tjekker dem. Tallet er, hvor mange "
                        "der venter. Knappen forsvinder, når der ikke er flere.",
                    ),
                    (4, "<b>? Hjælp</b> åbner denne vejledning."),
                ],
                W - img_w - GAP,
            ),
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Short version ------------------------------------------------------------
    s += section(
        TITLE["kort"],
        "kort",
        "Kort fortalt",
        "Det er alt, du skal gøre til hverdag. Detaljerne står på de næste sider.",
    )
    steps = [
        (
            "Hent billederne",
            "Download mappen med billeder fra Google Drive. Den kommer som en "
            "<b>ZIP-fil</b> – den skal ikke pakkes ud.",
        ),
        (
            "Vælg ZIP-filen",
            "Åbn Label Miner og klik <b>Vælg billeder eller ZIP</b> – eller træk "
            f"ZIP-filen ind i vinduet. {link('trin1', 'Se trin 1')}",
        ),
        (
            "Vent",
            "Programmet læser alle labels selv og finder priserne. De fleste bliver "
            f"<b>OK</b>. {link('trin2', 'Se trin 2')}",
        ),
        (
            "Gennemse",
            "Klik <b>Gennemse labels</b>. Læs spørgsmålet øverst, ret eller vælg, og tryk "
            f"<b>Godkend og næste</b>. {link('trin3', 'Se trin 3')}",
        ),
        (
            "Excel",
            "Når alle er gennemset, gemmes Excel-filen af sig selv ved siden af ZIP-filen. "
            f"Klik <b>Kopiér resultat</b>. {link('trin4', 'Se trin 4')}",
        ),
        (
            "Google Sheets",
            "Klik i kolonne A i den første tomme række i arket og tryk <b>Ctrl+V</b>. "
            f"{link('trin5', 'Se trin 5')}",
        ),
    ]
    rows = [
        [
            num(i + 1, 26),
            p(f"<b>{t}</b>", ParagraphStyle("st", parent=BODY, fontSize=13.5, leading=17)),
            p(txt, BODY),
        ]
        for i, (t, txt) in enumerate(steps)
    ]
    t = Table(rows, colWidths=[40, 150, W - 190])
    t.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, M.RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 12),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    s.append(t)
    s.append(Spacer(1, 14))
    s.append(
        note(
            "<b>Programmet gætter aldrig en pris.</b> Er det bare en smule i tvivl om varen "
            "eller prisen, spørger det dig i stedet. Derefter laves etiketterne i Label Printer."
        )
    )
    s.append(PageBreak())

    # ---- Step 1 ---------------------------------------------------------------------
    s += section(
        TITLE["trin1"],
        "trin1",
        "Trin 1",
        "Programmet tager en ZIP-fil fra Google Drive eller billeder, du vælger direkte.",
    )
    img_w = 505
    s.append(
        two_col(
            framed("01_start", img_w),
            [
                callouts(
                    [
                        (
                            1,
                            "<b>Vælg billeder eller ZIP</b> åbner et vindue, hvor du vælger ZIP-filen (eller "
                            "flere billeder).",
                        ),
                        (
                            2,
                            "Du kan også <b>trække</b> ZIP-filen ind i vinduet – eller slippe den på "
                            "Label Miner-ikonet på skrivebordet, så starter programmet med den.",
                        ),
                        (3, "<b>? Hjælp</b> åbner denne vejledning."),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "ZIP-filen skal ikke pakkes ud. Excel-filen gemmes senere i samme mappe som "
                    "ZIP-filen og får mappens navn, fx <i>24-9.xlsx</i>."
                ),
                Spacer(1, 8),
                p("Billeder fra iPhone (HEIC), JPG og PNG kan læses.", SMALL),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Step 2 -----------------------------------------------------------------------
    s += section(
        TITLE["trin2"], "trin2", "Trin 2", "Programmet læser billederne selv – et par sekunder pr. billede."
    )
    img_w = 505
    s.append(
        two_col(
            framed("02_laeser", img_w),
            [
                callouts(
                    [
                        (1, "<b>Fremgang:</b> hvor mange der er læst, og hvor lang tid der ca. er tilbage."),
                        (
                            2,
                            "<b>Listen</b> fyldes op, efterhånden som billederne bliver læst. "
                            "Den hopper ikke, så du kan læse rækkerne imens.",
                        ),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "Du kan begynde at gennemse labels, mens programmet stadig læser. Knappen "
                    "<b>Gennemse labels</b> dukker op, så snart der er noget at kigge på."
                ),
                Spacer(1, 8),
                p(
                    "Den allerførste gang programmet startes efter en installation, kan det tage et "
                    "par minutter, før det er klar.",
                    SMALL,
                ),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Step 3 -------------------------------------------------------------------------
    s += section(
        TITLE["trin3"],
        "trin3",
        "Trin 3",
        "Klik <b>Gennemse labels</b>. Vinduet viser én label ad gangen – billedet til "
        "venstre og det, programmet har læst, til højre.",
    )
    img_w = 520
    s.append(
        two_col(
            framed("04_gennemse", img_w),
            callouts(
                [
                    (
                        1,
                        "<b>Spørgsmålet:</b> det eneste, du skal tage stilling til. "
                        f"{link('spoergsmaal', 'Se alle spørgsmål')}",
                    ),
                    (
                        2,
                        "<b>Hvilken vare er det?</b> Varer fra hjemmesiden med billede og pris. "
                        f"{link('vaelg', 'Se hvordan du vælger')}",
                    ),
                    (3, "<b>Felterne</b> med ⚠ er dem, spørgsmålet handler om. Du kan rette i alle felter."),
                    (4, "<b>Godkend og næste</b> gemmer og går videre. Det er den eneste grønne knap."),
                    (
                        5,
                        "<b>Spring over – ret senere</b> går videre uden at gemme. Har du rettet noget, "
                        "bliver du spurgt, om det skal gemmes.",
                    ),
                    (6, "<b>Åbn hjemmeside</b> viser varen på moebelkompagniet.dk."),
                    (7, "<b>Billedet</b> er zoomet ind på labelen."),
                    (
                        8,
                        f"<b>Knapper til billedet</b> – drej, zoom og læs igen. {link('billede', 'Se mere')}",
                    ),
                ],
                W - img_w - GAP,
            ),
            img_w,
        )
    )
    s.append(Spacer(1, 8))
    s.append(
        note(
            "Lukker du vinduet med <b>✕</b>, gemmes din plads: tryk <b>Gennemse labels</b> "
            "igen, så fortsætter du med samme label – også det, du havde skrevet."
        )
    )
    s.append(PageBreak())

    # ---- Choosing a product ---------------------------------------------------------------
    s += section(
        TITLE["vaelg"],
        "vaelg",
        "Trin 3",
        "Er programmet i tvivl om varen, viser det forslag fra hjemmesiden. Vælg det "
        "forslag, der passer til labelen.",
    )
    image, img_w = framed_fit("05_valgt", 420, 400)
    s.append(
        two_col(
            image,
            [
                callouts(
                    [
                        (1, "<b>Klik på varen</b>, der passer. Den får en grøn kant og et ✓."),
                        (2, "<b>Varenavnet</b> bliver skiftet til navnet fra hjemmesiden."),
                        (3, "<b>Prisen</b> bliver udfyldt: tilbudspris og normalpris i parentes."),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "<b>Fortryd:</b> klik på den valgte vare igen – så står der igen det, labelen "
                    "sagde. Intet bliver gemt, før du trykker <b>Godkend og næste</b>."
                ),
                Spacer(1, 8),
                p(
                    "Under hver vare står, om den er fundet <i>via nummer</i> (nummeret på labelen) "
                    "eller <i>via navn</i>. Står den der ikke, må du gerne skrive navn og pris selv.",
                    SMALL,
                ),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- The questions ---------------------------------------------------------------------
    s += section(
        TITLE["spoergsmaal"],
        "spoergsmaal",
        "Trin 3",
        "Øverst i vinduet står der altid ét spørgsmål. Her er, hvad de betyder.",
    )
    questions = [
        [
            "Nummer og navn passer ikke sammen",
            "Nummeret på labelen hører til en anden vare på hjemmesiden end navnet. Ofte er "
            "nummeret skrevet forkert på labelen.",
            "Kig på billedet, og vælg den vare, der passer til navn og farve.",
        ],
        [
            "Er det den rigtige vare?",
            "Nummeret findes ikke på hjemmesiden (fx et leverandørnummer som <i>UE 45990201</i>), "
            "men varen er fundet via navnet.",
            "Tjek at forslaget passer, klik på det og godkend.",
        ],
        [
            "Vælg den rigtige vare",
            "Varen er fundet, men navn eller farve passer ikke præcist (fx en tysk label).",
            "Vælg den farve eller udgave, der står på labelen.",
        ],
        [
            "Delvis vare (del 1/2)",
            "Kun en del af varen, og hjemmesiden kunne ikke bekræfte den på nummer og navn. "
            "Der sættes aldrig pris, og i kommentaren står <i>OBS: delvis vare</i>.",
            "Tjek nummer og navn. Vælg gerne varen – så får den navn og link, men ingen pris.",
        ],
        [
            "Varen findes ikke på hjemmesiden",
            "Hverken nummer eller navn gav en vare.",
            "Tjek felterne mod billedet. Skriv prisen, hvis du kender den.",
        ],
        [
            "Skriv STAND (A, B eller C)",
            "Standen står ikke på labelen, eller kunne ikke læses.",
            "Skriv standen.",
        ],
        ["Tjek STAND", "Der står noget andet end A, B eller C (fx »AP«).", "Ret den til A, B eller C."],
        [
            "Nogle felter kunne ikke læses",
            "Billedet er uskarpt, eller labelen er dækket.",
            f"Skriv felterne ud fra billedet, eller {link('billede', 'drej eller beskær')} og læs "
            "billedet igen.",
        ],
    ]
    widths = [W * 0.28, W * 0.4, W * 0.32]
    data = [[p(h, M.HEAD_WHITE) for h in ("Spørgsmålet i vinduet", "Hvad betyder det?", "Gør sådan")]]
    data += [
        [question_box(q, widths[0] - 16), p(meaning, CELL), p(todo, CELL)] for q, meaning, todo in questions
    ]
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 1), (-1, -2), 1.5, M.colors.white),
        ("LINEAFTER", (0, 0), (-2, -1), 3, M.colors.white),
    ]
    for i, kind in enumerate(("accent", "neutral", "good")):
        head, background = M.KINDS[kind]
        style += [("BACKGROUND", (i, 0), (i, 0), head), ("BACKGROUND", (i, 1), (i, -1), background)]
    table.setStyle(TableStyle(style))
    s.append(table)
    s.append(PageBreak())
    s += section("Sådan ser spørgsmålene ud", "eksempler", "Trin 3 – eksempler")
    s.append(
        example_pair(
            (
                "06_delvis",
                "Delvis vare",
                [
                    (1, "Kun en del af varen – der sættes <b>ingen pris</b>."),
                    (2, "Vælg gerne varen: den giver navn og link, men stadig ingen pris."),
                    (3, "I kommentaren står <i>OBS: delvis vare</i>."),
                ],
            ),
            (
                "07_ikke_fundet",
                "Ikke på hjemmesiden",
                [
                    (1, "Hverken nummer eller navn gav en vare."),
                    (2, "Tjek felterne mod billedet, og skriv prisen, hvis du kender den."),
                ],
            ),
        )
    )
    s.append(PageBreak())
    s += section("Sådan ser spørgsmålene ud", "eksempler2", "Trin 3 – eksempler")
    s.append(
        example_pair(
            (
                "08_stand",
                "Tjek STAND",
                [
                    (1, "Labelen siger »AP«."),
                    (2, "Ret feltet til <b>A</b>, <b>B</b> eller <b>C</b>, og godkend."),
                ],
            ),
            (
                "09_via_navn",
                "Fundet via navnet",
                [
                    (1, "Nummeret er et leverandørnummer, der ikke findes på hjemmesiden."),
                    (2, "Passer forslaget, så klik på det og godkend."),
                ],
            ),
        )
    )
    s.append(PageBreak())

    # ---- The photo -------------------------------------------------------------------------
    s += section(
        TITLE["billede"],
        "billede",
        "Trin 3 – billedet",
        "Knapperne under billedet ændrer kun billedet – ikke felterne.",
    )
    img_w = 505
    s.append(
        two_col(
            framed("10_billede", img_w),
            [
                callouts(
                    [
                        (1, "<b>Drej</b> billedet en kvart omgang."),
                        (2, "<b>Vis hele billedet</b> / <b>Zoom ind på labelen</b>."),
                        (
                            3,
                            "<b>Læs billedet igen</b> – efter du har drejet eller beskåret det. Felterne "
                            "bliver læst forfra.",
                        ),
                        (4, "Under <b>Flere værktøjer</b>: skyder til at rette billedet præcist op."),
                        (5, "<b>Spejl</b> og <b>Nulstil billede</b>."),
                        (6, "<b>Tekst læst fra labelen</b> – præcis det, programmet kunne læse."),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "<b>Beskær:</b> træk en firkant på billedet rundt om labelen, og tryk "
                    "<b>Læs billedet igen</b>."
                ),
                Spacer(1, 8),
                p("Labels, der er fotograferet på siden, drejer programmet selv.", SMALL),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Step 4 -----------------------------------------------------------------------------
    s += section(
        TITLE["trin4"], "trin4", "Trin 4", "Når alle labels er gennemset, gemmes Excel-filen af sig selv."
    )
    img_w = 400
    s.append(
        two_col(
            framed("11_eksport", img_w),
            [
                callouts(
                    [
                        (
                            1,
                            "<b>Excel gemt:</b> filen ligger i samme mappe som ZIP-filen og har mappens "
                            "navn, fx <i>24-9.xlsx</i>.",
                        ),
                        (
                            2,
                            "<b>Kopiér resultat</b> kopierer rækkerne, så de kan sættes direkte ind i "
                            "Google Sheets (Ctrl+V).",
                        ),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "Er den samme vare fotograferet flere gange, bliver den til én række. "
                    "<b>STK</b> er labelens <b>Antal</b> – på gamle labels uden Antal: antal billeder."
                ),
            ],
            img_w,
        )
    )
    s.append(Spacer(1, 10))
    if export_rows:
        header = export_rows[1]
        wanted = ["Varenavn", "MK Varenummer", "MK ordrenr", "STAND", "STK", COMMENTS_COLUMN]
        idx = [header.index(c) for c in wanted]
        priced = [r for r in export_rows[2:] if r[idx[-1]] and not str(r[idx[-1]]).startswith("OBS")]
        body = [[str(r[i] or "") for i in idx] for r in priced[:4]]
        s.append(p("Sådan ser rækkerne ud (uddrag fra eksemplet):", SMALL))
        s.append(
            colour_table(
                ["Varenavn", "MK varenr.", "MK ordrenr", "Stand", "STK", "Pris / kommentar"],
                body,
                [W * 0.38, W * 0.12, W * 0.11, W * 0.07, W * 0.06, W * 0.26],
                ("neutral",) * 5 + ("accent",),
            )
        )
    s.append(PageBreak())

    # ---- Step 5 -------------------------------------------------------------------------------
    s += section(
        TITLE["trin5"],
        "trin5",
        "Trin 5",
        "Efter <b>Kopiér resultat</b> sætter du rækkerne ind i Google Sheets – samme ark "
        "som Label Printer bruger.",
    )
    img_w = 500
    s.append(
        two_col(
            framed("14_google_sheet", img_w),
            [
                callouts(
                    [
                        (
                            1,
                            "Klik i <b>kolonne A</b> i den første tomme række under de andre varer, og tryk "
                            "<b>Ctrl+V</b>. Øverst kommer en række med mappens navn (her <i>24-9</i>).",
                        ),
                        (
                            2,
                            "Derefter én række pr. vare: varenavn, MK-varenummer, MK-ordrenummer, "
                            "STAND, STK, Modtaget og prisen fra hjemmesiden.",
                        ),
                        (
                            3,
                            "<b>Varenummer</b> (A) og <b>Pris pr stk</b> (I) er tomme – dem kender "
                            "Label Miner ikke. De udfyldes i arket.",
                        ),
                    ],
                    W - img_w - GAP,
                ),
                note(
                    "Klik altid i <b>kolonne A</b>, før du sætter ind – ellers havner alt i de forkerte "
                    "kolonner."
                ),
                Spacer(1, 8),
                p(
                    "Rækker med <i>OBS: delvis vare</i> får ingen pris. Når arket er udfyldt, laves "
                    "etiketterne i Label Printer (se dens egen vejledning).",
                    SMALL,
                ),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Editing afterwards -----------------------------------------------------------------
    s += section(
        TITLE["ret"],
        "ret",
        "Bagefter",
        "Opdager du en fejl, når alt er gennemset: <b>dobbeltklik på rækken</b> i "
        "listen. Så åbner den i et vindue for sig.",
    )
    img_w = 570
    s.append(
        two_col(
            framed("12_rediger", img_w),
            [
                callouts(
                    [
                        (
                            1,
                            "<b>Gem ændringer:</b> gemmer rettelsen – Excel-filen bliver opdateret med det "
                            "samme.",
                        ),
                        (2, "<b>Luk</b> uden at gemme (har du rettet noget, bliver du spurgt)."),
                    ],
                    W - img_w - GAP,
                ),
            ],
            img_w,
        )
    )
    s.append(PageBreak())

    # ---- Rules ---------------------------------------------------------------------------------
    s += section(
        TITLE["regler"],
        "regler",
        "Regler",
        "En label bliver kun godkendt uden dig, når programmet er helt sikkert. Ellers "
        "havner den i Gennemse.",
    )
    half = (W - GAP) / 2
    s.append(
        two_col(
            [
                p("Godkendes automatisk, når…", H2),
                colour_table(
                    ["Krav", "Ellers"],
                    [
                        ["Alle fire felter er læst", "nummer, navn, MK-ordrenummer, stand"],
                        ["STAND er A, B eller C", "»AP« → Gennemse"],
                        ["Nummeret findes på hjemmesiden", "leverandørnummer → Gennemse"],
                        [
                            "Præcis én udgave passer til <i>alle</i> ord på labelen",
                            "farven mangler på labelen → Gennemse",
                        ],
                        [
                            "Delvis vare (»1/2«, »2/2«): det samme",
                            "men aldrig pris – <i>OBS: delvis vare</i>",
                        ],
                    ],
                    [half * 0.55, half * 0.45],
                    ("good", "neutral"),
                ),
                Spacer(1, 8),
                p(
                    "Prisen er hjemmesidens pris: tilbudspris og normalpris i parentes, fx "
                    "<i>8.999 kr (12.999 kr)</i>.",
                    SMALL,
                ),
            ],
            [
                p("Det ser programmet bort fra", H2),
                colour_table(
                    ["På labelen", "Hvad sker der"],
                    [
                        ["»- 6 stk.« efter navnet", "Bruges ikke. STK er labelens Antal."],
                        ["Håndskrift, fx »1stk«", "Ignoreres."],
                        ["Andre klistermærker og tekst på kassen", "Ignoreres – kun selve labelen læses."],
                        ["Tekst under STAND, fx »(forkert vare sendt)«", "Ignoreres."],
                        ["»|« og små stavefejl fra læsningen", "Tåles, når varen findes."],
                    ],
                    [half * 0.5, half * 0.5],
                    ("neutral", "accent"),
                ),
            ],
            half,
        )
    )
    s.append(Spacer(1, 16))
    s.append(p("Status i listen", H2))
    s.append(
        colour_table(
            ["OK", "Gennemse", "Fejl"],
            [
                [
                    "Godkendt af sig selv. Er klar til Excel.",
                    "Programmet er i tvivl. Tryk <b>Gennemse labels</b> og svar på spørgsmålet.",
                    "Billedet kunne ikke læses. Læs billedet igen, eller skriv felterne selv.",
                ]
            ],
            [W / 3] * 3,
            ("good", "accent", "bad"),
        )
    )
    s.append(PageBreak())

    # ---- Troubleshooting -------------------------------------------------------------------------
    s += section(TITLE["fejl"], "fejl", "Fejlfinding")
    trouble = [
        [
            "Vinduet med labels dukker ikke op",
            "Det åbner først, når du beder om det.",
            "Tryk <b>Gennemse labels</b>.",
        ],
        [
            "Jeg lukkede gennemse-vinduet med ✕",
            "Din plads er gemt.",
            "Tryk <b>Gennemse labels</b> – du fortsætter samme sted.",
        ],
        [
            "Excel-filen er ikke gemt",
            "Den gemmes først, når alle labels er gennemset.",
            "Gennemse de sidste (tallet står på knappen).",
        ],
        [
            "Der er ingen billeder i gennemse-vinduet",
            "Programmets midlertidige kopi af billederne er blevet slettet.",
            "Luk Label Miner, og vælg ZIP-filen igen.",
        ],
        [
            "»Kunne ikke søge på hjemmesiden«",
            "Ingen internetforbindelse.",
            "Tjek forbindelsen, og tryk <b>Søg på hjemmesiden igen</b>.",
        ],
        [
            "Prisen mangler",
            "Varen blev ikke fundet sikkert – programmet gætter ikke.",
            f"Vælg varen, eller skriv prisen. {link('spoergsmaal', 'Se spørgsmålene')}",
        ],
        [
            "En forkert vare blev godkendt",
            "Et forkert forslag blev valgt.",
            f"Dobbeltklik på rækken, og ret den. {link('ret', 'Se hvordan')}",
        ],
        [
            "Rækkerne havnede i de forkerte kolonner i Google Sheets",
            "Der var ikke klikket i kolonne A, før der blev sat ind.",
            f"Fortryd (Ctrl+Z), klik i kolonne A og sæt ind igen. {link('trin5', 'Se trin 5')}",
        ],
        [
            "Mange labels skal gennemses",
            "Billederne er uskarpe, eller labels er dækket.",
            "Tag billedet lige på, skarpt og med hele labelen.",
        ],
        [
            "Programmet er langsomt første gang",
            "Windows tjekker de nye filer efter installation.",
            "Vent et par minutter. Næste gang går det hurtigt.",
        ],
    ]
    s.append(
        colour_table(
            ["Det sker", "Hvorfor", "Gør sådan"],
            trouble,
            [W * 0.3, W * 0.36, W * 0.34],
            ("bad", "neutral", "good"),
        )
    )
    s.append(PageBreak())

    # ---- Installation -----------------------------------------------------------------------------
    s += section(
        TITLE["installation"],
        "installation",
        "Til nye computere",
        "Programmet installeres med én fil: <b>Label-Miner-Setup.exe</b> (ca. 650 MB). "
        "Der skal ikke installeres andet.",
    )
    half = (W - GAP) / 2
    s.append(
        two_col(
            [
                p("Første gang", H2),
                callouts(
                    [
                        (1, "Dobbeltklik på <b>Label-Miner-Setup.exe</b>."),
                        (
                            2,
                            "Siger Windows »Windows beskyttede din pc«, så klik <b>Flere oplysninger</b> og "
                            "derefter <b>Kør alligevel</b>. Advarslen kommer, fordi programmet er lavet til "
                            "butikken og ikke købt i en butik – det er normalt.",
                        ),
                        (3, "Klik dig gennem installationen. Der kræves ikke administrator-adgangskode."),
                        (
                            4,
                            "Start programmet med ikonet <b>Label Miner</b> på skrivebordet eller i "
                            "Start-menuen.",
                        ),
                    ],
                    half,
                ),
                Spacer(1, 6),
                note("Programmet skal have <b>internet</b> for at finde priserne på hjemmesiden."),
            ],
            [
                p("Opdatering", H2),
                p(
                    "Kør den nye <b>Label-Miner-Setup.exe</b> på samme måde. Den erstatter den gamle "
                    "version og rydder op efter den – du skal ikke afinstallere først. Er Label Miner "
                    "åben, bliver den lukket først."
                ),
                Spacer(1, 8),
                p("Det bliver installeret", H2),
                colour_table(
                    ["Hvad", "Hvor"],
                    [
                        ["Programmet", "Ikon på skrivebordet og i Start-menuen"],
                        [
                            "Denne vejledning",
                            "<b>?&nbsp;Hjælp</b> i programmet, eller Start-menuen "
                            "(<i>Label Miner – Vejledning</i>)",
                        ],
                        ["Afinstallering", "Start-menuen (<i>Afinstaller Label Miner</i>)"],
                    ],
                    [half * 0.35, half * 0.65],
                    ("neutral", "accent"),
                ),
            ],
            half,
        )
    )
    return s


def build_pdf() -> Path:
    rows = json.loads(ROWS_FILE.read_text(encoding="utf-8")) if ROWS_FILE.exists() else []
    return M.build_document(
        OUT,
        lambda: build_story(rows),
        title="Label Miner – Vejledning",
        subject=f"Brugervejledning til Label Miner {APP_VERSION}",
        footer=f"Label Miner {APP_VERSION}  ·  Vejledning",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-shots", action="store_true", help="reuse existing screenshots")
    args = parser.parse_args()
    if not args.no_shots:
        import screenshots

        _, rows = screenshots.take_all()
        ROWS_FILE.write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"wrote {build_pdf()}")


if __name__ == "__main__":
    main()
