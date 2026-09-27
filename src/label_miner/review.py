"""What the review window asks about a label that wasn't approved automatically."""

from .config import COLOR_ERROR, COLOR_WARNING
from .parsing import MISSING_FIELD_NAMES


def warning_messages(warnings):
    """Flatten (category, message) warning tuples down to just the messages."""
    return [message for _, message in warnings]


def review_question(warnings, part=""):
    """The one thing the user is asked to do for a label in review, in plain
    Danish: {"title", "detail", "fields", "others", "color"}. Picked by
    priority from the record's warnings - "fields" are the entry fields to
    highlight, "others" the remaining warning messages, shown smaller.
    Returns None when there is nothing to review."""
    if not warnings:
        return None
    by_category = {}
    for category, message in warnings:
        by_category.setdefault(category, []).append(message)

    def others(*handled):
        return [message for category, message in warnings if category not in handled]

    if "error" in by_category:
        return {
            "title": "Billedet kunne ikke læses",
            "detail": by_category["error"][0]
            + '\nTryk "Læs billedet igen", eller skriv felterne ud fra billedet.',
            "fields": list(MISSING_FIELD_NAMES),
            "others": others("error"),
            "color": COLOR_ERROR,
        }
    if "conflict" in by_category:
        return {
            "title": "Nummer og navn passer ikke sammen",
            "detail": by_category["conflict"][0],
            "fields": ["mk_varenummer", "varenavn"],
            "others": others("conflict"),
            "color": COLOR_WARNING,
        }
    if "missing" in by_category:
        missing = [
            field
            for field, name in MISSING_FIELD_NAMES.items()
            if any(name in message for message in by_category["missing"])
        ]
        if missing == ["stand"]:
            title, detail = "Skriv STAND (A, B eller C)", "Standen kunne ikke læses på labelen."
        else:
            title = "Nogle felter kunne ikke læses"
            detail = (
                "Skriv dem ud fra billedet: "
                + ", ".join(MISSING_FIELD_NAMES[field] for field in missing)
                + "."
            )
        return {
            "title": title,
            "detail": detail,
            "fields": missing,
            "others": others("missing", "partial"),
            "color": COLOR_WARNING,
        }
    if "suspicious" in by_category:
        message = by_category["suspicious"][0]
        field = "stand" if "STAND" in message else "mk_ordrenr"
        return {
            "title": "Tjek STAND" if field == "stand" else "Tjek MK-ordrenummeret",
            "detail": message.rstrip(".") + (". Ret den til A, B eller C." if field == "stand" else "."),
            "fields": [field],
            "others": others("suspicious"),
            "color": COLOR_WARNING,
        }
    if "part" in by_category:
        return {
            "title": f"Delvis vare (del {part})" if part else "Delvis vare",
            "detail": "Kun en del af varen – der sættes ingen pris. Tjek nummer og navn.",
            "fields": ["mk_varenummer", "varenavn"],
            "others": [
                message for category, message in warnings if category not in ("part", "variant", "by_name")
            ],
            "color": COLOR_WARNING,
        }
    if "by_name" in by_category:
        return {
            "title": "Er det den rigtige vare?",
            "detail": by_category["by_name"][0],
            "fields": [],
            "others": others("by_name"),
            "color": COLOR_WARNING,
        }
    if "variant" in by_category:
        return {
            "title": "Vælg den rigtige vare",
            "detail": "Navn eller farve passer ikke præcist med hjemmesiden. "
            "Vælg den vare der passer til labelen.",
            "fields": [],
            "others": others("variant"),
            "color": COLOR_WARNING,
        }
    if "not_found" in by_category:
        return {
            "title": "Varen findes ikke på hjemmesiden",
            "detail": "Tjek nummer og navn på billedet. Prisen skal skrives manuelt.",
            "fields": ["price"],
            "others": others("not_found"),
            "color": COLOR_WARNING,
        }
    category, message = warnings[0]
    return {
        "title": "Tjek labelen",
        "detail": message,
        "fields": ["price"] if category == "shop" else [],
        "others": [other for _, other in warnings[1:]],
        "color": COLOR_WARNING,
    }
