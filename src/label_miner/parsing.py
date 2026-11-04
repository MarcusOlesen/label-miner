"""Splitting a label's text into its fields.

Two label designs are in use side by side. The old one reads, line by
line, every line centred::

    <number>, <product name>        (or "<number> - <product name>")
    OUTLET
    <order number>
    STAND: <A/B/C>

The new one (from November 2026) is left-aligned, with the words for each
field printed on the label::

    OUTLET · VARENR.
    <number>
    <product name>                  (may run over two lines)
    ORDRE NR.
    <order number>
    -----------------------
    Antal: <quantity>        Stand: <A/B/C>

The design is told by its words: VARENR. and ORDRE NR. are only on the new
one (see ``is_new_design``). The input is the label's text in reading order
(see ``ocr.label_blocks_in_reading_order``), joined into one line.
"""

import re

ParsedLabel = dict[str, str]
Warning = tuple[str, str]  # (category, message in Danish)

WHITESPACE_RE = re.compile(r"\s+")
PIPE_RE = re.compile(r"\s*\|\s*")

# The number is either an MK item number, possibly marked as only part of
# the item ("101338_1/2"), or a supplier code ("UE 45990201", "LI 600200049",
# "AY H000017492", "VN 19987-330" - the "-330" belongs to the code, it is
# not the separator before the name). Supplier codes need 7+ digits or a
# dash, so stray capitals from box print in front of an MK number ("OM
# 103490") aren't taken for one.
LABEL_NUMBER_RE = re.compile(
    r"(?<![\w#])(?P<number>"
    r"[A-Z]{2} ?(?:[A-Z]?\d{7,}|\d{4,}-\d+)"
    r"|\d{5,6}(?:_\d+/\d+)?"
    r")(?!\d)\s*(?:,|-(?!\d))?"
)
OUTLET_RE = re.compile(r"\bOUTLET\b", flags=re.IGNORECASE)
STAND_RE = re.compile(r"\bSTAND\b\s*:?\s*(?P<stand>[A-Z]{1,3}\b)?", flags=re.IGNORECASE)
# The two words that identify a label's text among everything else in a photo
LABEL_ANCHOR_RE = re.compile(r"\b(?:OUTLET|STAND)\b", flags=re.IGNORECASE)
# The words only the new design has, and those that mark out its area
# (see ocr._left_aligned_label). Capitals only, as printed: an old label's
# order number can say "Uden ordre nr."
NEW_DESIGN_RE = re.compile(r"\bVARENR\b|\bORDRE\s*NR\b")
NEW_DESIGN_KEYWORD_RE = re.compile(r"\b(?:VARENR|ORDRE\s*NR|ANTAL|STAND)\b", flags=re.IGNORECASE)
VARENR_RE = re.compile(r"\bVARENR\b\.?", flags=re.IGNORECASE)
ORDRE_NR_RE = re.compile(r"\bORDRE\s*NR\b\.?", flags=re.IGNORECASE)
ANTAL_RE = re.compile(r"\bANTAL\b\s*:?\s*(?P<antal>\d+)?", flags=re.IGNORECASE)
VALID_STANDS = {"A", "B", "C"}
# An order number is a number (German orders: "#DE 2021"), or says the item
# came from stock. Anything else ("Uden ordre nr.", stray text read into it)
# is reviewed.
VALID_ORDER_NUMBER_RE = re.compile(r"(?:#DE )?\d+|(?:FRA )?LAGER(?:VARE)?", flags=re.IGNORECASE)

# OCR misreadings that are safe to undo:
# a diameter read with a zero instead of Ø ("090", "0120")
DIAMETER_DIGITS_RE = re.compile(r"(?<![\w/.,])0(\d{2,3})(?!\d)")
# a smudge read as a dot inside the order number ("137.017")
DIGIT_GROUP_DOT_RE = re.compile(r"(?<=\d)\.(?=\d{3}\b)")

# Only part of an item: "101338_1/2" or "1/2 Vega ..." (part 1 of 2). The
# other parts may come separately - or not at all - so it never gets a price.
PART_SUFFIX_RE = re.compile(r"_(?P<part>\d+/\d+)$")
PART_PREFIX_RE = re.compile(r"^(?P<part>\d+/\d+)\s+")

# A quantity tag at the end of the name: "... Naturflet - 1 stk."
STK_TAG_RE = re.compile(r"\s*-?\s*\b\d+\s*stk\b\.?\s*$", flags=re.IGNORECASE)

# Field name -> how the warnings refer to it (Danish)
MISSING_FIELD_NAMES = {
    "mk_varenummer": "MK-varenummeret",
    "varenavn": "varenavnet",
    "mk_ordrenr": "MK-ordrenummeret",
    "stand": "standen",
    # Only the new design prints the quantity; an old label's fields have no "antal"
    "antal": "antallet",
}


def _clean_varenavn(varenavn: str) -> str:
    varenavn = varenavn.replace("×", "x")
    # RapidOCR sometimes reads å as à ("Aragon Blà"), a letter no Danish or
    # German product name has
    varenavn = varenavn.replace("à", "å").replace("À", "Å")
    varenavn = PIPE_RE.sub(" | ", varenavn)
    varenavn = DIAMETER_DIGITS_RE.sub(r"Ø\1", varenavn)
    return WHITESPACE_RE.sub(" ", varenavn).strip(" ,|-")


def part_note(part: str) -> str:
    """The comment a part of an item gets instead of a price. It keeps the
    export clear even if the "1/2" disappears from the name (e.g. when the
    shop's name is picked in the review window)."""
    return f"OBS: delvis vare (del {part})"


def item_part(mk_varenummer: str, varenavn: str) -> str:
    """ "1/2" when the label marks the item as only part of it - in the
    number ("101338_1/2") or at the start of the name ("1/2 Vega ...")."""
    match = PART_SUFFIX_RE.search((mk_varenummer or "").strip()) or PART_PREFIX_RE.search(
        (varenavn or "").strip()
    )
    return match["part"] if match else ""


def is_new_design(text: str) -> bool:
    """Whether the text is from a label in the new, left-aligned design:
    it has the words VARENR. or ORDRE NR., which the old design never
    printed."""
    return NEW_DESIGN_RE.search(text) is not None


def _number_and_name(header: str) -> tuple[str, str]:
    """The label's number and the name after it, from the text in front of
    the order number."""
    number = LABEL_NUMBER_RE.search(header)
    if not number:
        return "", _clean_varenavn(header)
    # The name normally follows the number; text before it is stray text
    # or (rarely) a line read out of order.
    after = header[number.end() :].strip()
    return WHITESPACE_RE.sub(" ", number["number"]), _clean_varenavn(after or header[: number.start()])


def _clean_order_number(order_number: str) -> str:
    return DIGIT_GROUP_DOT_RE.sub("", order_number.strip(" ,:"))


def parse_label_fields(text: str) -> tuple[ParsedLabel, bool]:
    """Split a label's text into mk_varenummer, varenavn, mk_ordrenr, stand,
    part and - new design only - antal (empty when not found).

    Returns (fields, keywords_missing): keywords_missing is True when the
    label's words (OUTLET and STAND; on the new design VARENR., ORDRE NR.,
    Antal and Stand) weren't all found, so the split is less certain. The
    STAND value is kept as printed even if it isn't A/B/C (e.g. "AP"), so
    the user sees what the label says.
    """
    if is_new_design(text):
        return _parse_new_design(text)
    parsed_label = {"mk_varenummer": "", "varenavn": "", "mk_ordrenr": "", "stand": "", "part": ""}
    outlet = OUTLET_RE.search(text)
    stand = STAND_RE.search(text)
    header_end = min([match.start() for match in (outlet, stand) if match is not None] or [len(text)])
    header = text[:header_end].strip()

    parsed_label["mk_varenummer"], parsed_label["varenavn"] = _number_and_name(header)

    if outlet is not None:
        order_end = stand.start() if stand is not None and stand.start() > outlet.end() else len(text)
        parsed_label["mk_ordrenr"] = _clean_order_number(text[outlet.end() : order_end])
    if stand is not None and stand["stand"]:
        parsed_label["stand"] = stand["stand"].upper()

    parsed_label["part"] = item_part(parsed_label["mk_varenummer"], parsed_label["varenavn"])
    return parsed_label, outlet is None or stand is None


def _parse_new_design(text: str) -> tuple[ParsedLabel, bool]:
    """The new design: each field sits after its own printed word, so the
    number and name are what follows VARENR., the order number what
    follows ORDRE NR. - up to Antal/Stand on the label's bottom line."""
    parsed_label = {
        "mk_varenummer": "",
        "varenavn": "",
        "mk_ordrenr": "",
        "stand": "",
        "part": "",
        "antal": "",
    }
    varenr = VARENR_RE.search(text)
    ordre_nr = ORDRE_NR_RE.search(text)
    antal = ANTAL_RE.search(text)
    stand = STAND_RE.search(text)
    bottom_line = min([match.start() for match in (antal, stand) if match is not None] or [len(text)])

    if varenr is not None:
        header_end = bottom_line
        if ordre_nr is not None and ordre_nr.start() > varenr.end():
            header_end = ordre_nr.start()
        header = text[varenr.end() : max(header_end, varenr.end())].strip()
        parsed_label["mk_varenummer"], parsed_label["varenavn"] = _number_and_name(header)
    if ordre_nr is not None:
        order_number = text[ordre_nr.end() : max(bottom_line, ordre_nr.end())]
        parsed_label["mk_ordrenr"] = _clean_order_number(order_number)
    if stand is not None and stand["stand"]:
        parsed_label["stand"] = stand["stand"].upper()
    if antal is not None and antal["antal"]:
        parsed_label["antal"] = str(int(antal["antal"]))

    parsed_label["part"] = item_part(parsed_label["mk_varenummer"], parsed_label["varenavn"])
    keywords_missing = None in (varenr, ordre_nr, antal, stand)
    return parsed_label, keywords_missing


def label_warnings(parsed_label: ParsedLabel, keywords_missing: bool) -> list[Warning]:
    """Warnings about the label text itself (the webshop lookup adds its own,
    and decides whether only part of an item needs a look)."""
    warnings = [
        ("missing", f"Kunne ikke læse {label}")
        for key, label in MISSING_FIELD_NAMES.items()
        if key in parsed_label and not parsed_label[key]
    ]
    if keywords_missing:
        if "antal" in parsed_label:
            message = "Fandt ikke alle ordene VARENR., ORDRE NR., Antal og Stand på labelen"
        else:
            message = "Fandt ikke både OUTLET og STAND på labelen"
        warnings.append(("partial", message))
    if parsed_label["stand"] and parsed_label["stand"] not in VALID_STANDS:
        warnings.append(("suspicious", f'Usædvanlig STAND på labelen: "{parsed_label["stand"]}"'))
    if parsed_label["mk_ordrenr"] and not VALID_ORDER_NUMBER_RE.fullmatch(parsed_label["mk_ordrenr"]):
        warnings.append(("suspicious", f'Usædvanligt MK-ordrenummer: "{parsed_label["mk_ordrenr"]}"'))
    return warnings
