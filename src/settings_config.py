"""Persist and retrieve LensLedger application settings."""

from __future__ import annotations

import json
from pathlib import Path

from app_paths import data_root


SETTINGS_PATH = data_root() / "settings.json"

DEFAULTS: dict[str, object] = {
    "scan": {
        "ocr_workers": 4,
        "ocr_batch_size": 50,
        "semantic_batch_size": 16,
        "semantic_model": "ViT-B-32/openai",
    },
    "display": {
        "photos_per_page": 250,
        "default_sort": "newest",
        "filmstrip_size": "medium",
    },
    "watch": {
        "enabled": True,
        "interval_minutes": 5,
    },
    "ingest": {
        "enabled": False,
        "source_folder": "",
        "destination_folder": "",
        "rules": [],
    },
    "startup": {
        "show_library_picker": False,
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


def load_settings() -> dict:
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return _deep_merge(DEFAULTS, raw)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return dict(DEFAULTS)


def save_settings(values: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    merged = _deep_merge(DEFAULTS, values)
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_PATH)


def get_setting(*keys: str, default: object = None) -> object:
    settings = load_settings()
    current: object = settings
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current
