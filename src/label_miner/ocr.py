"""Reading the label: OCR with RapidOCR, then picking out the label's own lines.

OCR reads everything in the photo - the label, but also other stickers,
shipping labels and printing on the box. ``label_blocks_in_reading_order``
keeps only the text that belongs to the label and puts its lines in order,
using the layout of the label's design (see designs.py).
"""

import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .config import MAX_IMAGE_SIDE, OCR_WORKERS
from .designs import design_of
from .geometry import TextBlock, axes, direction_of, dot, parallel, span, turned_clockwise
from .parsing import LABEL_NUMBER_RE, OUTLET_RE, STAND_RE, WHITESPACE_RE
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

    OCR_WORKERS photos are read at once, and onnxruntime would give each of
    them every core of the PC - several times more threads than cores, which
    made the whole program (the window too) stutter while a batch was read.
    So each gets its share of the cores, leaving one for the window.
    """
    from rapidocr import RapidOCR

    cores = os.cpu_count() or 2
    threads = max(1, (cores - 1) // OCR_WORKERS)
    return RapidOCR(
        params={
            "Global.log_level": "warning",
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
    )


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

    RapidOCR finds text as pieces, each with four corner points that follow
    the text when it is tilted or sideways. A wide space can split one
    printed line into two pieces ("102162, Roots skænk," and "132x75"), and
    neither piece is centred on the label then, so pieces of the same line
    are joined again here: label_blocks_in_reading_order needs whole lines.
    The block is the upright box around the corner points."""
    result = engine(image)
    if result.boxes is None:
        return []
    lines = []  # the pieces of each line: [(text, corner points)]
    for points, piece_text in zip(result.boxes, result.txts, strict=True):
        text = WHITESPACE_RE.sub(" ", piece_text).strip()
        if not text:
            continue
        points = [(float(x), float(y)) for x, y in points]
        line = next((line for line in lines if any(_same_line(other, points) for _, other in line)), None)
        if line is None:
            lines.append([(text, points)])
        else:
            line.append((text, points))

    blocks = []
    for pieces in lines:
        along, _ = axes(direction_of(pieces[0][1]))
        pieces.sort(key=lambda piece: min(dot(point, along) for point in piece[1]))
        corners = [point for _, points in pieces for point in points]
        xs = [x for x, _ in corners]
        ys = [y for _, y in corners]
        text = " ".join(piece_text for piece_text, _ in pieces)
        blocks.append(
            TextBlock(text, min(xs), min(ys), max(xs), max(ys), direction_of(pieces[0][1]), corners)
        )
    return blocks


def _same_line(points, other_points) -> bool:
    """Whether two pieces are parts of one printed line: parallel, level
    with each other, and side by side - no more than a word space (half a
    line height) apart, and not on top of each other.

    "Level" only asks that the pieces overlap by half the smaller one's
    height: on a curved label the end of a line can sit a little higher
    than its start. So a tall piece right below a short line can count as
    level with it too - the new design's large item number under "OUTLET ·
    VARENR." when the number is long ("101935_2/3"). Pieces of one line
    never lie on top of each other, though, so those overlap along the
    line by far more than a little (half a line height, for boxes that
    touch)."""
    direction = direction_of(points)
    if not parallel(direction, direction_of(other_points)):
        return False
    along, across = axes(direction)

    (top, bottom), (other_top, other_bottom) = span(points, across), span(other_points, across)
    (start, end), (other_start, other_end) = span(points, along), span(other_points, along)
    line_height = min(bottom - top, other_bottom - other_top)
    level = min(bottom, other_bottom) - max(top, other_top) >= line_height / 2
    gap = max(other_start - end, start - other_end)
    return level and -line_height / 2 <= gap <= line_height / 2


def label_blocks_in_reading_order(blocks: list[TextBlock]) -> tuple[list[TextBlock], int]:
    """(label_blocks, turn): only the text blocks that belong to the
    label, in the order the label's lines are meant to be read - and, for
    a label photographed sideways, the rotation (degrees, PIL's
    counter-clockwise convention) that turns the photo upright, else 0.

    Photos often contain other text next to the label - a second sticker,
    a shipping label, printing on the box, handwriting - which would
    otherwise end up in the fields. And RapidOCR's own order doesn't always
    follow the lines. Which blocks are the label's own lines depends on its
    design (see LabelDesign.label_lines); the rest of this applies to every
    design.

    A label photographed sideways is read by the OCR line by line just
    fine - only the order comes out wrong - so its blocks are turned a
    quarter turn clockwise first and the same logic runs on them. The
    direction (which end is the first line) follows from STAND coming after
    OUTLET, or OUTLET coming after the item number; it also tells when that
    quarter turn was the wrong way round.

    RapidOCR reads sideways text less reliably, though ("90x90" as
    "06×06"), so extract_label re-reads a sideways photo turned upright
    (see ``turn``).

    Falls back to all blocks in RapidOCR's order when the design's anchor
    words (OUTLET, STAND) aren't there to anchor on."""
    design = design_of(" ".join(block.text for block in blocks))
    anchors = [block for block in blocks if design.anchor_re.search(block.text)]
    if not anchors:
        return blocks, 0
    # Every block is one line, and a line is much wider than tall unless
    # it is sideways
    sideways = sum(1 for block in anchors if block.height > block.width) * 2 > len(anchors)
    # From here on the label's lines run left to right, one below the other
    # (or upside down, see direction)
    upright = [turned_clockwise(block) for block in blocks] if sideways else list(blocks)
    original = {id(upright_block): block for upright_block, block in zip(upright, blocks, strict=True)}
    anchors = [block for block in upright if design.anchor_re.search(block.text)]

    label, reading_position = design.label_lines(upright, anchors)

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

    # The photo needs the same quarter turn clockwise (-90) - or, when that
    # left the label upside down, a quarter turn counter-clockwise (+90)
    turn = (-90 if direction == 1 else 90) if sideways else 0
    in_order = sorted(label, key=lambda block: reading_position(block, direction))
    return [original[id(block)] for block in in_order], turn


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
