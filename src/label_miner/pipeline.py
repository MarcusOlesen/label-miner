"""One photo, end to end: read the label, parse it and look up the price."""

from pathlib import Path
from threading import Lock

from .choices import remembered_choice
from .designs import design_of
from .ocr import label_blocks_in_reading_order, label_box, load_label_photo, read_text_blocks
from .parsing import part_note
from .records import Candidate, Record
from .shop import format_combined_price, look_up_product, remembered_candidate
from .util import debug_log


def extract_label(
    engine, image_path: Path, session, cache: dict, cache_lock: Lock, choices: dict | None = None
) -> Record:
    """Read one label photo and find its product.

    Returns the photo's record: the label's text and fields, the price and
    link, a few display hints (photo_turn, label_box), the warnings (none
    means approved automatically), and the webshop products to offer in the
    review window.

    ``engine`` is a RapidOCR engine (see ocr.create_ocr_engine) and
    ``session`` a requests.Session; the tests pass recorded stand-ins. Runs
    on up to OCR_WORKERS threads at once, sharing one engine and one webshop
    cache (guarded by cache_lock). ``choices`` are the user's remembered
    choices (see choices.py): a label that goes to review gets its
    remembered product as the first suggestion.
    """
    image = load_label_photo(image_path)
    blocks = read_text_blocks(engine, image)
    label_blocks, turn = label_blocks_in_reading_order(blocks)
    if turn:
        # A sideways label: read it again upright. One extra OCR, for
        # these photos only (typically 1-2 in 30); see
        # label_blocks_in_reading_order for why reordering isn't enough.
        upright = image.rotate(turn, expand=True)
        upright_blocks = read_text_blocks(engine, upright)
        upright_label, _ = label_blocks_in_reading_order(upright_blocks)
        if upright_label is not upright_blocks:  # found OUTLET/STAND
            debug_log(f"{image_path.name}: sideways label, re-read turned {turn} degrees")
            image, blocks, label_blocks = upright, upright_blocks, upright_label
        else:
            turn = 0
    ignored = [block.text for block in blocks if block not in label_blocks]
    if ignored:
        debug_log(f"{image_path.name}: ignored text outside the label: {ignored}")
    text = " ".join(block.text for block in label_blocks)

    design = design_of(text)
    label, keywords_missing = design.parse(text)
    # The review window shows the photo turned the same way, so the
    # label is upright there too (and label_box matches it)
    record = Record(image_path, raw_text=text, label=label, design=design.name, photo_turn=turn)
    if label_blocks is not blocks and label_blocks:
        record.label_box = label_box(label_blocks, image.size)
    record.warnings = design.warnings(label, keywords_missing)

    shop_result, shop_warnings = look_up_product(
        session, cache, cache_lock, label.mk_varenummer, label.varenavn, label.part
    )
    record.warnings.extend(shop_warnings)
    record.candidates = shop_result.candidates
    if record.warnings:  # a remembered choice is only ever a suggestion, never an approval
        record.candidates = with_remembered_choice(
            session, cache, cache_lock, choices, label.mk_varenummer, label.varenavn, record.candidates
        )
    if shop_result.varenavn:
        # A certain match: use the shop's own name rather than the OCR text
        label.varenavn = shop_result.varenavn
    record.price = format_combined_price(shop_result.price, shop_result.compare_price)
    if label.part:
        record.price = part_note(label.part)
    record.compare_price = shop_result.compare_price
    record.url = shop_result.url
    return record


def with_remembered_choice(
    session,
    cache: dict,
    cache_lock: Lock,
    choices: dict | None,
    mk_varenummer: str,
    varenavn: str,
    candidates: list[Candidate],
) -> list[Candidate]:
    """The suggestions, with the product the user picked for this label
    before (see choices.py) first - if there is one and the shop still has
    it as it was."""
    choice = remembered_choice(choices, mk_varenummer, varenavn)
    if choice is None:
        return candidates
    remembered = remembered_candidate(session, cache, cache_lock, choice)
    if remembered is None:
        return candidates
    return [remembered] + [candidate for candidate in candidates if candidate.id != remembered.id]
