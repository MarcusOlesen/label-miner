"""What the label designs share when splitting a label's text into fields:
the number formats, the OCR slips that are safe to undo, parts of an item,
and what counts as a valid STAND and order number.

The designs themselves - where each field sits on the label - are in
designs.py. Pure functions on text.
"""

import re

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
    # Only the new design prints the quantity (see LabelDesign.fields)
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
    return f"OBS: delvis vare ({part})"


def item_part(mk_varenummer: str, varenavn: str) -> str:
    """ "1/2" when the label marks the item as only part of it - in the
    number ("101338_1/2") or at the start of the name ("1/2 Vega ...")."""
    match = PART_SUFFIX_RE.search((mk_varenummer or "").strip()) or PART_PREFIX_RE.search(
        (varenavn or "").strip()
    )
    return match["part"] if match else ""


def number_and_name(header: str) -> tuple[str, str]:
    """The label's number and the name after it, from the text in front of
    the order number."""
    number = LABEL_NUMBER_RE.search(header)
    if not number:
        return "", _clean_varenavn(header)
    # The name normally follows the number; text before it is stray text
    # or (rarely) a line read out of order.
    after = header[number.end() :].strip()
    return WHITESPACE_RE.sub(" ", number["number"]), _clean_varenavn(after or header[: number.start()])


def clean_order_number(order_number: str) -> str:
    return DIGIT_GROUP_DOT_RE.sub("", order_number.strip(" ,:"))
