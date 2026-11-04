"""The user's settings, kept between runs in a small JSON file.

Windows: %APPDATA%\\Label Miner\\settings.json (it survives updates and
reinstalls, and isn't cleaned away like %TEMP%). Elsewhere:
~/.config/label-miner/settings.json. A missing or damaged file, or one
that can't be written, just means the defaults - the app never fails over
its settings.
"""

import json
import os
from pathlib import Path

from .util import debug_log

DEFAULTS = {
    # STK = the label's Antal instead of one per photo (see batch.pieces)
    "stk_from_antal": False,
}


def settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Label Miner" / "settings.json"
    return Path.home() / ".config" / "label-miner" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    """The saved settings over the defaults; unknown keys and values of the
    wrong type are ignored."""
    settings = dict(DEFAULTS)
    path = path or settings_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as error:
        debug_log(f"Could not read settings {path}: {error}")
        return settings
    if isinstance(saved, dict):
        for key, default in DEFAULTS.items():
            if isinstance(saved.get(key), type(default)):
                settings[key] = saved[key]
    return settings


def save_settings(settings: dict, path: Path | None = None) -> None:
    path = path or settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, indent=1, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)  # never leaves a half-written file
    except OSError as error:
        debug_log(f"Could not save settings {path}: {error}")
