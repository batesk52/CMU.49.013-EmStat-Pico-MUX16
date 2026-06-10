"""Tests for forced auto-save (script provenance) on EIS/GEIS.

EIS/GEIS encode the applied DC bias and current-range settings only in the
generated MethodSCRIPT, so auto-save is forced on for those techniques to
guarantee a ``_script.mscr`` lands in every run folder. Covers the pure
decision helper :func:`src.gui.main_window._forces_auto_save`.
"""

from __future__ import annotations

import os

import pytest

# Force offscreen platform so PyQt6 boots in headless CI / WSL.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6")

from src.gui.main_window import (  # noqa: E402
    _ALWAYS_AUTOSAVE_TECHNIQUES,
    _forces_auto_save,
)


@pytest.mark.parametrize("technique", ["eis", "geis", "EIS", "GEIS", "Eis"])
def test_eis_geis_force_auto_save(technique):
    assert _forces_auto_save(technique) is True


@pytest.mark.parametrize(
    "technique", ["cv", "ca", "swv", "dpv", "lsv", "ca_alt_mux", "ocp", ""]
)
def test_other_techniques_do_not_force_auto_save(technique):
    assert _forces_auto_save(technique) is False


def test_always_autosave_set_is_exactly_eis_and_geis():
    assert _ALWAYS_AUTOSAVE_TECHNIQUES == frozenset({"eis", "geis"})
