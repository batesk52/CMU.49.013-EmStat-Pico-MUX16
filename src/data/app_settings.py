"""Lightweight persistent application settings (CMU.17.034 — Phase 1).

Remembers cross-launch pointers — currently the last-used preset file —
so the GUI can auto-load it on startup.

**Choice of backend:** a tiny JSON file in the per-user data directory
(``~/.emstat_pico_mux16/app_settings.json``) rather than ``QSettings``.
The JSON store imports and round-trips with NO running ``QApplication``,
so the get/set helpers and their tests are fully headless; it also lives
beside the externalized preset store, keeping all user data in one place.
The store path is overridable (``path=``) so tests can point at a temp
file and never touch the real user store.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Per-user data directory (shared with the preset store).
_USER_DATA_DIR = os.path.join(
    os.path.expanduser("~"), ".emstat_pico_mux16"
)
_DEFAULT_SETTINGS_FILE = os.path.join(
    _USER_DATA_DIR, "app_settings.json"
)

_LAST_PRESET_FILE_KEY = "last_preset_file"


def _resolve_path(path: Optional[str]) -> str:
    """Return the settings-file path, defaulting to the user store.

    Args:
        path: Explicit path override, or ``None`` for the default.

    Returns:
        The resolved settings file path.
    """
    return path if path is not None else _DEFAULT_SETTINGS_FILE


def _read(path: Optional[str] = None) -> dict[str, object]:
    """Read the settings dict, tolerating a missing/corrupt file.

    Args:
        path: Optional settings-file path override.

    Returns:
        The parsed settings mapping, or an empty dict on any failure.
    """
    resolved = _resolve_path(path)
    if not os.path.isfile(resolved):
        return {}
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning(
            "Settings file %s is not an object; ignoring", resolved
        )
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "Failed to read settings from %s: %s", resolved, e
        )
        return {}


def _write(data: dict[str, object], path: Optional[str] = None) -> None:
    """Persist the settings dict, creating the directory as needed.

    Args:
        data: Settings mapping to write.
        path: Optional settings-file path override.
    """
    resolved = _resolve_path(path)
    directory = os.path.dirname(resolved)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(resolved, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_last_preset_file(
    path: Optional[str] = None,
) -> Optional[str]:
    """Return the remembered last-used preset file path.

    Args:
        path: Optional settings-file path override (for tests).

    Returns:
        The stored preset-file path, or ``None`` if unset.
    """
    value = _read(path).get(_LAST_PRESET_FILE_KEY)
    return value if isinstance(value, str) and value else None


def set_last_preset_file(
    preset_file: Optional[str], path: Optional[str] = None
) -> None:
    """Store (or clear) the last-used preset file path.

    Args:
        preset_file: Preset-file path to remember, or ``None`` to clear.
        path: Optional settings-file path override (for tests).
    """
    data = _read(path)
    if preset_file:
        data[_LAST_PRESET_FILE_KEY] = preset_file
    else:
        data.pop(_LAST_PRESET_FILE_KEY, None)
    _write(data, path)
