"""The label designs: each one knows how to recognise itself, find its lines
in a photo and split its text into fields.

Two designs are in use side by side. The old one reads, line by line, every
line centred::

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

A new design is a new ``LabelDesign`` subclass, added to ``DESIGNS`` in
front of the designs it could be mistaken for. What the designs share - the
number formats, the checks on STAND and the order number - is in parsing.py.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable

from .geometry import TextBlock, axes, dot, parallel, span
from .parsing import (
    LABEL_ANCHOR_RE,
    MISSING_FIELD_NAMES,
    OUTLET_RE,
    STAND_RE,
    VALID_ORDER_NUMBER_RE,
    VALID_STANDS,
    clean_order_number,
    item_part,
    number_and_name,
)
from .records import Label, Warning

# A block's position among the label's lines: (block, direction) -> a number
# that grows from the first line to the last. direction is 1 when the first
# line is at the top, -1 when the label is upside down.
ReadingPosition = Callable[[TextBlock, int], float]


class LabelDesign(ABC):
    """One design of the shop's labels."""

    # Saved on the record ("old", "new")
    name: str
    # The fields the design prints: one that can't be read is reviewed
    fields: tuple[str, ...]
    # What the user is told when the design's words weren't all found
    keywords_message: str
    # The words that tell the label's text from everything else in a photo,
    # and which way up the label is (see ocr.label_blocks_in_reading_order)
    anchor_re = LABEL_ANCHOR_RE

    @abstractmethod
    def recognises(self, text: str) -> bool:
        """Whether this text is from a label in this design."""

    @abstractmethod
    def label_lines(
        self, blocks: list[TextBlock], anchors: list[TextBlock]
    ) -> tuple[list[TextBlock], ReadingPosition]:
        """(the blocks that are the label's own lines, their reading
        position). ``blocks`` are every line of text in the photo, turned so
        that the label's lines run left to right, one below the other (or
        upside down); ``anchors`` are those of them with the design's
        anchor words."""

    @abstractmethod
    def parse(self, text: str) -> tuple[Label, bool]:
        """Split a label's text (its lines in reading order, joined into
        one line) into its fields; a field that isn't found is empty.

        Returns (label, keywords_missing): keywords_missing is True when
        the design's words weren't all found, so the split is less certain.
        The STAND value is kept as printed even if it isn't A/B/C (e.g.
        "AP"), so the user sees what the label says."""

    def warnings(self, label: Label, keywords_missing: bool) -> list[Warning]:
        """Warnings about the label text itself (the webshop lookup adds its
        own, and decides whether only part of an item needs a look)."""
        warnings = [
            ("missing", f"Kunne ikke læse {name}")
            for key, name in MISSING_FIELD_NAMES.items()
            if key in self.fields and not getattr(label, key)
        ]
        if keywords_missing:
            warnings.append(("partial", self.keywords_message))
        if label.stand and label.stand not in VALID_STANDS:
            warnings.append(("suspicious", f'Usædvanlig STAND på labelen: "{label.stand}"'))
        if label.mk_ordrenr and not VALID_ORDER_NUMBER_RE.fullmatch(label.mk_ordrenr):
            warnings.append(("suspicious", f'Usædvanligt MK-ordrenummer: "{label.mk_ordrenr}"'))
        return warnings


class CentredDesign(LabelDesign):
    """The old design: every line centred around the words OUTLET and STAND."""

    name = "old"
    fields = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand")
    keywords_message = "Fandt ikke både OUTLET og STAND på labelen"

    def recognises(self, text: str) -> bool:
        """Any label that isn't in another design: this one has no words of
        its own to tell it by, so it comes last in DESIGNS."""
        return True

    def label_lines(self, blocks, anchors):
        """The label is found by starting from the block(s) containing
        OUTLET/STAND and growing outward to blocks that are (a) close to the
        label along the direction its lines advance, (b) centred on the same
        axis and (c) parallel to OUTLET/STAND. A sticker right next to the
        label fails (b); text further down the box fails (a); tilted print
        on the box fails (c) - its upright box is much larger than the text,
        so it can overlap the label. Measured on the fixture photos, the
        label's own lines are at most 0.06 label widths off its axis and the
        nearest stray text 0.2, so the limit is 0.12; and a gap of up to two
        lines still lets a line the OCR missed be skipped."""
        axis_x = sum(block.center_x for block in anchors) / len(anchors)
        line_height = min(block.height for block in anchors)
        label = list(anchors)
        added = True
        while added:
            added = False
            label_top = min(block.top for block in label)
            label_bottom = max(block.bottom for block in label)
            label_width = max(block.width for block in label)
            for block in blocks:
                if block in label:
                    continue
                gap = max(block.top - label_bottom, label_top - block.bottom, 0)
                off_axis = abs(block.center_x - axis_x)
                is_parallel = any(parallel(block.direction, anchor.direction) for anchor in anchors)
                if (
                    gap <= 2 * line_height
                    and off_axis <= 0.12 * max(label_width, block.width)
                    and is_parallel
                ):
                    label.append(block)
                    added = True

        def reading_position(block, direction):
            return block.top if direction == 1 else -block.bottom

        return label, reading_position

    def parse(self, text: str) -> tuple[Label, bool]:
        label = Label()
        outlet = OUTLET_RE.search(text)
        stand = STAND_RE.search(text)
        header_end = min([match.start() for match in (outlet, stand) if match is not None] or [len(text)])
        header = text[:header_end].strip()

        label.mk_varenummer, label.varenavn = number_and_name(header)

        if outlet is not None:
            order_end = stand.start() if stand is not None and stand.start() > outlet.end() else len(text)
            label.mk_ordrenr = clean_order_number(text[outlet.end() : order_end])
        if stand is not None and stand["stand"]:
            label.stand = stand["stand"].upper()

        label.part = item_part(label.mk_varenummer, label.varenavn)
        return label, outlet is None or stand is None


class LeftAlignedDesign(LabelDesign):
    """The new design: left-aligned lines from "OUTLET · VARENR." down to
    "Antal" on the left and "Stand" on the far right."""

    name = "new"
    fields = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "antal")
    keywords_message = (
        "Fandt ikke ordene VARENR., ORDRE NR., Antal og Stand præcis én gang hver - "
        "er der mere end én label på billedet?"
    )

    # The words only this design has. Capitals only, as printed: an old
    # label's order number can say "Uden ordre nr."
    RECOGNISE_RE = re.compile(r"\bVARENR\b|\bORDRE\s*NR\b")
    # The words that mark out the label's area (see label_lines)
    KEYWORD_RE = re.compile(r"\b(?:VARENR|ORDRE\s*NR|ANTAL|STAND)\b", flags=re.IGNORECASE)
    VARENR_RE = re.compile(r"\bVARENR\b\.?", flags=re.IGNORECASE)
    ORDRE_NR_RE = re.compile(r"\bORDRE\s*NR\b\.?", flags=re.IGNORECASE)
    ANTAL_RE = re.compile(r"\bANTAL\b\s*:?", flags=re.IGNORECASE)

    def recognises(self, text: str) -> bool:
        """It has the words VARENR. or ORDRE NR., which the old design
        never printed."""
        return self.RECOGNISE_RE.search(text) is not None

    def label_lines(self, blocks, anchors):
        """The label's printed words mark out its area: "OUTLET · VARENR."
        is the top line and starts at the left edge, "Antal" ends the left
        edge at the bottom and "Stand" sits at the far right of the bottom
        line. Every line on the label is parallel to them and starts inside
        that area; text outside it (box print, a second sticker,
        handwriting) does not. Measured along and across the label's own
        lines, so a tilted label works too."""
        keywords = [block for block in blocks if self.KEYWORD_RE.search(block.text)]
        label_direction = sorted(block.direction for block in keywords)[len(keywords) // 2]
        along, across = axes(label_direction)

        line_height = min(span(block.points, across)[1] - span(block.points, across)[0] for block in keywords)
        margin = line_height / 2
        left = min(span(block.points, along)[0] for block in keywords) - margin
        right = max(span(block.points, along)[1] for block in keywords) + margin
        top = min(span(block.points, across)[0] for block in keywords) - margin
        bottom = max(span(block.points, across)[1] for block in keywords) + margin

        label = []
        for block in blocks:
            start = span(block.points, along)[0]
            middle = sum(span(block.points, across)) / 2
            if (
                left <= start <= right
                and top <= middle <= bottom
                and parallel(block.direction, label_direction)
            ):
                label.append(block)

        def reading_position(block, direction):
            # Across the label's own lines: on a tilted label a long line's
            # upright box reaches up past the line above it
            return sum(dot(point, across) for point in block.points) / len(block.points) * direction

        return label, reading_position

    def parse(self, text: str) -> tuple[Label, bool]:
        """Each field sits after its own printed word, so the number and
        name are what follows VARENR., the order number what follows ORDRE
        NR. - up to Antal/Stand on the label's bottom line."""
        label = Label()
        varenr = self.VARENR_RE.search(text)
        ordre_nr = self.ORDRE_NR_RE.search(text)
        antal = self.ANTAL_RE.search(text)
        stand = STAND_RE.search(text)
        bottom_line = min([match.start() for match in (antal, stand) if match is not None] or [len(text)])

        if varenr is not None:
            header_end = bottom_line
            if ordre_nr is not None and ordre_nr.start() > varenr.end():
                header_end = ordre_nr.start()
            header = text[varenr.end() : max(header_end, varenr.end())].strip()
            label.mk_varenummer, label.varenavn = number_and_name(header)
        if ordre_nr is not None:
            order_number = text[ordre_nr.end() : max(bottom_line, ordre_nr.end())]
            label.mk_ordrenr = clean_order_number(order_number)
        if stand is not None and stand["stand"]:
            label.stand = stand["stand"].upper()
        if antal is not None:
            # All of what follows Antal on its line must be the number: "1O"
            # (24 read as) "2 4" or a smudge must not quietly become a quantity
            antal_end = stand.start() if stand is not None and stand.start() > antal.end() else len(text)
            value = text[antal.end() : antal_end].strip()
            if value.isdigit() and int(value) > 0:
                label.antal = str(int(value))

        label.part = item_part(label.mk_varenummer, label.varenavn)
        # Each word exactly once: twice means two labels were read as one (stacked
        # or side by side), and the second one would be lost
        keywords_missing = any(
            len(word.findall(text)) != 1
            for word in (self.VARENR_RE, self.ORDRE_NR_RE, self.ANTAL_RE, STAND_RE)
        )
        return label, keywords_missing


# Tried in this order: the first design that recognises the text. The old
# design has no words of its own, so it is last and takes everything else.
DESIGNS: tuple[LabelDesign, ...] = (LeftAlignedDesign(), CentredDesign())


def design_of(text: str) -> LabelDesign:
    """The design of the label this text is from."""
    return next(design for design in DESIGNS if design.recognises(text))
