"""Preset sequence model and persistence (CMU.17.034 — Phase 2).

A sequence stacks saved presets as ordered steps and runs them
back-to-back on the MUX-16 (a PSTrace-"Scripts" equivalent).  Steps
reference presets by name and may carry per-step overrides; the
sequence file is portable on its own and is stored SEPARATELY from the
preset store in a ``*.mux16seq`` file.

On-disk format is JSON under a versioned wrapper::

    {"format": "mux16-sequence", "version": 1,
     "name": <str>, "steps": [<asdict(SequenceStep)>, ...]}

``build_config`` resolves a step against its preset (applying overrides)
and constructs a :class:`TechniqueConfig`, letting
``TechniqueConfig.__post_init__`` validate it (Mode-C bounds, RE/CE
length, etc.).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from src.data.models import TechniqueConfig
from src.data.presets import Preset

# On-disk wrapper identity.
SEQUENCE_FILE_FORMAT = "mux16-sequence"
SEQUENCE_FILE_VERSION = 1


@dataclass
class SequenceStep:
    """One step in a preset sequence.

    Attributes:
        preset_name: Key of the preset this step runs.  Resolved
            against a :class:`~src.data.presets.PresetManager` at build
            time.
        repeat: Number of times the step is run back-to-back (>= 1).
        delay_s: Idle delay in seconds inserted AFTER the step (and its
            repeats) before the next step starts.
        channels_override: Optional WE channel list that replaces the
            preset's ``channels`` for this step.  ``None`` keeps the
            preset's channels.
        mode_override: Optional electrode-config mode
            (``"external"`` / ``"on_board"`` / ``"manual"``) that
            replaces the preset's mode for this step.  ``None`` keeps
            the preset's mode.
    """

    preset_name: str
    repeat: int = 1
    delay_s: float = 0.0
    channels_override: Optional[list[int]] = None
    mode_override: Optional[str] = None


@dataclass
class Sequence:
    """An ordered, named list of preset steps.

    Attributes:
        name: Display name for the sequence.
        steps: Ordered list of :class:`SequenceStep`.
    """

    name: str
    steps: list[SequenceStep] = field(default_factory=list)

    # -- (de)serialization -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned wrapper dict for this sequence.

        Returns:
            A JSON-serializable mapping in the on-disk wrapper format.
        """
        return {
            "format": SEQUENCE_FILE_FORMAT,
            "version": SEQUENCE_FILE_VERSION,
            "name": self.name,
            "steps": [asdict(step) for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sequence":
        """Build a :class:`Sequence` from a parsed wrapper dict.

        Tolerates unknown extra keys on each step so the format can
        evolve.  Missing step fields fall back to their dataclass
        defaults.

        Args:
            data: Parsed JSON object (wrapper format).

        Returns:
            The reconstructed ``Sequence``.
        """
        allowed = set(SequenceStep.__dataclass_fields__.keys())
        steps = [
            SequenceStep(
                **{k: v for k, v in raw.items() if k in allowed}
            )
            for raw in data.get("steps", [])
        ]
        return cls(name=data.get("name", ""), steps=steps)

    def save_to_path(self, path: str) -> None:
        """Write the sequence to a ``*.mux16seq`` file.

        Args:
            path: Destination file path.
        """
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load_from_path(cls, path: str) -> "Sequence":
        """Read a sequence from a ``*.mux16seq`` file.

        Args:
            path: Source file path.

        Returns:
            The loaded ``Sequence``.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


def build_config(
    step: SequenceStep, preset: Preset
) -> TechniqueConfig:
    """Resolve a step against its preset into a validated config.

    The preset supplies the technique, params, channels, electrode
    mode, and RE/CE pairing.  Step overrides (``channels_override``,
    ``mode_override``) take precedence when present.  The resulting
    :class:`TechniqueConfig` is validated by its ``__post_init__`` — a
    Mode-C step with empty ``re_ce_channels`` therefore raises
    ``ValueError``.

    Args:
        step: The sequence step to resolve.
        preset: The preset referenced by ``step.preset_name``.

    Returns:
        A constructed, validated ``TechniqueConfig``.

    Raises:
        ValueError: If the resolved configuration is invalid (e.g.
            Mode-C with no ``re_ce_channels``, channel out of range,
            or mismatched RE/CE length).
    """
    channels = (
        list(step.channels_override)
        if step.channels_override is not None
        else list(preset.channels)
    )
    mode = (
        step.mode_override
        if step.mode_override is not None
        else preset.electrode_config_mode
    )
    # Carry the preset's explicit RE/CE pairing only when it still
    # matches the resolved channel count.  A channels override (or a
    # length mismatch from a hand-edited preset) drops it so external /
    # on_board steps repopulate from the mode default in
    # ``__post_init__``; a manual step with no usable pairing then
    # raises there, which is the intended safety behaviour.
    re_ce_channels = list(preset.re_ce_channels)
    if len(re_ce_channels) != len(channels):
        re_ce_channels = []
    return TechniqueConfig(
        technique=preset.technique,
        params=dict(preset.params),
        channels=channels,
        re_ce_channels=re_ce_channels,
        electrode_config_mode=mode,
    )
