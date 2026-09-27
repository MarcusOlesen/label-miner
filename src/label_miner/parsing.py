"""Splitting a label's text into its fields."""

import re

WHITESPACE_RE = re.compile(r"\s+")


PIPE_RE = re.compile(r"\s*\|\s*")


# A diameter as OCR reads it, with a zero instead of Ø: "090", "0120"
DIAMETER_DIGITS_RE = re.compile(r"(?<![\w/.,])0(\d{2,3})(?!\d)")


# A label reads, line by line:
#     <number>, <product name>        (or "<number> - <product name>")
#     OUTLET
#     <order number>
#     STAND: <A/B/C>
# The number is either an MK item number, possibly marked as only part of
# the item ("101338_1/2"), or a supplier code ("UE 45990201", "LI 600200049",
# "AY H000017492", "VN 19987-330" - the "-330" belongs to the code, it is
# not the separator before the name). Supplier codes have 7+ digits or a
# dash, so stray capitals in front of an MK number (box print read as
# "OM 103490") aren't mistaken for one.
LABEL_NUMBER_RE = re.compile(
    r"(?<![\w#])(?P<number>"
    r"[A-Z]{2} ?(?:[A-Z]?\d{7,}|\d{4,}-\d+)"
    r"|\d{5,6}(?:_\d+/\d+)?"
    r")(?!\d)\s*(?:,|-(?!\d))?"
)


# "137.017" - a smudge on the label read as a dot inside the order number
DIGIT_GROUP_DOT_RE = re.compile(r"(?<=\d)\.(?=\d{3}\b)")


OUTLET_RE = re.compile(r"\bOUTLET\b", flags=re.IGNORECASE)


STAND_RE = re.compile(r"\bSTAND\b\s*:?\s*(?P<stand>[A-Z]{1,3}\b)?", flags=re.IGNORECASE)


LABEL_ANCHOR_RE = re.compile(r"\b(?:OUTLET|STAND)\b", flags=re.IGNORECASE)


VALID_STANDS = {"A", "B", "C"}


# Only part of an item: "101338_1/2" / "1/2 Vega ..." = part 1 of 2. The
# other parts may come separately - or not at all.
PART_SUFFIX_RE = re.compile(r"_(?P<part>\d+/\d+)$")


PART_PREFIX_RE = re.compile(r"^(?P<part>\d+/\d+)\s+")


# A quantity tag at the end of the name: "... Naturflet - 1 stk."
STK_TAG_RE = re.compile(r"\s*-?\s*\b\d+\s*stk\b\.?\s*$", flags=re.IGNORECASE)


MISSING_FIELD_NAMES = {
    "mk_varenummer": "MK-varenummeret",
    "varenavn": "varenavnet",
    "mk_ordrenr": "MK-ordrenummeret",
    "stand": "standen",
}


def clean_varenavn(varenavn):
    varenavn = varenavn.replace("×", "x")
    varenavn = PIPE_RE.sub(" | ", varenavn)
    varenavn = DIAMETER_DIGITS_RE.sub(r"Ø\1", varenavn)
    return WHITESPACE_RE.sub(" ", varenavn).strip(" ,|-")


def part_note(part):
    """Put in the price/comment field of a part of an item instead of a price, so
    the export still says so even if the "1/2" disappears from the name
    (e.g. when the user picks the shop's name for it)."""
    return f"OBS: delvis vare (del {part})"


def item_part(mk_varenummer, varenavn):
    """ "1/2" when the label marks the item as only part of it - in the
    number ("101338_1/2") or at the start of the name ("1/2 Vega ...")."""
    match = PART_SUFFIX_RE.search((mk_varenummer or "").strip()) or PART_PREFIX_RE.search(
        (varenavn or "").strip()
    )
    return match["part"] if match else ""


def parse_label_fields(text):
    """Split a label's text (lines in reading order, see
    label_blocks_in_reading_order) into its fields:

        <number>, <name> OUTLET <order number> STAND: <A/B/C>

    Returns (fields, keywords_missing) - keywords_missing is True when
    OUTLET or STAND wasn't found, so the split is less certain. Fields
    that couldn't be read are left empty. The raw STAND value is kept even
    if it isn't A/B/C (e.g. "AP"), so the user sees what the label says."""
    parsed_label = {
        "mk_varenummer": "",
        "varenavn": "",
        "mk_ordrenr": "",
        "stand": "",
        "part": "",
    }
    outlet = OUTLET_RE.search(text)
    stand = STAND_RE.search(text)
    header_end = min([match.start() for match in (outlet, stand) if match is not None] or [len(text)])
    header = text[:header_end].strip()

    number = LABEL_NUMBER_RE.search(header)
    if number:
        parsed_label["mk_varenummer"] = WHITESPACE_RE.sub(" ", number["number"])
        # Anything before the number is either stray text or (rarely) a
        # line read out of order - the name normally follows the number
        after = header[number.end() :].strip()
        name = after or header[: number.start()]
    else:
        name = header
    parsed_label["varenavn"] = clean_varenavn(name)

    if outlet is not None:
        order_end = stand.start() if stand is not None and stand.start() > outlet.end() else len(text)
        parsed_label["mk_ordrenr"] = DIGIT_GROUP_DOT_RE.sub("", text[outlet.end() : order_end].strip(" ,:"))
    if stand is not None and stand["stand"]:
        parsed_label["stand"] = stand["stand"].upper()

    parsed_label["part"] = item_part(parsed_label["mk_varenummer"], parsed_label["varenavn"])
    return parsed_label, outlet is None or stand is None


def label_warnings(parsed_label, keywords_missing):
    """Warnings about the label text itself (the webshop lookup adds its own)."""
    warnings = [
        ("missing", f"Kunne ikke læse {label}")
        for key, label in MISSING_FIELD_NAMES.items()
        if not parsed_label[key]
    ]
    if keywords_missing:
        warnings.append(("partial", "Fandt ikke både OUTLET og STAND på labelen"))
    if parsed_label["stand"] and parsed_label["stand"] not in VALID_STANDS:
        warnings.append(("suspicious", f'Usædvanlig STAND på labelen: "{parsed_label["stand"]}"'))
    if parsed_label["mk_ordrenr"] and len(parsed_label["mk_ordrenr"].split()) > 4:
        warnings.append(("suspicious", "Mistænkelig tekst før MK-ordrenummer"))
    if parsed_label["part"]:
        warnings.append(
            (
                "part",
                f"Delvis vare (del {parsed_label['part']}) - der sættes ingen pris. "
                "Tjek at labelen er læst rigtigt.",
            )
        )
    return warnings
