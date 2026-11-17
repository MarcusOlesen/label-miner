"""The data that moves through the app, as typed records.

- ``Label``: what a label says - what a label design must deliver (see designs.py)
- ``Candidate``, ``Product``, ``ShopResult``: what the webshop lookup delivers (see shop.py)
- ``Record``: one photo, with its label, its product and its place in the review
"""

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

Warning = tuple[str, str]  # (category, message in Danish)


@dataclass
class Label:
    """What the label says, as read (and, after a review, as the user corrected it)."""

    mk_varenummer: str = ""
    varenavn: str = ""
    mk_ordrenr: str = ""
    stand: str = ""
    # The quantity - only the new design prints it (see LabelDesign.fields)
    antal: str = ""
    # "1/2" when the label marks the item as only part of it (see parsing.item_part)
    part: str = ""


@dataclass
class Candidate:
    """One variant (colour, size) of a webshop product: what is matched
    automatically, and what the review window offers as a card."""

    name: str
    price: str = ""
    compare_price: str = ""
    id: int | None = None
    sku: str = ""
    url: str = ""
    image: str = ""
    # How it was found, as the card says it: "nummer", "navn" or "valgt før"
    found_by: str = ""
    # How many times the user picked it for this label before (see choices.py)
    chosen_before: int = 0


@dataclass
class Product:
    """A webshop product, with a candidate per variant."""

    title: str
    url: str
    candidates: list[Candidate]


@dataclass
class ShopResult:
    """The webshop lookup's answer for a label. The name, price and link are
    only filled in for a product that is certain (see shop.look_up_product);
    otherwise ``candidates`` are the products to choose from."""

    varenavn: str = ""
    price: str = ""
    compare_price: str = ""
    url: str = ""
    candidates: list[Candidate] = field(default_factory=list)


# The fields the review window shows and the user can change
LABEL_FIELDS = ("mk_varenummer", "varenavn", "mk_ordrenr", "stand", "antal")
EDITABLE_FIELDS = LABEL_FIELDS + ("price",)


@dataclass
class Record:
    """One photo: what was read from it, the product found for it, and where
    it is in the review."""

    path: Path
    # The label's text in reading order, as the OCR read it
    raw_text: str = ""
    label: Label = field(default_factory=Label)
    # The name of the label's design (see designs.py); "" when the photo couldn't be read
    design: str = ""
    # The export's comments column: the price ("8.999 kr (12.999 kr)"), a
    # part's note, or what the user typed
    price: str = ""
    compare_price: str = ""
    url: str = ""
    # None left means approved
    warnings: list[Warning] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    # How the review window shows the photo: the rotation (degrees) that
    # turns a sideways label upright, and the label's area as fractions
    # (left, top, right, bottom) of the photo - None when it wasn't found
    photo_turn: int = 0
    label_box: tuple[float, float, float, float] | None = None
    # True while the record waits for (or is in) the batch review
    review_pending: bool = False
    # The photo as edited in the review window (a PIL image), for "Læs billedet igen"
    working_image: Any = None

    def editable_fields(self) -> dict[str, str]:
        """The fields of EDITABLE_FIELDS, as the review window shows them."""
        values = {key: getattr(self.label, key) for key in LABEL_FIELDS}
        values["price"] = self.price
        return values

    def update_fields(self, values: dict[str, str]) -> None:
        """Save the review window's fields (see editable_fields)."""
        for key in LABEL_FIELDS:
            setattr(self.label, key, values[key])
        self.price = values["price"]

    def take_reading(self, reading: "Record") -> None:
        """Replace everything that was read from the photo with a new
        reading of it (see extract_label). The photo itself and the record's
        place in the review stay."""
        kept = {"path", "review_pending", "working_image"}
        for attribute in fields(self):
            if attribute.name not in kept:
                setattr(self, attribute.name, getattr(reading, attribute.name))
