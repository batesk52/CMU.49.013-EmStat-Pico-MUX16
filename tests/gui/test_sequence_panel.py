"""Tests for the SequencePanel (CMU.17.034 -- Phase 3).

Exercises :class:`src.gui.sequence_panel.SequencePanel` headless
(offscreen): step blocks reorder through the list model, the visual
order maps to ``Sequence.steps`` order, and a sequence round-trips
through a temp ``*.mux16seq`` file.

Reordering is driven via the list model API (``takeItem`` /
``insertItem``) -- NOT a physical drag -- so the test is deterministic.
"""

from __future__ import annotations

import os
import sys

import pytest

# Force offscreen platform so PyQt6 boots headless (CI / WSL).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PyQt6 = pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.data.sequence import Sequence, SequenceStep  # noqa: E402
from src.gui.sequence_panel import SequencePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """Provide a single QApplication for all tests in this module."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _step_names(panel: SequencePanel) -> list[str]:
    """Return the preset_name of every step in visual order."""
    return [s.preset_name for s in panel.build_sequence().steps]


def test_three_blocks_added_in_order(qapp) -> None:
    """Adding three step blocks preserves insertion order."""
    panel = SequencePanel()
    panel.add_step(SequenceStep(preset_name="cv1"))
    panel.add_step(SequenceStep(preset_name="ca1"))
    panel.add_step(SequenceStep(preset_name="dpv1"))

    assert _step_names(panel) == ["cv1", "ca1", "dpv1"]


def test_reorder_via_model_changes_sequence_order(qapp) -> None:
    """Moving a row via the list model reorders Sequence.steps."""
    panel = SequencePanel()
    for name in ("cv1", "ca1", "dpv1"):
        panel.add_step(SequenceStep(preset_name=name))

    # Move the first row (cv1) to the end via the model API.
    item = panel._list.takeItem(0)  # noqa: SLF001 - test introspection
    panel._list.insertItem(panel._list.count(), item)  # noqa: SLF001

    assert _step_names(panel) == ["ca1", "dpv1", "cv1"]


def test_save_reload_round_trips(qapp, tmp_path) -> None:
    """A built sequence saves to *.mux16seq and reloads equal."""
    panel = SequencePanel()
    panel.add_step(SequenceStep(preset_name="cv1", repeat=2, delay_s=1.5))
    panel.add_step(SequenceStep(preset_name="ca1"))
    panel.add_step(SequenceStep(preset_name="dpv1", delay_s=0.5))

    path = str(tmp_path / "seq.mux16seq")
    panel.build_sequence(name="round").save_to_path(path)

    reloaded = Sequence.load_from_path(path)
    # Load it back into a fresh panel and compare the recomposed model.
    panel2 = SequencePanel()
    panel2.load_sequence(reloaded)

    assert panel2.build_sequence(name="round") == panel.build_sequence(
        name="round"
    )
    # Independent ground-truth check on the persisted file.
    assert reloaded.steps[0].preset_name == "cv1"
    assert reloaded.steps[0].repeat == 2
    assert reloaded.steps[0].delay_s == 1.5
    assert [s.preset_name for s in reloaded.steps] == [
        "cv1",
        "ca1",
        "dpv1",
    ]
