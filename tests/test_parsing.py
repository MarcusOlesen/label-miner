"""Label text -> fields, and label name vs. webshop name. Real OCR output from the test photos."""

import pytest

from label_miner.parsing import item_part, label_warnings, parse_label_fields
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


def test_part_of_item_is_always_flagged():
    parsed, missing = parse_label_fields("101338_1/2, Vega sofabord OUTLET 149426 STAND: B")
    assert any(category == "part" for category, _ in label_warnings(parsed, missing))


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
