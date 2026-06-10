"""Measurement preset management.

Provides :class:`Preset` dataclass and :class:`PresetManager` for
saving, loading, and managing named measurement configurations.

Preset files are user data and live OUTSIDE the repository (CMU.17.034 —
preset sequencer, Phase 1).  The default store is a versioned file in a
per-user data directory; the in-repo ``presets/presets.json`` is treated
as a one-time migration source only.  ``_BUILTIN_PRESETS`` stays in code
as seed defaults and is always present in memory.

On-disk format is JSON under a versioned wrapper::

    {"format": "mux16-presets", "version": 1,
     "presets": {<name>: <asdict(Preset)>}}

The loader also accepts the legacy bare ``{<name>: {...}}`` map (detected
by the absence of a ``"format"`` key) so files written before the wrapper
was introduced still load cleanly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# On-disk wrapper identity.
PRESET_FILE_FORMAT = "mux16-presets"
PRESET_FILE_VERSION = 1

# Per-user data directory for the externalized preset store.  Kept out of
# the repo so presets are user data, not code.
_USER_DATA_DIR = os.path.join(
    os.path.expanduser("~"), ".emstat_pico_mux16"
)
_DEFAULT_PRESETS_FILE = os.path.join(_USER_DATA_DIR, "presets.mux16")

# Legacy in-repo store, kept only as a one-time migration source.
_LEGACY_PRESETS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))),
    "presets",
    "presets.json",
)


@dataclass
class Preset:
    """A named measurement configuration.

    Attributes:
        name: Unique identifier / display name.
        technique: Lowercase technique identifier.
        params: Technique-specific parameter dict.
        channels: 1-indexed MUX channel list.
        auto_save: Whether auto-save should be enabled.
        description: Human-readable description.
        electrode_config_mode: Wiring mode (``"external"`` /
            ``"on_board"`` / ``"manual"``).  Defaults to
            ``"external"`` for backward compatibility with presets
            written before batch 2 of WS-electrode-config-modes.
        re_ce_channels: Per-WE RE/CE channel list.  When empty (the
            common case for external / on_board presets),
            :class:`TechniqueConfig.__post_init__` populates the
            default at run time.  Manual-mode presets MUST supply
            a list that matches ``channels`` length and stays within
            CH1-CH14.
    """

    name: str
    technique: str
    params: dict[str, Any] = field(default_factory=dict)
    channels: list[int] = field(default_factory=list)
    auto_save: bool = False
    description: str = ""
    electrode_config_mode: str = "external"
    re_ce_channels: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

# Presets here are injected at load time and protected from deletion via
# the GUI.  Empty by default — shipped presets are migrated from the
# legacy presets.json into the external user store on first run so users
# can manage them with Save/Delete without code changes.
_BUILTIN_PRESETS: dict[str, Preset] = {}


# ---------------------------------------------------------------------------
# (De)serialization helpers
# ---------------------------------------------------------------------------


def _preset_from_dict(obj: dict[str, Any]) -> Preset:
    """Build a :class:`Preset` from a raw dict, tolerating extra keys.

    Filters the dict to the known ``Preset`` fields so older files
    (pre-batch-2) and newer ones carrying unknown extras both load
    cleanly.  Missing new fields fall back to their dataclass defaults.

    Args:
        obj: Raw mapping of preset field names to values.

    Returns:
        A constructed ``Preset``.
    """
    allowed = set(Preset.__dataclass_fields__.keys())
    filtered = {k: v for k, v in obj.items() if k in allowed}
    return Preset(**filtered)


def _presets_from_payload(
    data: dict[str, Any]
) -> dict[str, Preset]:
    """Extract a ``{name: Preset}`` map from a loaded JSON payload.

    Accepts both the versioned wrapper
    (``{"format": ..., "presets": {...}}``) and the legacy bare
    ``{name: {...}}`` map.  Detection is by the presence of a
    ``"format"`` key.

    Args:
        data: Parsed JSON object from a preset file.

    Returns:
        Mapping of preset key to ``Preset`` instance.
    """
    if isinstance(data, dict) and "format" in data:
        raw = data.get("presets", {})
    else:
        # Legacy bare map: {name: {...preset...}}.
        raw = data
    return {key: _preset_from_dict(obj) for key, obj in raw.items()}


def _wrap_presets(
    presets: dict[str, Preset]
) -> dict[str, Any]:
    """Wrap a ``{name: Preset}`` map in the versioned on-disk format.

    Args:
        presets: In-memory preset map.

    Returns:
        A JSON-serializable wrapper dict.
    """
    return {
        "format": PRESET_FILE_FORMAT,
        "version": PRESET_FILE_VERSION,
        "presets": {k: asdict(v) for k, v in presets.items()},
    }


# ---------------------------------------------------------------------------
# PresetManager
# ---------------------------------------------------------------------------


class PresetManager:
    """Load, save, and manage measurement presets from a JSON file.

    Built-in presets are always available in memory and cannot be
    deleted (but their parameters can be overridden by user presets
    with the same key).  The active store is an external, versioned
    file under a per-user data directory.

    Args:
        path: Path to the presets file.  When ``None`` the per-user
            default (``~/.emstat_pico_mux16/presets.mux16``) is used,
            with a one-time import from the legacy in-repo
            ``presets/presets.json`` if the external file does not yet
            exist.
    """

    def __init__(
        self, path: Optional[str] = None
    ) -> None:
        if path is None:
            self._path = _DEFAULT_PRESETS_FILE
            self._use_default_store = True
        else:
            self._path = path
            self._use_default_store = False
        self._presets: dict[str, Preset] = {}
        self._load()

    # -- internal -----------------------------------------------------

    def _seed_builtins(self) -> None:
        """Reset the in-memory map to a fresh copy of the built-ins."""
        self._presets = {
            k: Preset(**asdict(v))
            for k, v in _BUILTIN_PRESETS.items()
        }

    def _load(self) -> None:
        """Load presets from disk, merging on top of the built-ins.

        For the default store, a missing external file triggers a
        one-time migration from the legacy in-repo ``presets.json``
        (when present) before the external file is created.
        """
        self._seed_builtins()

        if os.path.isfile(self._path):
            self._read_into(self._path)
            return

        # External store does not exist yet.
        if self._use_default_store and os.path.isfile(
            _LEGACY_PRESETS_FILE
        ):
            # One-time migration of the shipped in-repo presets.
            self._read_into(_LEGACY_PRESETS_FILE)
            logger.info(
                "Migrated legacy presets from %s to %s",
                _LEGACY_PRESETS_FILE,
                self._path,
            )

        # Materialize the (possibly migrated, possibly built-in-only)
        # store at the active path so subsequent runs are stable.
        self._save()

    def _read_into(self, path: str) -> None:
        """Parse ``path`` and merge its presets over the built-ins.

        Args:
            path: Existing preset file to read.
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = _presets_from_payload(data)
            self._presets.update(loaded)
            logger.info(
                "Loaded %d presets from %s", len(loaded), path
            )
        except (
            OSError,
            json.JSONDecodeError,
            TypeError,
            KeyError,
            AttributeError,
        ) as e:
            logger.warning(
                "Failed to load presets from %s: %s", path, e
            )

    def _save(self) -> None:
        """Write all presets to the active path in wrapper format."""
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(_wrap_presets(self._presets), f, indent=2)
        logger.info(
            "Saved %d presets to %s",
            len(self._presets),
            self._path,
        )

    # -- explicit path I/O (CMU.17.034) -------------------------------

    def save_to_path(self, path: str) -> None:
        """Write the current presets to an arbitrary file.

        The file is written in the versioned wrapper format.  The
        manager's active path is unchanged; use this to export to a
        new ``*.mux16`` location.

        Args:
            path: Destination file path.
        """
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_wrap_presets(self._presets), f, indent=2)
        logger.info(
            "Saved %d presets to %s", len(self._presets), path
        )

    def load_from_path(self, path: str) -> None:
        """Replace the in-memory presets with the contents of ``path``.

        Built-ins are re-seeded first, then the file's presets are
        merged on top (so user entries override same-named built-ins).
        The manager's active path is switched to ``path`` so subsequent
        ``add_preset`` / ``delete_preset`` calls persist there.

        Args:
            path: Source preset file (wrapper or legacy bare map).
        """
        self._seed_builtins()
        self._read_into(path)
        self._path = path
        self._use_default_store = False

    # -- queries ------------------------------------------------------

    @property
    def path(self) -> str:
        """Return the active preset file path."""
        return self._path

    def list_presets(self) -> list[str]:
        """Return sorted list of preset keys."""
        return sorted(self._presets.keys())

    def get_preset(self, key: str) -> Optional[Preset]:
        """Return a preset by key, or None if not found."""
        return self._presets.get(key)

    def get_all(self) -> dict[str, Preset]:
        """Return a copy of all presets."""
        return dict(self._presets)

    def add_preset(self, key: str, preset: Preset) -> None:
        """Add or update a preset and save to disk.

        Args:
            key: Unique key for the preset.
            preset: The preset configuration.
        """
        self._presets[key] = preset
        self._save()

    def delete_preset(self, key: str) -> bool:
        """Delete a user preset.

        Built-in presets cannot be deleted.

        Args:
            key: Preset key to delete.

        Returns:
            True if deleted, False if not found or built-in.
        """
        if key in _BUILTIN_PRESETS:
            logger.warning(
                "Cannot delete built-in preset: %s", key
            )
            return False
        if key in self._presets:
            del self._presets[key]
            self._save()
            return True
        return False

    def is_builtin(self, key: str) -> bool:
        """Return True if ``key`` refers to an undeletable built-in preset."""
        return key in _BUILTIN_PRESETS
