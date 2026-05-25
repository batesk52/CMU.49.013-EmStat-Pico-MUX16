"""Tests for PresetManager.delete_preset and is_builtin.

Covers the data-layer contract that the GUI's "Delete..." button
relies on: built-in presets cannot be removed, user presets round-trip
through add -> delete cleanly, and is_builtin reports correctly so the
GUI can disable Delete on undeletable selections without trial-and-error.
"""

from __future__ import annotations

from src.data.presets import Preset, PresetManager


def test_is_builtin_true_for_no_sensing(tmp_path) -> None:
    """The shipped built-in 'no_sensing' is reported as built-in."""
    mgr = PresetManager(path=str(tmp_path / "presets.json"))
    assert mgr.is_builtin("no_sensing") is True


def test_is_builtin_false_for_user_key(tmp_path) -> None:
    """An arbitrary user key is not built-in."""
    mgr = PresetManager(path=str(tmp_path / "presets.json"))
    assert mgr.is_builtin("user_made_this_up") is False


def test_is_builtin_false_for_empty_key(tmp_path) -> None:
    """The '(No Preset)' sentinel ('') is not built-in."""
    mgr = PresetManager(path=str(tmp_path / "presets.json"))
    assert mgr.is_builtin("") is False


def test_add_then_delete_user_preset_round_trip(tmp_path) -> None:
    """add_preset followed by delete_preset removes the entry."""
    mgr = PresetManager(path=str(tmp_path / "presets.json"))
    mgr.add_preset(
        "my_preset", Preset(name="My Preset", technique="cv")
    )
    assert mgr.get_preset("my_preset") is not None

    assert mgr.delete_preset("my_preset") is True
    assert mgr.get_preset("my_preset") is None
    # Second delete is a no-op that signals failure rather than raises.
    assert mgr.delete_preset("my_preset") is False


def test_delete_persists_across_manager_reload(tmp_path) -> None:
    """A delete actually writes to disk, not just the in-memory dict."""
    path = tmp_path / "presets.json"
    mgr = PresetManager(path=str(path))
    mgr.add_preset(
        "tmp", Preset(name="Temp", technique="cv")
    )
    assert mgr.delete_preset("tmp") is True

    reloaded = PresetManager(path=str(path))
    assert reloaded.get_preset("tmp") is None


def test_delete_builtin_refused_and_preset_survives(tmp_path) -> None:
    """delete_preset returns False on built-ins and leaves them intact."""
    mgr = PresetManager(path=str(tmp_path / "presets.json"))
    assert mgr.get_preset("no_sensing") is not None

    assert mgr.delete_preset("no_sensing") is False
    assert mgr.get_preset("no_sensing") is not None
