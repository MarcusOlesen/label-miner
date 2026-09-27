"""One photo, end to end: read the label, parse it and look up the price."""

from .ocr import (
    label_area,
    label_blocks_in_reading_order,
    load_label_photo,
    read_text_blocks,
    remove_temporary_files,
    save_temporary_png,
)
from .parsing import label_warnings, parse_label_fields, part_note
from .shop import format_combined_price, lookup_shop_price
from .util import debug_log


def extract_label(converter, image_path, session, cache, cache_lock):
    """Called from up to OCR_WORKERS worker threads at once, all sharing
    one DocumentConverter instance (see process_in_background) - verified
    safe by running it concurrently against a set of real label photos and
    diffing the extracted text against a single-threaded run (identical
    output, no exceptions, real wall-clock speedup - consistent with the
    torch/RapidOCR backend releasing the GIL during inference)."""
    temporary_paths = []
    try:
        image = load_label_photo(image_path)
        blocks = read_text_blocks(converter, save_temporary_png(image, temporary_paths))
        label_blocks, turn = label_blocks_in_reading_order(blocks)
        if turn:
            # A sideways label: read it again upright. Costs one extra OCR,
            # only for these photos (typically 1-2 in 30) - see
            # label_blocks_in_reading_order for why ordering alone isn't enough.
            upright = image.rotate(turn, expand=True)
            upright_blocks = read_text_blocks(converter, save_temporary_png(upright, temporary_paths))
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
            # A confident shop match wins over the raw OCR text - it's the
            # shop's own canonical name, and it's what both the review
            # window and the export end up showing.
            parsed_label["varenavn"] = shop_result["varenavn"]
        parsed_label["price"] = format_combined_price(shop_result["price"], shop_result["compare_price"])
        if parsed_label["part"]:
            parsed_label["price"] = part_note(parsed_label["part"])
        parsed_label["compare_price"] = shop_result["compare_price"]
        parsed_label["url"] = shop_result["url"]
        return text, parsed_label, warnings, shop_result["candidates"]
    finally:
        remove_temporary_files(temporary_paths)
