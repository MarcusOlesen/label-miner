"""Finding the label's lines among the OCR's text pieces, on corner points
recorded from real photos."""

from types import SimpleNamespace

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
