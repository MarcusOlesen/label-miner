"""One photo, end to end: read the label, parse it and look up the price."""

from pathlib import Path
from threading import Lock

from .ocr import label_area, label_blocks_in_reading_order, load_label_photo, read_text_blocks
from .parsing import label_warnings, parse_label_fields, part_note
from .shop import format_combined_price, lookup_shop_price
from .util import debug_log


def extract_label(
    engine, image_path: Path, session, cache: dict, cache_lock: Lock
) -> tuple[str, dict, list[tuple[str, str]], list[dict]]:
    """Read one label photo and find its product.

    Returns (text, fields, warnings, candidates): the label's text, the
    parsed fields plus price/url and a few display hints (photo_turn,
    label_box), the warnings (none means approved automatically), and the
    webshop products to offer in the review window.

    ``engine`` is a RapidOCR engine (see ocr.create_ocr_engine) and
    ``session`` a requests.Session; the tests pass recorded stand-ins. Runs
    on up to OCR_WORKERS threads at once, sharing one engine and one webshop
    cache (guarded by cache_lock).
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

    parsed_label, keywords_missing = parse_label_fields(text)
    # The review window shows the photo turned the same way, so the
    # label is upright there too (and label_box matches it)
    parsed_label["photo_turn"] = turn
    if label_blocks is not blocks and label_blocks:
        parsed_label["label_box"] = label_area(label_blocks, image.size)
    warnings = label_warnings(parsed_label, keywords_missing)

    shop_result, shop_warnings = lookup_shop_price(
        session,
        cache,
        cache_lock,
        parsed_label["mk_varenummer"],
        parsed_label["varenavn"],
        parsed_label["part"],
    )
    warnings.extend(shop_warnings)
    if shop_result["varenavn"]:
        # A certain match: use the shop's own name rather than the OCR text
        parsed_label["varenavn"] = shop_result["varenavn"]
    parsed_label["price"] = format_combined_price(shop_result["price"], shop_result["compare_price"])
    if parsed_label["part"]:
        parsed_label["price"] = part_note(parsed_label["part"])
    parsed_label["compare_price"] = shop_result["compare_price"]
    parsed_label["url"] = shop_result["url"]
    return text, parsed_label, warnings, shop_result["candidates"]
