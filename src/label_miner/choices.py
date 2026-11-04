"""Remembered choices: which webshop product the user picked for a label.

Some labels can never be matched automatically: a supplier code the shop
doesn't know, a German label, a new label without its colour. When the user
approves such a label after clicking one of the webshop's suggestions, the
choice is saved, and the next label with the same number and name gets that
product as its first suggestion, already selected - one click instead of a
search. It is only a suggestion: the label still goes to review and the
user still approves it, so a remembered choice never approves anything on
its own.

A choice is kept per label number (as printed, supplier prefix included) and
name, so the same supplier code on a different product isn't mixed up.
Picking another product for the same label replaces the old choice, so a
wrong click doesn't keep coming back.

Saved next to the settings: %APPDATA%\\Label Miner\\choices.json (see
settings.py). Like the settings, a missing or damaged file just means no
remembered choices.
"""

import json
from datetime import date
from pathlib import Path

from .parsing import WHITESPACE_RE
from .settings import settings_path
from .shop import name_tokens
from .util import debug_log

Choices = dict  # key -> {variant_id, url, name, count, last}


def choices_path() -> Path:
    return settings_path().with_name("choices.json")


def choice_key(mk_varenummer: str, varenavn: str) -> str | None:
    """The label as it is read: number and the words of its name. None
    without both - a number or a name alone says too little."""
    number = WHITESPACE_RE.sub(" ", mk_varenummer or "").strip().upper()
    words = " ".join(name_tokens(varenavn))
    if not number or not words:
        return None
    return f"{number}|{words}"


def load_choices(path: Path | None = None) -> Choices:
    path = path or choices_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        debug_log(f"Could not read remembered choices {path}: {error}")
        return {}
    if not isinstance(saved, dict) or not isinstance(saved.get("choices"), dict):
        return {}
    return {
        key: choice
        for key, choice in saved["choices"].items()
        if isinstance(choice, dict) and isinstance(choice.get("url"), str) and choice.get("variant_id")
    }


def save_choices(choices: Choices, path: Path | None = None) -> None:
    path = path or choices_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        content = {"version": 1, "choices": choices}
        temporary.write_text(json.dumps(content, indent=1, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        debug_log(f"Could not save remembered choices {path}: {error}")


def remember_choice(choices: Choices, mk_varenummer: str, varenavn: str, candidate: dict) -> bool:
    """Record that the user picked `candidate` for this label. The same
    product again counts up; another product replaces it. Returns whether
    anything was recorded."""
    key = choice_key(mk_varenummer, varenavn)
    if key is None or not candidate.get("id") or not candidate.get("url"):
        return False
    old = choices.get(key)
    count = old["count"] + 1 if old and old.get("variant_id") == candidate["id"] else 1
    choices[key] = {
        "variant_id": candidate["id"],
        "url": candidate["url"],
        "name": candidate["name"],
        "count": count,
        "last": date.today().isoformat(),
    }
    return True


def remembered_choice(choices: Choices | None, mk_varenummer: str, varenavn: str) -> dict | None:
    if not choices:
        return None
    key = choice_key(mk_varenummer, varenavn)
    return choices.get(key) if key else None
