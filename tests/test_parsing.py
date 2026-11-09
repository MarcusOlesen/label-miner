"""Label text -> fields, and label name vs. webshop name. Real OCR output from the test photos."""

import pytest

from label_miner.parsing import is_new_design, item_part, label_warnings, parse_label_fields
from label_miner.shop import name_tokens, token_coverage

# (OCR text in reading order) -> (MK number, name, order number, STAND, part of item)
PARSE_CASES = [
    pytest.param(
        "103169, Palermo chaiselong sofa, venstrevendt Loop Mørk Beige OUTLET 146421 STAND: B",
        ("103169", "Palermo chaiselong sofa, venstrevendt Loop Mørk Beige", "146421", "B", ""),
        id="plain",
    ),
    pytest.param(
        "VN 19987-330, Undra tæppe, 250×350 Beige OUTLET 137980 STAND: B",
        ("VN 19987-330", "Undra tæppe, 250x350 Beige", "137980", "B", ""),
        id="dash inside a supplier code is not the separator",
    ),
    pytest.param(
        "AY H000017492 - Stanford LUX U-sovesofa, højrevendt |opbevaring OUTLET 120332 STAND B",
        ("AY H000017492", "Stanford LUX U-sovesofa, højrevendt | opbevaring", "120332", "B", ""),
        id="supplier code with a letter",
    ),
    pytest.param(
        "101338_1/2,Vega sofabord, latte marmor |090 OUTLET 149426 STAND: B",
        ("101338_1/2", "Vega sofabord, latte marmor | Ø90", "149426", "B", "1/2"),
        id="part of an item in the number",
    ),
    pytest.param(
        "96919 - 2/2 Vega latte marmor sofabord, 90×90 OUTLET 149991 STAND B",
        ("96919", "2/2 Vega latte marmor sofabord, 90x90", "149991", "B", "2/2"),
        id="part of an item in the name",
    ),
    pytest.param(
        "108940 - Carno spisebord, 0120 OUTLET 147433 STAND A",
        ("108940", "Carno spisebord, Ø120", "147433", "A", ""),
        id="diameter read as a zero",
    ),
    pytest.param(
        "OBELK OM 103490, Plush Esszimmerstuhl, Amiya Bouclé Braun OUTLET #DE 2280 STAND: A",
        ("103490", "Plush Esszimmerstuhl, Amiya Bouclé Braun", "#DE 2280", "A", ""),
        id="box print in front of the number",
    ),
    pytest.param(
        "UE 45990201, Carno spisebord, 0120 OUTLET 137.017 STAND: A",
        ("UE 45990201", "Carno spisebord, Ø120", "137017", "A", ""),
        id="smudge read as a dot in the order number",
    ),
    pytest.param(
        "102105 Venice 3 personers sovesofa, opbevaring - Aragon Beige OUTLET FRA LAGER",
        ("102105", "Venice 3 personers sovesofa, opbevaring - Aragon Beige", "FRA LAGER", "", ""),
        id="no separator after the number, no STAND",
    ),
    pytest.param(
        "AY H000022202, Vega sofabord, latte marmor | 90x90 OUTLET 135800 STAND: AP",
        ("AY H000022202", "Vega sofabord, latte marmor | 90x90", "135800", "AP", ""),
        id="unusual STAND kept as printed",
    ),
]


# The new design: (OCR text in reading order) -> (MK number, name, order
# number, STAND, part of item, antal). Real OCR output from the 4-11 photos.
NEW_DESIGN_CASES = [
    pytest.param(
        "OUTLET·VARENR. 103469 Roots sofabord, 080 ORDRE NR. 156146 Stand: B Antal:1",
        ("103469", "Roots sofabord, Ø80", "156146", "B", "", "1"),
        id="plain",
    ),
    pytest.param(
        "OUTLET·VARENR. VN 61248-103 Vince tæppe, 230x160 ORDRE NR. 156950 Stand: A Antal:1",
        ("VN 61248-103", "Vince tæppe, 230x160", "156950", "A", "", "1"),
        id="supplier code",
    ),
    pytest.param(
        "OUTLET·VARENR. AY 0000108453 Cholet sengebord, væghængt | vild eg ORDRE NR. 154165 Antal:1 Stand: B",
        ("AY 0000108453", "Cholet sengebord, væghængt | vild eg", "154165", "B", "", "1"),
        id="supplier code, Antal before Stand",
    ),
    pytest.param(
        "OUTLET·VARENR. 103162_1/2 1/2 Palermo chaiselong sofa, venstrevendt - Aragon Blà "
        "ORDRE NR. 124504 Antal:1 Stand: C",
        ("103162_1/2", "1/2 Palermo chaiselong sofa, venstrevendt - Aragon Blå", "124504", "C", "1/2", "1"),
        id="part of an item, name over two lines, å read as à",
    ),
    pytest.param(
        "OUTLET·VARENR. 103485 Curve spisebordsstol ORDRE NR. Lager Antal: 24 Stand: A",
        ("103485", "Curve spisebordsstol", "Lager", "A", "", "24"),
        id="from stock",
    ),
    pytest.param(
        "OUTLET·VARENR. 102511 Plint spisebord, inkl. tillægsplade | 200/250x100 ORDRENR. 152973 "
        "Antal:1 Stand: B",
        ("102511", "Plint spisebord, inkl. tillægsplade | 200/250x100", "152973", "B", "", "1"),
        id="ORDRE NR. read without the space",
    ),
]


@pytest.mark.parametrize(("text", "expected"), NEW_DESIGN_CASES)
def test_parse_new_design(text, expected):
    parsed, keywords_missing = parse_label_fields(text)
    fields = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "part", "antal")
    assert tuple(parsed[k] for k in fields) == expected
    assert not keywords_missing
    assert label_warnings(parsed, keywords_missing) == []


@pytest.mark.parametrize(
    ("text", "new"),
    [
        ("OUTLET·VARENR. 103469 Roots sofabord ORDRE NR. 156146 Stand: B Antal:1", True),
        ("103469 Roots sofabord ORDRE NR. 156146 Stand: B Antal:1", True),  # OUTLET·VARENR. not read
        ("103297, Holdbar spisebord OUTLET 152406 STAND:B", False),
        ("103291, Leonora spisebordsstol OUTLET Uden ordre nr. STAND: B", False),
    ],
)
def test_is_new_design(text, new):
    assert is_new_design(text) == new


def test_new_design_without_antal_is_reviewed():
    parsed, keywords_missing = parse_label_fields(
        "OUTLET·VARENR. 103469 Roots sofabord ORDRE NR. 156146 Stand: B"
    )
    assert parsed["antal"] == ""
    assert keywords_missing
    assert ("missing", "Kunne ikke læse antallet") in label_warnings(parsed, keywords_missing)


@pytest.mark.parametrize("antal", ["Antal: 1O", "Antal: 2 4", "Antal: l", "Antal: 0", "Antal:"])
def test_new_design_unclear_antal_is_not_guessed(antal):
    parsed, keywords_missing = parse_label_fields(
        f"OUTLET·VARENR. 103469 Roots sofabord ORDRE NR. 156146 {antal} Stand: B"
    )
    assert parsed["antal"] == ""
    assert ("missing", "Kunne ikke læse antallet") in label_warnings(parsed, keywords_missing)


def test_new_design_two_labels_in_one_photo_are_reviewed():
    # Two labels one above the other: the first label's fields read fine,
    # but the second label would silently be lost
    parsed, keywords_missing = parse_label_fields(
        "OUTLET·VARENR. 102686 Como puf ORDRE NR. 141807 Antal: 1 Stand: B "
        "OUTLET·VARENR. 106854 Oris sofabord ORDRE NR. 157498 Stand: A Antal:1"
    )
    assert keywords_missing
    assert "partial" in {category for category, _ in label_warnings(parsed, keywords_missing)}


def test_old_design_has_no_antal():
    parsed, keywords_missing = parse_label_fields("103297, Holdbar spisebord OUTLET 152406 STAND:B")
    assert "antal" not in parsed
    assert label_warnings(parsed, keywords_missing) == []


@pytest.mark.parametrize(("text", "expected"), PARSE_CASES)
def test_parse_label_fields(text, expected):
    parsed, _ = parse_label_fields(text)
    assert tuple(parsed[k] for k in ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "part")) == expected


def test_missing_keywords_are_reported():
    parsed, keywords_missing = parse_label_fields("102105 Venice sovesofa OUTLET FRA LAGER")
    categories = {category for category, _ in label_warnings(parsed, keywords_missing)}
    assert keywords_missing
    assert {"missing", "partial"} <= categories


def test_unusual_stand_is_suspicious():
    parsed, missing = parse_label_fields("AY H000022202, Vega sofabord OUTLET 135800 STAND: AP")
    assert ("suspicious", 'Usædvanlig STAND på labelen: "AP"') in label_warnings(parsed, missing)


@pytest.mark.parametrize(
    ("order_number", "suspicious"),
    [
        ("135800", False),
        ("FRA LAGER", False),
        ("LAGERVARE", False),
        ("#DE 2021", False),
        ("Uden ordre nr.", True),
        ("154197 sniseborus", True),  # box print read into the order number
    ],
)
def test_unusual_order_number_is_suspicious(order_number, suspicious):
    parsed, missing = parse_label_fields(f"103291, Leonora spisebordsstol OUTLET {order_number} STAND: B")
    assert parsed["mk_ordrenr"] == order_number
    assert any(category == "suspicious" for category, _ in label_warnings(parsed, missing)) == suspicious


@pytest.mark.parametrize(
    ("number", "name", "part"),
    [("101338_1/2", "Vega", "1/2"), ("96919", "2/2 Vega", "2/2"), ("101338", "Vega 90x90", "")],
)
def test_item_part(number, name, part):
    assert item_part(number, name) == part


# (name on the label, name on the webshop, same product?)
MATCH_CASES = [
    ("Plush spisebordsstol, Kobber - 1 stk.", "Plush spisebordsstol, Kobber", True),
    (
        "Leonora spisebordsstol, armlæn | Røget egetræ - 6 stk.",
        "Leonora spisebordsstol, armlæn | Røget egetræ",
        True,
    ),
    (
        "Marmoris spisebord, keramik 1200x100 Pandora/Hvid",
        "Marmoris spisebord, keramik | 200x100 Pandora/Hvid",
        True,
    ),
    (
        "Stanford LUX U-sovesofa, højrevendt Iopbevaring",
        "Stanford LUX U-sovesofa, højrevendt | opbevaring",
        True,
    ),
    ("Undra tæppe, 250x350 Beige", "Undra tæppe, 250×350 Beige", True),
    # Near misses the webshop's own search suggests - these must NOT match
    ("Bali chaiselong sofa, højrevendt Hygge Beige", "Cali chaiselong sofa, højrevendt Hygge Beige", False),
    ("Siesta tæppe, 140x200 Lyseblå/Mørkeblå", "Siesta tæppe, 200x300 Lyseblå/Mørkeblå", False),
    ("Ground TV-stander Natur egetræ", "Frame TV-stander Natur egetræ", False),
    ("Plush Esszimmerstuhl, Amiya Bouclé Braun", "Plush spisebordsstol, Amiya Bouclé Brun", False),
]


@pytest.mark.parametrize(("label", "shop", "same"), MATCH_CASES)
def test_name_matching(label, shop, same):
    assert (token_coverage(name_tokens(label), name_tokens(shop)) == 1.0) == same
