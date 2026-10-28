"""Reading the label: OCR with RapidOCR, then picking out the label's own lines.

OCR reads everything in the photo - the label, but also other stickers,
shipping labels and printing on the box. ``label_blocks_in_reading_order``
keeps only the text that belongs to the label and puts its lines in order,
using the label's layout: every line is centred, around the words OUTLET and
STAND.
"""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .config import MAX_IMAGE_SIDE
from .parsing import LABEL_ANCHOR_RE, LABEL_NUMBER_RE, OUTLET_RE, STAND_RE, WHITESPACE_RE
from .util import debug_log

# iPhone photos are HEIC; this teaches Pillow to open them.
register_heif_opener()


def create_ocr_engine():  # -> rapidocr.RapidOCR
    """The OCR engine: RapidOCR on onnxruntime, with its default models.

    RapidOCR used to run inside Docling, whose layout model and PyTorch
    added ~2 GB and a slow start, while the labels only need the OCR itself.
    The models ship inside the rapidocr package, so the app never downloads
    anything, and the pinned rapidocr version fixes which models (and so
    which results) it gets.
    """
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


@dataclass
class TextBlock:
    """One block of OCR'd text (a line or a paragraph) and where it sits in
    the photo, in pixels from the top-left corner."""

    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


def load_label_photo(image_path: Path) -> Image.Image:
    """The photo as upright RGB, no larger than MAX_IMAGE_SIDE.

    Phone photos often store their pixels sideways plus an EXIF orientation
    tag. The OCR is given pixels, not the file, so the photo is turned
    upright here - the same way the review window shows it.
    """
    with Image.open(image_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return image


def save_temporary_png(image: Image.Image, temporary_paths: list[Path]) -> Path:
    """Save the image as a new temporary PNG and add its path to
    temporary_paths; see remove_temporary_files. A "Prøv igen" passes the
    edited photo to extract_label this way."""
    handle, name = tempfile.mkstemp(suffix=".png")
    os.close(handle)
    temporary_path = Path(name)
    temporary_paths.append(temporary_path)
    image.save(temporary_path, format="PNG")
    return temporary_path


def remove_temporary_files(temporary_paths: list[Path]) -> None:
    for temporary_path in temporary_paths:
        try:
            os.unlink(temporary_path)
        except OSError as error:
            debug_log(f"Could not delete temporary file {temporary_path}: {error}")


def read_text_blocks(engine, image: Image.Image) -> list[TextBlock]:
    """OCR one image, as one TextBlock per line of text in RapidOCR's order.

    RapidOCR gives each line as four corner points, which follow the text
    when it is tilted or sideways; the block is the upright box around
    them."""
    result = engine(image)
    if result.boxes is None:
        return []
    blocks = []
    for points, line_text in zip(result.boxes, result.txts, strict=True):
        text = WHITESPACE_RE.sub(" ", line_text).strip()
        if not text:
            continue
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        blocks.append(TextBlock(text, min(xs), min(ys), max(xs), max(ys)))
    return blocks


def label_blocks_in_reading_order(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    """(label_blocks, turn): only the text blocks that belong to the
    label, in the order the label's lines are meant to be read - and, for
    a label photographed sideways, the rotation (degrees, PIL's
    counter-clockwise convention) that turns the photo upright, else 0.

    Photos often contain other text next to the label - a second sticker,
    a shipping label, printing on the box, handwriting - which would
    otherwise end up in the fields. And RapidOCR's own order doesn't always
    follow the lines.

    Labels are printed with every line centred, so the label is found by
    starting from the block(s) containing OUTLET/STAND and growing outward
    to blocks that are (a) close to the label along the direction its lines
    advance and (b) centred on the same axis. A sticker right next to the
    label fails (b); text further down the box fails (a). Measured on the
    fixture photos, the label's own lines are at most 0.06 label widths off
    its axis and the nearest stray text 0.2, so the limit is 0.12; and a
    gap of up to two lines still lets a line the OCR missed be skipped.

    A label photographed sideways is read by the OCR line by line just
    fine - only the order comes out wrong - so its blocks are turned a
    quarter turn clockwise first and the same logic runs on them. The
    direction (which end is the first line) follows from STAND coming after
    OUTLET, or OUTLET coming after the item number; it also tells when that
    quarter turn was the wrong way round.

    RapidOCR reads sideways text less reliably, though ("90x90" as
    "06×06"), so extract_label re-reads a sideways photo turned upright
    (see ``turn``).

    Falls back to all blocks in RapidOCR's order when there is no OUTLET or
    STAND to anchor on."""
    anchors = [block for block in blocks if LABEL_ANCHOR_RE.search(block.text)]
    if not anchors:
        return blocks, 0
    # Every block is one line, and a line is much wider than tall unless
    # it is sideways
    sideways = sum(1 for block in anchors if block.height > block.width) * 2 > len(anchors)
    # From here on the label's lines run left to right, one below the other
    # (or upside down, see direction)
    upright = [turned_clockwise(block) for block in blocks] if sideways else list(blocks)
    original = {id(upright_block): block for upright_block, block in zip(upright, blocks, strict=True)}
    anchors = [block for block in upright if LABEL_ANCHOR_RE.search(block.text)]

    axis_x = sum(block.center_x for block in anchors) / len(anchors)
    line_height = min(block.height for block in anchors)
    label = list(anchors)
    added = True
    while added:
        added = False
        label_top = min(block.top for block in label)
        label_bottom = max(block.bottom for block in label)
        label_width = max(block.width for block in label)
        for block in upright:
            if block in label:
                continue
            gap = max(block.top - label_bottom, label_top - block.bottom, 0)
            off_axis = abs(block.center_x - axis_x)
            if gap <= 2 * line_height and off_axis <= 0.12 * max(label_width, block.width):
                label.append(block)
                added = True

    outlet = next((block for block in label if OUTLET_RE.search(block.text)), None)
    stand = next((block for block in label if STAND_RE.search(block.text)), None)
    number = next(
        (block for block in label if LABEL_NUMBER_RE.search(block.text) and block not in (outlet, stand)),
        None,
    )
    # 1: the first line is at the top; -1: the label is upside down
    if outlet is not None and stand is not None and outlet is not stand:
        direction = 1 if stand.center_y > outlet.center_y else -1
    elif number is not None:
        direction = 1 if (outlet or stand).center_y > number.center_y else -1
    else:
        direction = -1 if sideways else 1

    def reading_position(block):
        return block.top if direction == 1 else -block.bottom

    # The photo needs the same quarter turn clockwise (-90) - or, when that
    # left the label upside down, a quarter turn counter-clockwise (+90)
    turn = (-90 if direction == 1 else 90) if sideways else 0
    return [original[id(block)] for block in sorted(label, key=reading_position)], turn


def turned_clockwise(block: TextBlock) -> TextBlock:
    """The block as it sits in the photo turned a quarter turn clockwise:
    right becomes down. Only positions relative to other blocks matter, so
    the photo's own width isn't needed (the turned block is shifted by it)."""
    return TextBlock(block.text, -block.bottom, block.left, -block.top, block.right)


def label_box(
    label_blocks: list[TextBlock], image_size: tuple[int, int]
) -> tuple[float, float, float, float]:
    """The label's area as fractions (left, top, right, bottom) of the
    photo, with a margin around its text - the review window zooms in on
    this. Fractions, so they apply to the full-resolution photo too."""
    width, height = image_size
    left = min(block.left for block in label_blocks)
    top = min(block.top for block in label_blocks)
    right = max(block.right for block in label_blocks)
    bottom = max(block.bottom for block in label_blocks)
    margin = 0.35 * max(right - left, bottom - top)
    return (
        max(0.0, (left - margin) / width),
        max(0.0, (top - margin) / height),
        min(1.0, (right + margin) / width),
        min(1.0, (bottom + margin) / height),
    )
