"""Reading the label: OCR with Docling and picking out the label's own lines."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .config import MAX_IMAGE_SIDE
from .parsing import LABEL_ANCHOR_RE, LABEL_NUMBER_RE, OUTLET_RE, STAND_RE, WHITESPACE_RE
from .util import debug_log, resource_path

# iPhone photos are HEIC; this teaches Pillow to open them.
register_heif_opener()


def create_converter():
    """A Docling converter for label photos, with the OCR engine pinned.

    RapidOCR (torch backend) is pinned instead of Docling's "auto" choice:
    auto picks whichever OCR engine happens to be installed, and with
    EasyOCR installed it silently switches to an English-only recogniser
    that reads Danish labels measurably worse (it dropped leading item
    numbers like "103035 -"). Pinning keeps the results the same on every
    machine.

    Models come from a local docling-models folder when there is one
    (bundled in the installer), so the app never needs to download them;
    otherwise Docling uses its normal Hugging Face cache.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=False,
        do_picture_classification=False,
        do_picture_description=False,
        images_scale=1.0,
        generate_page_images=False,
        generate_picture_images=False,
        ocr_options=RapidOcrOptions(backend="torch"),
    )
    models_dir = resource_path("docling-models")
    if models_dir.is_dir():
        pipeline_options.artifacts_path = models_dir
        debug_log(f"Using bundled Docling models from {models_dir}")
    else:
        debug_log("No docling-models folder found; Docling uses its Hugging Face cache.")
    return DocumentConverter(
        allowed_formats=[InputFormat.IMAGE],
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options)},
    )


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
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def center_x(self):
        return (self.left + self.right) / 2

    @property
    def center_y(self):
        return (self.top + self.bottom) / 2


def load_label_photo(image_path):
    """The photo as upright RGB, no larger than MAX_IMAGE_SIDE.

    Phone photos (HEIC and plain JPEG alike) very commonly store pixels
    "sideways" plus an EXIF Orientation tag rather than upright pixels -
    ReviewWindow.show_image() corrects for this before *displaying* a
    photo (via the identical ImageOps.exif_transpose() call), and Docling
    opens images with plain PIL.Image.open(...).convert("RGB") and never
    looks at EXIF, so it has to happen here before OCR as well - otherwise
    a rotated-by-EXIF label gets OCR'd sideways or upside down while
    looking perfectly upright in the review window."""
    with Image.open(image_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    return image


def save_temporary_png(image, temporary_paths: list[Path]) -> Path:
    """Save the image as a new temporary PNG (Docling reads files, not images)
    and add its path to temporary_paths; see remove_temporary_files."""
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


def read_text_blocks(converter, image_path):
    """OCR one image with Docling, as TextBlocks in Docling's own order.

    One Docling text item can be several pieces of text from different
    places in the photo, merged: it then has one `prov` entry per piece,
    each with its own box and a `charspan` saying which characters of the
    item's text are in it. Each piece becomes its own TextBlock here.
    (Using only the first box for the whole item once put the entire label
    of IMG_0277 at the position of a 2-letter scrap of packaging text: the
    zoom showed that scrap, and the packaging text was read as part of the
    label.)"""
    document = converter.convert(str(image_path)).document
    page = next(iter(document.pages.values()), None)
    blocks = []
    for item in document.texts:
        if not item.text or not item.prov:
            continue
        pieces = [(item.text, item.prov[0].bbox)]
        if len(item.prov) > 1:
            pieces = [(item.text[prov.charspan[0] : prov.charspan[1]], prov.bbox) for prov in item.prov]
        for piece_text, box in pieces:
            text = WHITESPACE_RE.sub(" ", piece_text).strip()
            if not text:
                continue
            if page is not None:
                box = box.to_top_left_origin(page.size.height)
            blocks.append(TextBlock(text, box.l, box.t, box.r, box.b))
    return blocks


def estimated_line_height(block, along_span):
    """Height of one line of text in `block` (in the direction lines
    advance). A block can hold several lines - IMG_0277's whole label is a
    single block - so its full height is often far more than a line.
    Estimated from how much area its characters fill: with this font a
    character is about half a line high wide, and lines fill roughly 70 %
    of the block's width, so area ≈ chars × 0.55 h × h / 0.7."""
    low, high = along_span(block)
    extent = high - low
    area_estimate = (1.27 * block.width * block.height / max(len(block.text), 1)) ** 0.5
    return min(extent, area_estimate)


def label_blocks_in_reading_order(blocks):
    """(label_blocks, turn): only the text blocks that belong to the
    label, in the order the label's lines are meant to be read - and, for
    a label photographed sideways, the rotation (degrees, PIL's
    counter-clockwise convention) that turns the photo upright, else 0.

    Photos often contain other text next to the label - a second sticker,
    a shipping label, printing on the box, handwriting - which used to end
    up inside the parsed fields. And Docling's own block order sometimes
    puts "OUTLET" before the product name, which breaks parsing too.

    Labels are printed with every line centred, so the label is found by
    starting from the block(s) containing OUTLET/STAND and growing outward
    to blocks that are (a) close to the label along the direction its lines
    advance and (b) centred on the same axis. A sticker right next to the
    label fails (b); text further down the box fails (a).

    A label photographed sideways is read by the OCR line by line just
    fine - only the order comes out wrong - so the same logic runs with the
    axes swapped instead of re-reading a rotated copy of the photo. The
    direction (which end is the first line) follows from STAND coming after
    OUTLET, or OUTLET coming after the item number.

    Ordering is only as good as the blocks, though: Docling sometimes
    merges several sideways lines into one block with the lines in the
    wrong order inside it (IMG_0285), which no reordering can fix. So
    extract_label re-reads a sideways photo turned upright (see `turn`).

    Falls back to all blocks in Docling's order when there is no OUTLET or
    STAND to anchor on."""
    anchors = [block for block in blocks if LABEL_ANCHOR_RE.search(block.text)]
    if not anchors:
        return blocks, 0
    # Orientation is judged only from short, single-line anchors ("OUTLET",
    # "STAND: B"), which are much taller than wide when sideways. A block
    # holding several lines can be any shape (IMG_9168's whole upright
    # label is one block, slightly taller than wide) - with no short
    # anchor the label is taken to be upright, the normal case.
    single_lines = [block for block in anchors if len(block.text) <= 15]
    sideways = bool(single_lines) and (
        sum(1 for block in single_lines if block.height > block.width) * 2 > len(single_lines)
    )

    def along(block):  # position in the direction the label's lines advance
        return block.center_x if sideways else block.center_y

    def along_span(block):
        return (block.left, block.right) if sideways else (block.top, block.bottom)

    def across(block):  # position along a line
        return block.center_y if sideways else block.center_x

    def across_size(block):
        return block.height if sideways else block.width

    axis = sum(across(block) for block in anchors) / len(anchors)
    line_size = min(estimated_line_height(block, along_span) for block in anchors)
    label = list(anchors)
    added = True
    while added:
        added = False
        start = min(along_span(block)[0] for block in label)
        end = max(along_span(block)[1] for block in label)
        label_width = max(across_size(block) for block in label)
        for block in blocks:
            if block in label:
                continue
            low, high = along_span(block)
            gap = max(low - end, start - high, 0)
            off_axis = abs(across(block) - axis)
            if gap <= 2 * line_size and off_axis <= 0.2 * max(label_width, across_size(block)):
                label.append(block)
                added = True

    outlet = next((block for block in label if OUTLET_RE.search(block.text)), None)
    stand = next((block for block in label if STAND_RE.search(block.text)), None)
    number = next(
        (block for block in label if LABEL_NUMBER_RE.search(block.text) and block not in (outlet, stand)),
        None,
    )
    if outlet is not None and stand is not None and outlet is not stand:
        direction = 1 if along(stand) > along(outlet) else -1
    elif number is not None:
        direction = 1 if along(outlet or stand) > along(number) else -1
    else:
        direction = -1 if sideways else 1

    def reading_position(block):
        low, high = along_span(block)
        return low if direction == 1 else -high

    # Sideways text whose lines advance to the left was turned clockwise -
    # turn it back counter-clockwise (+90), and the other way round
    turn = (90 if direction == -1 else -90) if sideways else 0
    return sorted(label, key=reading_position), turn


def label_area(label_blocks, image_size):
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
