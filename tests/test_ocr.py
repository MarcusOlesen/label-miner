"""Finding the label's lines among the OCR's text pieces, on corner points
recorded from real photos."""

import math
from types import SimpleNamespace

import pytest

from label_miner.ocr import label_blocks_in_reading_order, read_text_blocks


def blocks_of(pieces):
    """The TextBlocks read_text_blocks makes of these (text, corner points)."""
    result = SimpleNamespace(boxes=[points for _, points in pieces], txts=[text for text, _ in pieces])
    return read_text_blocks(lambda image: result, None)


def label_text(pieces):
    label, _ = label_blocks_in_reading_order(blocks_of(pieces))
    return " ".join(block.text for block in label)


def test_a_line_split_in_two_pieces_is_joined():
    # 7-10/IMG_0948.JPG: neither piece of the first line is centred on its own
    pieces = [
        ("102162, Roots skænk,", [[138, 591], [1240, 651], [1233, 777], [131, 717]]),
        ("132x75", [[1225, 659], [1572, 673], [1568, 777], [1222, 764]]),
        ("Natur egetræe", [[551, 730], [1187, 766], [1179, 902], [544, 866]]),
        ("OUTLET", [[662, 861], [1068, 876], [1065, 974], [658, 959]]),
        ("152416", [[696, 968], [1027, 978], [1024, 1077], [692, 1067]]),
        ("STAND: B", [[632, 1067], [1068, 1084], [1065, 1177], [628, 1161]]),
    ]
    assert len(blocks_of(pieces)) == 5
    assert label_text(pieces) == "102162, Roots skænk, 132x75 Natur egetræe OUTLET 152416 STAND: B"


def test_tilted_print_on_the_box_is_not_part_of_the_label():
    # 27-10/IMG_0843.HEIC: "sniseborus" runs at about 30 degrees to the label,
    # and its upright box overlaps the label, centred on it
    pieces = [
        ("103291, Leonora", [[512, 912], [1324, 876], [1329, 993], [517, 1029]]),
        ("spisebordsstol, armlæn | Røget", [[133, 1028], [1659, 948], [1667, 1090], [141, 1171]]),
        ("egetræ", [[755, 1128], [1136, 1084], [1149, 1211], [769, 1255]]),
        ("OUTLET", [[731, 1224], [1193, 1188], [1203, 1317], [741, 1352]]),
        ("154197", [[779, 1348], [1182, 1310], [1194, 1438], [792, 1476]]),
        ("STAND: B", [[711, 1481], [1282, 1426], [1294, 1563], [723, 1617]]),
        ("sniseborus", [[118, 2274], [1808, 1385], [1874, 1893], [380, 2499]]),
    ]
    assert label_text(pieces) == (
        "103291, Leonora spisebordsstol, armlæn | Røget egetræ OUTLET 154197 STAND: B"
    )


# 4-11/IMG_1002.JPG, the new design: left-aligned, with box print above and
# to the left of the label ("ve spisebordsstol", "3485", "iy 07 北e")
NEW_DESIGN_WITH_BOX_PRINT = [
    ("ve spisebordsstol", [[0, 295], [1083, 319], [1078, 561], [0, 537]]),
    ("3485", [[0, 519], [261, 538], [248, 706], [0, 687]]),
    ("iy", [[0, 708], [112, 713], [106, 868], [0, 863]]),
    ("OUTLET·VARENR.", [[152, 731], [583, 766], [578, 824], [147, 789]]),
    ("103485", [[146, 777], [592, 817], [578, 974], [132, 935]]),
    ("07", [[0, 849], [113, 849], [113, 1006], [0, 1006]]),
    ("Curve spisebordsstol", [[147, 948], [701, 1011], [692, 1090], [138, 1027]]),
    ("北e", [[5, 1040], [81, 1040], [81, 1166], [5, 1166]]),
    ("ORDRE NR.", [[189, 1071], [444, 1108], [436, 1158], [182, 1121]]),
    ("Lager", [[175, 1110], [559, 1158], [537, 1334], [153, 1287]]),
    ("Antal: 24", [[138, 1413], [404, 1457], [391, 1535], [126, 1491]]),
    ("Stand: A", [[1297, 1609], [1641, 1658], [1628, 1745], [1284, 1696]]),
]

# 4-11/IMG_0989.JPG: a creased label, its lines running 3-14 degrees
NEW_DESIGN_CREASED = [
    ("OUTLET·VARENR.", [[461, 819], [828, 868], [823, 914], [455, 864]]),
    ("108139", [[454, 854], [832, 905], [815, 1031], [438, 980]]),
    ("Sevilla hjørnesofa, højrevendt - Loop Creme", [[444, 982], [1393, 1115], [1381, 1198], [433, 1065]]),
    ("ORDRE NR.", [[485, 1091], [701, 1129], [693, 1172], [477, 1134]]),
    ("150019", [[477, 1120], [834, 1187], [812, 1308], [455, 1241]]),
    ("Antal: 1", [[418, 1361], [601, 1405], [585, 1476], [401, 1431]]),
    ("Stand: B", [[1310, 1492], [1516, 1503], [1512, 1573], [1307, 1561]]),
]


def rotated(pieces, degrees):
    """The pieces as in a photo turned this many degrees (clockwise on screen)."""
    radians = math.radians(degrees)
    cos, sin = math.cos(radians), math.sin(radians)
    return [
        (text, [[x * cos - y * sin + 1000, x * sin + y * cos + 1000] for x, y in points])
        for text, points in pieces
    ]


def test_new_design_box_print_next_to_the_label_is_left_out():
    assert label_text(NEW_DESIGN_WITH_BOX_PRINT) == (
        "OUTLET·VARENR. 103485 Curve spisebordsstol ORDRE NR. Lager Antal: 24 Stand: A"
    )


@pytest.mark.parametrize("degrees", [0, -25, 90, -90])
def test_new_design_lines_in_order_when_tilted_or_sideways(degrees):
    # Turned 25 degrees, so its lines run up to the right, the long name line's upright box
    # reaches above the number's: the lines are ordered across the label's
    # own lines instead
    label, _ = label_blocks_in_reading_order(blocks_of(rotated(NEW_DESIGN_CREASED, degrees)))
    lines = [block.text for block in label]
    assert lines[:5] == [
        "OUTLET·VARENR.",
        "108139",
        "Sevilla hjørnesofa, højrevendt - Loop Creme",
        "ORDRE NR.",
        "150019",
    ]
    # Antal and Stand share the bottom line; their order doesn't matter
    assert sorted(lines[5:]) == ["Antal: 1", "Stand: B"]
