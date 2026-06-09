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
    """A running sequence raises no modal and keeps Start disabled."""
    # Block every modal entry point used on the finished path so a stray
    # prompt fails loudly.
    for name in ("question", "information", "critical", "warning"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(_no_modal))
    monkeypatch.setattr(
        main_window_mod.QMessageBox, "question", staticmethod(_no_modal)
    )

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


class _FakeResult:
    """Minimal MeasurementResult stand-in for the finished handler."""

    num_points = 3
    measured_channels = [1]
