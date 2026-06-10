"""Vendored CMU.49.011 electrochemistry analysis toolkit (READ-ONLY).

PROVENANCE
    Source repo:   CMU.49.011-Electrochemistry
                   (local: _all_work/_codebases/CMU.49.011-Electrochemistry)
    Source commit: e35ccfc ("updated analysis")
    Copied:        2026-06-09
    Packages:      src/analysis, src/dataloaders, src/utils
    Mechanical edits only: absolute imports rewritten from
    ``src.<pkg>`` / ``<pkg>`` to ``src.vendor.electrochem_analysis.<pkg>``.
    No behavioral edits. Fixes go upstream in CMU.49.011, then re-copy.

The analysis modules import ``matplotlib.pyplot`` at module top; the
Agg backend is forced here, before any of them load, so the vendored
code renders figures headlessly and never spins up a Qt/Tk canvas
inside the host application (Windows DLL/threading safety).
"""

import matplotlib

matplotlib.use("Agg", force=True)
