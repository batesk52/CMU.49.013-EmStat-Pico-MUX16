"""Tests for the preset sequence model (CMU.17.034 — Phase 2).

Covers:
  * a 3-step sequence round-trips through ``*.mux16seq`` save/load with
    equality preserved (including per-step overrides), and
  * ``build_config`` lets ``TechniqueConfig.__post_init__`` reject a
    Mode-C step that carries no ``re_ce_channels``.
"""

from __future__ import annotations

import pytest

from src.data.presets import Preset
from src.data.sequence import (
    Sequence,
    SequenceStep,
    build_config,
)


def test_three_step_sequence_round_trip(tmp_path) -> None:
    """A 3-step sequence survives save -> load unchanged."""
    seq = Sequence(
        name="my_sequence",
        steps=[
            SequenceStep(preset_name="cv_one"),
            SequenceStep(
                preset_name="ca_two",
                repeat=3,
                delay_s=2.5,
            ),
            SequenceStep(
                preset_name="manual_three",
                channels_override=[1, 2],
                mode_override="manual",
            ),
        ],
    )

    path = tmp_path / "seq.mux16seq"
    seq.save_to_path(str(path))
    loaded = Sequence.load_from_path(str(path))

    assert loaded == seq
    assert loaded.name == "my_sequence"
    assert len(loaded.steps) == 3
    assert loaded.steps[1].repeat == 3
    assert loaded.steps[1].delay_s == 2.5
    assert loaded.steps[2].channels_override == [1, 2]
    assert loaded.steps[2].mode_override == "manual"


def test_build_config_resolves_preset(tmp_path) -> None:
    """build_config carries preset fields into the TechniqueConfig."""
    preset = Preset(
        name="CV One",
        technique="cv",
        params={"e_step": 0.01},
        channels=[1, 2, 3],
        electrode_config_mode="external",
    )
    step = SequenceStep(preset_name="cv_one")

    cfg = build_config(step, preset)

    assert cfg.technique == "cv"
    assert cfg.channels == [1, 2, 3]
    assert cfg.electrode_config_mode == "external"
    # __post_init__ fills external RE/CE from the mode default.
    assert cfg.re_ce_channels == [15, 15, 15]


def test_build_config_applies_overrides() -> None:
    """Step overrides take precedence over preset values."""
    preset = Preset(
        name="CV One",
        technique="cv",
        params={},
        channels=[1, 2, 3, 4],
        re_ce_channels=[1, 2, 3, 4],
        electrode_config_mode="manual",
    )
    step = SequenceStep(
        preset_name="cv_one",
        channels_override=[5, 6],
        mode_override="external",
    )

    cfg = build_config(step, preset)

    assert cfg.channels == [5, 6]
    assert cfg.electrode_config_mode == "external"


def test_build_config_raises_on_mode_c_empty_re_ce() -> None:
    """A Mode-C step with empty re_ce_channels is rejected."""
    preset = Preset(
        name="Manual Bad",
        technique="cv",
        params={},
        channels=[1, 2],
        electrode_config_mode="manual",
        re_ce_channels=[],
    )
    step = SequenceStep(preset_name="manual_three")

    with pytest.raises(ValueError):
        build_config(step, preset)
