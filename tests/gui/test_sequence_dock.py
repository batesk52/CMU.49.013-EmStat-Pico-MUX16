"""Tests for the Sequence dock + export suppression (CMU.17.034 -- Phase 4).

Offscreen GUI tests over :class:`src.gui.main_window.MainWindow`:

* a dock titled "Sequence" exists after construction, and
* in sequence mode no modal export dialog is raised and the single-run
  Start control is disabled mid-sequence and re-enabled after.

The real ``MeasurementEngine`` (a QThread) is never started.  The
sequence panel's engine is swapped for a ``MockEngine`` that drives the
``measurement_finished`` lifecycle synchronously, and every ``QMessageBox``
entry point is monkeypatched to fail the test if a modal is raised.
"""

from __future__ import annotations

import os
import sys

import pytest

# Force offscreen platform so PyQt6 boots headless (CI / WSL).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PyQt6 = pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QDockWidget,
    QMessageBox,
)

from src.data.presets import Preset, PresetManager  # noqa: E402
from src.data.sequence import SequenceStep  # noqa: E402
import src.gui.main_window as main_window_mod  # noqa: E402
import src.gui.sequence_panel as sequence_panel_mod  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """Provide a single QApplication for all tests in this module."""
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class MockEngine(QObject):
    """Minimal stand-in for ``MeasurementEngine`` (no QThread)."""

    measurement_finished = pyqtSignal(object)
    measurement_error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.started_configs: list[object] = []
        self._running = False

    def start_measurement(self, connection, config) -> None:
        if self._running:
            raise RuntimeError("MockEngine is already running.")
        self._running = True
        self.started_configs.append(config)

    def isRunning(self) -> bool:  # noqa: N802 - Qt naming
        return self._running

    def abort(self) -> None:
        self._running = False

    def finish_current(self) -> None:
        """Emit measurement_finished for the in-flight step."""
        self._running = False
        self.measurement_finished.emit(object())


def _drain(qapp) -> None:
    """Process pending Qt events so deferred singleShot timers fire."""
    for _ in range(50):
        qapp.processEvents()


def _no_modal(*args, **kwargs):
    """Stand-in for any QMessageBox call -- fails the test if invoked."""
    raise AssertionError("A modal dialog was raised in sequence mode.")


def test_sequence_dock_exists(qapp) -> None:
    """A dock titled 'Sequence' is present after MainWindow construction."""
    window = MainWindow()
    try:
        titles = {
            d.windowTitle() for d in window.findChildren(QDockWidget)
        }
        assert "Sequence" in titles
    finally:
        window.close()


def test_sequence_suppresses_export_and_locks_start(
    qapp, tmp_path, monkeypatch
) -> None:
    """A sequence raises no per-step modal and keeps Start disabled.

    The only modal allowed is the end-of-run save prompt (auto-save is
    off), which we decline with No; any per-step prompt fails loudly.
    """
    # No per-step modal may block the queue.
    for name in ("information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_no_modal))
    # The completion save-prompt is expected -- decline it (No).
    decline = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question", decline)
    monkeypatch.setattr(main_window_mod.QMessageBox, "question", decline)

    window = MainWindow()
    try:
        # Pretend a device is connected so the start/stop control state
        # transitions exercise the connected branch.
        monkeypatch.setattr(
            type(window._connection),  # noqa: SLF001
            "is_connected",
            property(lambda self: True),
        )

        # Swap in a mock engine + a 2-preset store the steps resolve to.
        engine = MockEngine()
        mgr = PresetManager(path=str(tmp_path / "store.mux16"))
        mgr.add_preset(
            "cv1", Preset(name="cv1", technique="cv", channels=[1])
        )
        mgr.add_preset(
            "ca1", Preset(name="ca1", technique="ca", channels=[1])
        )

        panel = window._sequence_panel  # noqa: SLF001
        panel.set_engine(engine)
        panel.set_preset_manager(mgr)
        panel.set_connection_provider(lambda: object())
        panel.add_step(SequenceStep(preset_name="cv1"))
        panel.add_step(SequenceStep(preset_name="ca1"))

        # Also block the sequence panel's own message boxes.
        monkeypatch.setattr(
            sequence_panel_mod.QMessageBox,
            "critical",
            staticmethod(_no_modal),
        )
        monkeypatch.setattr(
            sequence_panel_mod.QMessageBox,
            "warning",
            staticmethod(_no_modal),
        )

        # Start the sequence.
        panel._on_run()  # noqa: SLF001
        assert window._sequence_active is True  # noqa: SLF001
        # Start control is disabled while the sequence runs.
        assert (
            window._meas_panel._start_btn.isEnabled() is False
        )  # noqa: SLF001

        # Step 1 finishes -> the main window's finished handler must NOT
        # prompt (it would raise via _no_modal); the queue advances.
        window._on_measurement_finished(_FakeResult())  # noqa: SLF001
        engine.finish_current()
        _drain(qapp)
        assert len(engine.started_configs) == 2

        # Step 2 finishes -> sequence completes, controls restore.
        window._on_measurement_finished(_FakeResult())  # noqa: SLF001
        engine.finish_current()
        _drain(qapp)

        assert window._sequence_active is False  # noqa: SLF001
        # Start re-enabled now the sequence is done (device connected).
        assert window._meas_panel._start_btn.isEnabled() is True  # noqa: SLF001
    finally:
        window.close()


def test_measurement_started_switches_plot_technique(qapp) -> None:
    """Each engine ``measurement_started`` retargets the live plot.

    Sequence steps launch straight through the engine (not the single-run
    Start path), so the plot must follow ``measurement_started`` or a mixed
    CV->EIS->CA sequence would draw every step on the first step's axes
    (e.g. CV data on an EIS Nyquist view).
    """
    window = MainWindow()
    try:
        # An EIS step makes the active tab EIS-mode (Nyquist/Bode selector).
        window._on_measurement_started("eis")  # noqa: SLF001
        container = window._plot_container  # noqa: SLF001
        assert container._technique == "eis"  # noqa: SLF001
        assert container._selector_row.isHidden() is False  # noqa: SLF001

        # A following CV run retargets to the time/IV view (stack 0) and
        # hides the EIS-only selector -- i.e. the CV plot is visible again.
        window._on_measurement_started("cv")  # noqa: SLF001
        container = window._plot_container  # noqa: SLF001
        assert container._technique == "cv"  # noqa: SLF001
        assert container._stack.currentIndex() == 0  # noqa: SLF001
        assert container._selector_row.isHidden() is True  # noqa: SLF001
    finally:
        window.close()


def test_sequence_creates_one_plot_tab_per_step(qapp) -> None:
    """A sequence accumulates a labelled live-plot tab for each step."""
    window = MainWindow()
    try:
        window._on_sequence_started()  # noqa: SLF001
        # Sequence start clears the tab strip; steps then add tabs.
        assert window._plot_tabs.count() == 0  # noqa: SLF001

        window._on_measurement_started("cv")  # noqa: SLF001
        window._on_measurement_started("eis")  # noqa: SLF001
        window._on_measurement_started("ca")  # noqa: SLF001

        assert window._plot_tabs.count() == 3  # noqa: SLF001
        labels = [
            window._plot_tabs.tabText(i)  # noqa: SLF001
            for i in range(3)
        ]
        assert labels == ["1·CV", "2·EIS", "3·CA"]
        # The active container tracks the most recently started step.
        assert window._plot_container._technique == "ca"  # noqa: SLF001
    finally:
        window.close()


def test_single_run_uses_one_replaced_tab(qapp) -> None:
    """A single run keeps one tab, replaced (not accumulated) per run."""
    window = MainWindow()
    try:
        window._on_measurement_started("cv")  # noqa: SLF001
        assert window._plot_tabs.count() == 1  # noqa: SLF001
        assert window._plot_tabs.tabText(0) == "CV"  # noqa: SLF001

        window._on_measurement_started("eis")  # noqa: SLF001
        assert window._plot_tabs.count() == 1  # noqa: SLF001 (replaced)
        assert window._plot_tabs.tabText(0) == "EIS"  # noqa: SLF001
    finally:
        window.close()


def test_sequence_export_base_respects_auto_save_toggle(qapp) -> None:
    """The sequencer auto-saves only when the GUI auto-save toggle is on.

    Off by default -> no base dir (the sequence writes nothing). Enabling
    the auto-save toggle opts the sequence in, sharing the single-run
    policy rather than forcing saves on.
    """
    window = MainWindow()
    try:
        # Opt-in default: auto-save off -> no export base.
        assert window._meas_panel.is_auto_save_enabled() is False  # noqa: SLF001
        assert window._sequence_export_base() is None  # noqa: SLF001

        # User enables auto-save -> the sequencer gets a real base dir.
        window._meas_panel.set_auto_save(True, "")  # noqa: SLF001
        base = window._sequence_export_base()  # noqa: SLF001
        assert base
    finally:
        window.close()


def test_preset_selection_does_not_enable_auto_save(
    qapp, tmp_path
) -> None:
    """Selecting a preset never auto-enables auto-save (opt-in only).

    Even a preset carrying ``auto_save=True`` must leave the toggle off;
    the user activates auto-save explicitly in the GUI.
    """
    from src.data.presets import Preset, PresetManager

    window = MainWindow()
    try:
        # Swap in a temp store so we never touch the real user store.
        mgr = PresetManager(path=str(tmp_path / "store.mux16"))
        mgr.add_preset(
            "forces_save",
            Preset(
                name="forces_save",
                technique="cv",
                channels=[1],
                auto_save=True,
            ),
        )
        window._preset_mgr = mgr  # noqa: SLF001

        assert window._meas_panel.is_auto_save_enabled() is False  # noqa: SLF001
        window._on_preset_selected("forces_save")  # noqa: SLF001
        assert window._meas_panel.is_auto_save_enabled() is False  # noqa: SLF001
    finally:
        window.close()


def test_sequence_completion_autosaved_does_not_prompt(
    qapp, monkeypatch
) -> None:
    """When the run already auto-saved, completion reports without a prompt."""
    from src.data.models import MeasurementResult

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_no_modal))
    window = MainWindow()
    try:
        window._sequence_autosaved = True  # noqa: SLF001
        window._sequence_results = [  # noqa: SLF001
            MeasurementResult(technique="cv")
        ]
        # Must not raise via _no_modal (no prompt on the auto-saved path).
        window._on_sequence_completed()  # noqa: SLF001
        assert window._sequence_results == []  # noqa: SLF001
    finally:
        window.close()


def test_sequence_completion_saves_all_steps_when_accepted(
    qapp, tmp_path, monkeypatch
) -> None:
    """Auto-save off + accept the prompt -> every step is written.

    Lands one ``<stamp>_sequence`` parent with a ``stepNN_<technique>``
    subfolder per step (each an ordinary per-step export).
    """
    from src.data.models import DataPoint, MeasurementResult

    monkeypatch.setattr(
        main_window_mod, "get_export_dir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda *a, **k: None)
    )
    window = MainWindow()
    try:

        def _result(tech):
            r = MeasurementResult(technique=tech)
            r.add_point(
                DataPoint(
                    timestamp=0.1,
                    channel=1,
                    variables={"potential": 0.1, "current": 1e-5},
                )
            )
            return r

        window._sequence_autosaved = False  # noqa: SLF001
        window._sequence_results = [  # noqa: SLF001
            _result("cv"),
            _result("eis"),
        ]
        window._on_sequence_completed()  # noqa: SLF001

        seq_dirs = list(tmp_path.glob("*_sequence"))
        assert len(seq_dirs) == 1
        steps = sorted(p.name for p in seq_dirs[0].iterdir())
        assert steps == ["step01_cv", "step02_eis"]
        assert (seq_dirs[0] / "step01_cv" / "ch01.csv").exists()
        assert window._sequence_results == []  # noqa: SLF001
    finally:
        window.close()


def test_sequence_completion_declined_saves_nothing(
    qapp, tmp_path, monkeypatch
) -> None:
    """Declining the completion prompt writes no files."""
    from src.data.models import MeasurementResult

    monkeypatch.setattr(
        main_window_mod, "get_export_dir", lambda: str(tmp_path)
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    window = MainWindow()
    try:
        window._sequence_autosaved = False  # noqa: SLF001
        window._sequence_results = [  # noqa: SLF001
            MeasurementResult(technique="cv")
        ]
        window._on_sequence_completed()  # noqa: SLF001
        assert list(tmp_path.glob("*_sequence")) == []
        assert window._sequence_results == []  # noqa: SLF001
    finally:
        window.close()


class _FakeResult:
    """Minimal MeasurementResult stand-in for the finished handler."""

    num_points = 3
    measured_channels = [1]
