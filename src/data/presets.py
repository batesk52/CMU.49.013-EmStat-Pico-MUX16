"""Measurement preset management.

Provides :class:`Preset` dataclass and :class:`PresetManager` for
saving, loading, and managing named measurement configurations.
Ships with a built-in NO Sensing preset matching the DARPA IV&V SOP.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default preset storage location relative to project root.
_DEFAULT_PRESETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    ))),
    "presets",
)
_DEFAULT_PRESETS_FILE = os.path.join(
    _DEFAULT_PRESETS_DIR, "presets.json"
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
    """

    name: str
    technique: str
    params: dict[str, Any] = field(default_factory=dict)
    channels: list[int] = field(default_factory=list)
    auto_save: bool = False
    description: str = ""


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

_BUILTIN_PRESETS: dict[str, Preset] = {
    "no_sensing": Preset(
        name="NO Sensing (DARPA IV&V)",
        technique="ca",
        params={
            "e_dc": 0.85,
            "t_run": 10.0,
            "t_interval": 0.1,
        },
        channels=list(range(1, 9)),
        auto_save=True,
        description=(
            "NO biosensor: CA at 0.85V, channels 1-8, "
            "auto-save enabled"
        ),
    ),
}


# ---------------------------------------------------------------------------
# PresetManager
# ---------------------------------------------------------------------------


class PresetManager:
    """Load, save, and manage measurement presets from JSON.

    Presets are stored in a JSON file.  Built-in presets are always
    available and cannot be deleted (but their parameters can be
    overridden by user presets with the same key).

    Args:
        path: Path to the presets JSON file.  Defaults to
            ``presets/presets.json`` relative to the project root.
    """

    def __init__(
        self, path: Optional[str] = None
    ) -> None:
        self._path = path or _DEFAULT_PRESETS_FILE
        self._presets: dict[str, Preset] = {}
        self._load()

    def _load(self) -> None:
        """Load presets from disk, merging with built-ins."""
        # Start with built-in presets
        self._presets = {
            k: Preset(**asdict(v))
            for k, v in _BUILTIN_PRESETS.items()
        }

        if os.path.isfile(self._path):
            try:
                with open(
                    self._path, "r", encoding="utf-8"
                ) as f:
                    data = json.load(f)
                for key, obj in data.items():
                    self._presets[key] = Preset(**obj)
                logger.info(
                    "Loaded %d presets from %s",
                    len(data),
                    self._path,
                )
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(
                    "Failed to load presets from %s: %s",
                    self._path,
                    e,
                )
        else:
            # Create file with built-in presets
            self._save()

    def _save(self) -> None:
        """Write all presets to disk."""
        os.makedirs(
            os.path.dirname(self._path), exist_ok=True
        )
        data = {k: asdict(v) for k, v in self._presets.items()}
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved %d presets to %s", len(data), self._path)

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
