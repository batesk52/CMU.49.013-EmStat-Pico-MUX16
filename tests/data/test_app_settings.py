"""Tests for persistent app settings (CMU.17.034 — Phase 1).

Covers the ``last_preset_file`` pointer used to auto-load the user's
chosen preset store on startup:
  * set-then-get round-trips through a temp settings file, and
  * the remembered file actually re-loads its presets via
    ``PresetManager.load_from_path`` (the auto-load contract).

A temp settings path is passed everywhere so the real per-user store is
never touched.
"""

from __future__ import annotations

from src.data.app_settings import (
    get_last_preset_file,
    set_last_preset_file,
)
from src.data.presets import Preset, PresetManager


def test_set_then_get_round_trip(tmp_path) -> None:
    """A stored pointer reads back identically."""
    settings = tmp_path / "app_settings.json"
    target = str(tmp_path / "store.mux16")

    assert get_last_preset_file(path=str(settings)) is None

    set_last_preset_file(target, path=str(settings))
    assert get_last_preset_file(path=str(settings)) == target


def test_clear_pointer(tmp_path) -> None:
    """Setting None clears the remembered pointer."""
    settings = tmp_path / "app_settings.json"
    set_last_preset_file("x.mux16", path=str(settings))
    assert get_last_preset_file(path=str(settings)) is not None

    set_last_preset_file(None, path=str(settings))
    assert get_last_preset_file(path=str(settings)) is None


def test_missing_settings_file_returns_none(tmp_path) -> None:
    """A never-written settings file yields no pointer (no crash)."""
    settings = tmp_path / "does_not_exist.json"
    assert get_last_preset_file(path=str(settings)) is None


def test_auto_load_returns_presets_from_saved_file(tmp_path) -> None:
    """The remembered file re-loads its presets on the next manager.

    Mirrors the GUI auto-load path: a preset is saved to an external
    file, the pointer is remembered, and a fresh manager loading that
    same path recovers the preset.
    """
    settings = tmp_path / "app_settings.json"
    store = tmp_path / "store.mux16"

    # Author a store with a user preset and remember it.
    writer = PresetManager(path=str(store))
    writer.add_preset(
        "my_cv",
        Preset(name="My CV", technique="cv", channels=[1, 2]),
    )
    set_last_preset_file(str(store), path=str(settings))

    # Simulate startup auto-load: read pointer, load that file.
    remembered = get_last_preset_file(path=str(settings))
    assert remembered == str(store)

    # Use a temp active path so construction never touches the real
    # per-user store; load_from_path then switches to the remembered one.
    mgr = PresetManager(path=str(tmp_path / "active.mux16"))
    mgr.load_from_path(remembered)
    loaded = mgr.get_preset("my_cv")
    assert loaded is not None
    assert loaded.name == "My CV"
    assert loaded.channels == [1, 2]
