"""Persist and retrieve LensLedger application settings."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from app_paths import data_root, write_json_atomically


def settings_file() -> Path:
    """Resolve the settings file every time it is needed.

    Binding this once at import time meant that whatever LENSLEDGER_DATA_DIR
    said when the module first loaded won for the life of the process -- so a
    test that set it later still read and wrote the real one.
    """
    return data_root() / "settings.json"

DEFAULTS: dict[str, object] = {
    "scan": {
        "semantic_batch_size": 16,
        "semantic_model": "ViT-B-32/openai",
    },
    "watch": {
        "enabled": True,
        "interval_minutes": 5,
    },
    "ingest": {
        "enabled": False,
        "source_folder": "",
        "destination_folder": "",
        "interval_minutes": 10,
        "rules": [],
    },
    "startup": {
        "show_library_picker": False,
        "terminal_tab": True,
    },
    "publish": {
        "write_mode": "embedded",
        "auto_classify": False,
        "classify_threshold": 0.22,
        "classify_top_n": 5,
        "backup_keep_days": 30,
        "backup_max_gb": 20,
    },
}

AVAILABLE_MODELS: list[dict[str, str]] = [
    {"id": "ViT-B-32/openai", "name": "ViT-B-32 (OpenAI)", "description": "Fast, small (~400 MB). Good general quality.", "size": "~400 MB"},
    {"id": "ViT-B-16/openai", "name": "ViT-B-16 (OpenAI)", "description": "Better quality than B-32, slower. ~600 MB download.", "size": "~600 MB"},
    {"id": "ViT-L-14/openai", "name": "ViT-L-14 (OpenAI)", "description": "High quality, significantly slower and larger. ~1.8 GB download.", "size": "~1.8 GB"},
]


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# What each setting may hold: (kind, lowest, highest, name shown in an error).
# Anything not listed here is kept as it is.
_RULES: dict[tuple[str, str], tuple[str, float | None, float | None, str]] = {
    ("scan", "semantic_batch_size"): ("int", 1, 128, "Meaning search batch"),
    ("scan", "semantic_model"): ("text", None, None, "Meaning search model"),
    ("watch", "enabled"): ("bool", None, None, "Folder watching"),
    ("watch", "interval_minutes"): ("int", 5, 1440, "Folder watching check interval"),
    ("ingest", "enabled"): ("bool", None, None, "Auto-import"),
    ("ingest", "interval_minutes"): ("int", 5, 1440, "Auto-import check interval"),
    ("ingest", "source_folder"): ("text", None, None, "Auto-import source folder"),
    ("ingest", "destination_folder"): ("text", None, None, "Auto-import destination folder"),
    ("ingest", "default_template"): ("text", None, None, "Auto-import date template"),
    ("ingest", "rules"): ("list", None, None, "Auto-import override rules"),
    ("startup", "show_library_picker"): ("bool", None, None, "Ask which library to open at startup"),
    ("startup", "terminal_tab"): ("bool", None, None, "Terminal tab"),
    ("publish", "embed"): ("bool", None, None, "Embed tags in photos"),
    ("publish", "write_mode"): ("text", None, None, "Write mode"),
    ("publish", "sidecar_types"): ("dict", None, None, "Sidecar file types"),
    ("publish", "auto_classify"): ("bool", None, None, "Automatic classification"),
    ("publish", "classify_threshold"): ("float", 0, 1, "Classification threshold"),
    ("publish", "classify_top_n"): ("int", 1, 100, "Classification tags per photo"),
    ("publish", "backup_keep_days"): ("int", 0, 36500, "Days to keep photo safety copies"),
    ("publish", "backup_max_gb"): ("float", 0, 100000, "Total size limit for safety copies"),
}
_TEXT_LIMIT = 4096


def _number(value: object, integer: bool) -> float:
    if isinstance(value, bool):
        raise ValueError("not a number")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        number = float(value.strip())
    else:
        raise ValueError("not a number")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError("not a number")
    if integer and number != int(number):
        raise ValueError("not a whole number")
    return number


def _coerce(rule: tuple, value: object) -> object:
    kind, low, high, label = rule
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{label} must be on or off")
    if kind == "text":
        if isinstance(value, str) and len(value) <= _TEXT_LIMIT:
            return value
        raise ValueError(f"{label} must be text of at most {_TEXT_LIMIT} characters")
    if kind == "list":
        if isinstance(value, list):
            return value
        raise ValueError(f"{label} must be a list")
    if kind == "dict":
        if isinstance(value, dict):
            return value
        raise ValueError(f"{label} must be a set of options")
    integer = kind == "int"
    allowed = f"{'a whole number' if integer else 'a number'} from {low:g} to {high:g}"
    try:
        number = _number(value, integer)
    except ValueError:
        raise ValueError(f"{label} must be {allowed}") from None
    if not low <= number <= high:
        raise ValueError(f"{label} must be {allowed}")
    return int(number) if integer else number


def validate_settings(values: object) -> dict:
    """The settings as a clean copy, or a ValueError naming the first bad one.

    Known settings are checked for type and range; anything else is kept, so
    a newer or older version's extra keys survive a save.
    """
    if not isinstance(values, dict):
        raise ValueError("invalid settings format")
    cleaned = copy.deepcopy(values)
    sections = {section for section, _key in _RULES}
    for section in sections:
        if section in cleaned and not isinstance(cleaned[section], dict):
            raise ValueError(f"The {section} settings are not in the expected form")
    for (section, key), rule in _RULES.items():
        if key in cleaned.get(section, {}):
            cleaned[section][key] = _coerce(rule, cleaned[section][key])
    return cleaned


def _repair(settings: dict) -> dict:
    """Replace stored values that no longer fit with the default, so a
    hand-edited or damaged file cannot stop a page or the server starting."""
    for section in {section for section, _key in _RULES}:
        if section in settings and not isinstance(settings[section], dict):
            settings[section] = copy.deepcopy(DEFAULTS.get(section, {}))
    for (section, key), rule in _RULES.items():
        stored = settings.get(section, {})
        if key not in stored:
            continue
        try:
            stored[key] = _coerce(rule, stored[key])
        except ValueError:
            default = DEFAULTS.get(section, {}).get(key)
            if default is None:
                del stored[key]
            else:
                stored[key] = copy.deepcopy(default)
    return settings


def load_settings() -> dict:
    # A copy: the nested sections are otherwise DEFAULTS' own dicts, and a
    # caller that edits what it loaded would change the defaults for the
    # rest of the process.
    try:
        raw = json.loads(settings_file().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return _repair(copy.deepcopy(_deep_merge(DEFAULTS, raw)))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return copy.deepcopy(DEFAULTS)


def save_settings(values: dict) -> None:
    """Check the values, fold them into what is stored, and write the file.
    Nothing is written when a value is not acceptable."""
    cleaned = validate_settings(values)
    write_json_atomically(settings_file(), _deep_merge(load_settings(), cleaned))


def get_setting(*keys: str, default: object = None) -> object:
    settings = load_settings()
    current: object = settings
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current
