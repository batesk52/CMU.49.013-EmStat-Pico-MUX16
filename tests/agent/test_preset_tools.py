"""Tests for the agent preset/sequence save & load tools.

The tools open a native file dialog in the app, but every handler also
accepts an explicit ``path`` (the headless fallback). These tests drive the
path-based branch — it exercises all of the validation, persistence, and
round-trip logic without needing a Qt event loop; the dialog branch only
swaps how the path is obtained.
"""

from __future__ import annotations

import asyncio
import os

from src.agent.preset_tools import build_preset_tools
from src.data.presets import read_preset_file
from src.data.sequence import Sequence, build_config


def _tools():
    """Build the tools with no GUI dialog (path-based fallback)."""
    return {d["name"]: handler for (d, handler) in build_preset_tools(file_dialog=None)}


def _run(coro):
    return asyncio.run(coro)


# ---- save_preset ----------------------------------------------------------


def test_save_preset_writes_validated_file(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "ceox_cv.mux16")
    res = _run(
        tools["save_preset"](
            {
                "name": "CeOx CV",
                "technique": "cv",
                "params": {"scan_rate": 0.1, "cr": "100u"},
                "channels": [1, 2],
                "path": path,
            }
        )
    )
    assert res["ok"] is True
    assert res["replaced"] is False
    assert os.path.isfile(path)
    presets = read_preset_file(path)
    assert "ceox_cv" in presets
    preset = presets["ceox_cv"]
    assert preset.technique == "cv"
    assert preset.channels == [1, 2]
    # Defaults are merged in, and the supplied values win.
    assert preset.params["scan_rate"] == 0.1
    assert "e_begin" in preset.params


def test_save_preset_appends_suffix(tmp_path) -> None:
    tools = _tools()
    res = _run(
        tools["save_preset"](
            {
                "name": "x",
                "technique": "cv",
                "params": {},
                "channels": [1],
                "path": str(tmp_path / "noext"),
            }
        )
    )
    assert res["ok"] is True
    assert res["path"].endswith(".mux16")
    assert (tmp_path / "noext.mux16").is_file()


def test_save_preset_merges_into_existing_file(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "store.mux16")
    _run(
        tools["save_preset"](
            {"name": "A", "technique": "cv", "params": {}, "channels": [1], "path": path}
        )
    )
    res = _run(
        tools["save_preset"](
            {"name": "B", "technique": "eis", "params": {}, "channels": [1], "path": path}
        )
    )
    assert res["ok"] is True
    assert res["replaced"] is False
    presets = read_preset_file(path)
    assert {"a", "b"} <= set(presets)  # the first preset was preserved


def test_save_preset_rejects_bad_channel_without_writing(tmp_path) -> None:
    tools = _tools()
    path = tmp_path / "bad.mux16"
    res = _run(
        tools["save_preset"](
            {
                "name": "bad",
                "technique": "cv",
                "params": {},
                "channels": [99],
                "path": str(path),
            }
        )
    )
    assert res["ok"] is False
    assert not path.exists()


def test_save_preset_no_path_no_dialog_asks_for_path() -> None:
    tools = _tools()
    res = _run(
        tools["save_preset"](
            {"name": "x", "technique": "cv", "params": {}, "channels": [1]}
        )
    )
    assert res["ok"] is False
    assert "path" in res["error"].lower()


# ---- save_sequence --------------------------------------------------------


def test_save_sequence_roundtrips_via_build_config(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "char.mux16seq")
    res = _run(
        tools["save_sequence"](
            {
                "name": "CeOx characterization",
                "steps": [
                    {"technique": "cv", "params": {"scan_rate": 0.05}, "channels": [1, 2]},
                    {"technique": "eis", "params": {"cr": "100u"}, "channels": [1, 2]},
                ],
                "path": path,
            }
        )
    )
    assert res["ok"] is True
    assert res["n_steps"] == 2
    seq = Sequence.load_from_path(path)
    assert seq.name == "CeOx characterization"
    assert len(seq.steps) == 2
    cfg0 = build_config(seq.steps[0])
    assert cfg0.technique == "cv" and cfg0.channels == [1, 2]
    cfg1 = build_config(seq.steps[1])
    assert cfg1.technique == "eis"


def test_save_sequence_manual_preserves_re_ce(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "manual.mux16seq")
    res = _run(
        tools["save_sequence"](
            {
                "name": "Manual",
                "steps": [
                    {
                        "technique": "cv",
                        "params": {},
                        "channels": [1, 3],
                        "electrode_config_mode": "manual",
                        "re_ce_channels": [13, 1],
                    }
                ],
                "path": path,
            }
        )
    )
    assert res["ok"] is True
    cfg = build_config(Sequence.load_from_path(path).steps[0])
    assert cfg.electrode_config_mode == "manual"
    assert cfg.re_ce_channels == [13, 1]


def test_save_sequence_rejects_empty_steps(tmp_path) -> None:
    tools = _tools()
    res = _run(
        tools["save_sequence"](
            {"name": "x", "steps": [], "path": str(tmp_path / "e.mux16seq")}
        )
    )
    assert res["ok"] is False


def test_save_sequence_reports_bad_step_index(tmp_path) -> None:
    tools = _tools()
    res = _run(
        tools["save_sequence"](
            {
                "name": "x",
                "steps": [
                    {"technique": "cv", "params": {}, "channels": [1]},
                    {"technique": "eis", "params": {}, "channels": [99]},
                ],
                "path": str(tmp_path / "x.mux16seq"),
            }
        )
    )
    assert res["ok"] is False
    assert "step 2" in res["error"]


# ---- load -----------------------------------------------------------------


def test_load_preset_returns_runnable_config(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "s.mux16")
    _run(
        tools["save_preset"](
            {
                "name": "CeOx EIS",
                "technique": "eis",
                "params": {"cr": "50u", "freq_end": 10.0},
                "channels": [1],
                "path": path,
            }
        )
    )
    res = _run(tools["load_preset"]({"path": path}))
    assert res["ok"] is True
    assert res["count"] == 1
    preset = res["presets"][0]
    assert preset["technique"] == "eis"
    assert preset["channels"] == [1]
    assert preset["params"]["cr"] == "50u"


def test_load_sequence_returns_steps(tmp_path) -> None:
    tools = _tools()
    path = str(tmp_path / "c.mux16seq")
    _run(
        tools["save_sequence"](
            {"name": "C", "steps": [{"technique": "cv", "params": {}, "channels": [1]}], "path": path}
        )
    )
    res = _run(tools["load_sequence"]({"path": path}))
    assert res["ok"] is True
    assert res["n_steps"] == 1
    assert res["steps"][0]["technique"] == "cv"


def test_load_preset_missing_file_errors(tmp_path) -> None:
    tools = _tools()
    res = _run(tools["load_preset"]({"path": str(tmp_path / "nope.mux16")}))
    assert res["ok"] is False
