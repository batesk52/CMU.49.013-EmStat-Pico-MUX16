"""Sequencer panel for the EmStat Pico MUX16 (CMU.17.034 — Phase 3).

A PSTrace-"Scripts"-equivalent: saved presets are stacked as reorderable
step blocks and run back-to-back through the shared
:class:`~src.engine.measurement_engine.MeasurementEngine` via a
:class:`~src.engine.sequence_runner.SequenceRunner`.

The panel owns NO engine and NO serial I/O.  It takes the
:class:`~src.data.presets.PresetManager`, the engine, and a
connection-provider callable by injection from
:class:`~src.gui.main_window.MainWindow`, builds a ``SequenceRunner`` from
the current :class:`~src.data.sequence.Sequence` on Run, and wires the
runner's progress / finished / error signals back to the progress label
and the Run/Stop enabled-state.

Reordering uses a ``QListWidget`` in ``InternalMove`` drag-drop mode; each
row stores its :class:`~src.data.sequence.SequenceStep` as item data so
the visual order IS the sequence order (read back via
:meth:`build_sequence`).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.data.presets import PresetManager
from src.data.sequence import Sequence, SequenceStep
from src.engine.sequence_runner import SequenceRunner

logger = logging.getLogger(__name__)

# Item-data role storing the SequenceStep on each list row.
_STEP_ROLE = Qt.ItemDataRole.UserRole

# File dialog filter for sequence files (CMU.17.034).
_SEQUENCE_FILE_FILTER = "MUX16 sequences (*.mux16seq)"
_SEQUENCE_FILE_SUFFIX = ".mux16seq"

# Type alias: a zero-arg callable returning the current connection (or
# None when disconnected).  Injected so the panel never owns the
# connection lifecycle.
ConnectionProvider = Callable[[], object]


class SequencePanel(QWidget):
    """Reorderable list of preset steps with Run / Stop and persistence.

    The panel is a thin orchestrator: it composes a :class:`Sequence`
    from its visual row order, hands it to a :class:`SequenceRunner`
    built against the injected engine + connection, and reflects the
    runner's lifecycle in its progress label and button state.

    Signals:
        sequence_started(): Emitted when a run launches (so the main
            window can suppress its single-run export prompt and disable
            the Start control).
        sequence_stopped(): Emitted when a run finishes, errors, or is
            stopped (so the main window can restore its controls).

    Args:
        preset_manager: Store the step preset names resolve against.
            May be injected later via :meth:`set_preset_manager`.
        engine: Shared measurement engine.  May be injected later via
            :meth:`set_engine`.
        connection_provider: Zero-arg callable returning the current
            connection (or ``None``).  May be injected later via
            :meth:`set_connection_provider`.
        parent: Optional Qt parent.
    """

    sequence_started = pyqtSignal()
    sequence_stopped = pyqtSignal()

    def __init__(
        self,
        preset_manager: Optional[PresetManager] = None,
        engine: Optional[object] = None,
        connection_provider: Optional[ConnectionProvider] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._preset_mgr = preset_manager
        self._engine = engine
        self._connection_provider = connection_provider
        self._runner: Optional[SequenceRunner] = None
        self._setup_ui()
        self._update_buttons(running=False)

    # -- injection ---------------------------------------------------------

    def set_preset_manager(self, manager: PresetManager) -> None:
        """Inject (or replace) the PresetManager the steps resolve against."""
        self._preset_mgr = manager

    def set_engine(self, engine: object) -> None:
        """Inject (or replace) the shared measurement engine."""
        self._engine = engine

    def set_connection_provider(
        self, provider: ConnectionProvider
    ) -> None:
        """Inject (or replace) the connection-provider callable."""
        self._connection_provider = provider

    # -- UI construction ---------------------------------------------------

    def _setup_ui(self) -> None:
        """Build the step list, add/remove controls, and Run/Stop row."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Sequence steps (drag to reorder):"))

        # Reorderable step list.  InternalMove lets the user drag rows;
        # the visual order IS the sequence order.
        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        layout.addWidget(self._list, 1)

        # Per-step option editors (apply to the selected row).
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Repeat:"))
        self._repeat_spin = QSpinBox()
        self._repeat_spin.setRange(1, 9999)
        self._repeat_spin.setValue(1)
        self._repeat_spin.valueChanged.connect(self._on_option_changed)
        opt_row.addWidget(self._repeat_spin)

        opt_row.addWidget(QLabel("Delay (s):"))
        self._delay_spin = QDoubleSpinBox()
        self._delay_spin.setDecimals(2)
        self._delay_spin.setRange(0.0, 86400.0)
        self._delay_spin.setSingleStep(1.0)
        self._delay_spin.valueChanged.connect(self._on_option_changed)
        opt_row.addWidget(self._delay_spin)
        opt_row.addStretch()
        layout.addLayout(opt_row)

        self._list.currentItemChanged.connect(
            self._on_current_item_changed
        )

        # Add / remove row.
        edit_row = QHBoxLayout()
        self._add_btn = QPushButton("Add step")
        self._add_btn.setToolTip("Add a step from the loaded presets")
        self._add_btn.clicked.connect(self._on_add_step)
        edit_row.addWidget(self._add_btn)

        self._remove_btn = QPushButton("Remove step")
        self._remove_btn.clicked.connect(self._on_remove_step)
        edit_row.addWidget(self._remove_btn)
        layout.addLayout(edit_row)

        # Save / Load row.
        io_row = QHBoxLayout()
        self._save_btn = QPushButton("Save...")
        self._save_btn.setToolTip("Save this sequence to a *.mux16seq file")
        self._save_btn.clicked.connect(self._on_save_sequence)
        io_row.addWidget(self._save_btn)

        self._load_btn = QPushButton("Load...")
        self._load_btn.setToolTip("Load a *.mux16seq file")
        self._load_btn.clicked.connect(self._on_load_sequence)
        io_row.addWidget(self._load_btn)
        layout.addLayout(io_row)

        # Run / Stop row + progress label.
        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run")
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #2ca02c; color: white; "
            "font-weight: bold; padding: 6px; }"
        )
        self._run_btn.clicked.connect(self._on_run)
        run_row.addWidget(self._run_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #d62728; color: white; "
            "font-weight: bold; padding: 6px; }"
        )
        self._stop_btn.clicked.connect(self._on_stop)
        run_row.addWidget(self._stop_btn)
        layout.addLayout(run_row)

        self._progress_label = QLabel("Idle")
        self._progress_label.setStyleSheet(
            "color: #888; font-size: 11px;"
        )
        layout.addWidget(self._progress_label)

    # -- step model --------------------------------------------------------

    def _step_label(self, step: SequenceStep) -> str:
        """Return the row caption for a step."""
        suffix = ""
        if step.repeat and step.repeat != 1:
            suffix += f"  x{step.repeat}"
        if step.delay_s:
            suffix += f"  +{step.delay_s:g}s"
        return f"{step.preset_name}{suffix}"

    def add_step(self, step: SequenceStep) -> None:
        """Append a step block to the list.

        Args:
            step: The :class:`SequenceStep` to add.
        """
        item = QListWidgetItem(self._step_label(step))
        item.setData(_STEP_ROLE, step)
        self._list.addItem(item)

    def build_sequence(self, name: str = "sequence") -> Sequence:
        """Compose a :class:`Sequence` from the current row order.

        Args:
            name: Display name for the sequence.

        Returns:
            A :class:`Sequence` whose ``steps`` match the visual order.
        """
        steps: list[SequenceStep] = []
        for i in range(self._list.count()):
            step = self._list.item(i).data(_STEP_ROLE)
            if isinstance(step, SequenceStep):
                steps.append(step)
        return Sequence(name=name, steps=steps)

    def load_sequence(self, sequence: Sequence) -> None:
        """Replace the list contents with a sequence's steps.

        Args:
            sequence: The sequence whose steps populate the list.
        """
        self._list.clear()
        for step in sequence.steps:
            self.add_step(step)

    # -- option editing ----------------------------------------------------

    @pyqtSlot()
    def _on_current_item_changed(self, *args: object) -> None:
        """Reflect the selected row's options in the option spin boxes."""
        item = self._list.currentItem()
        step = item.data(_STEP_ROLE) if item is not None else None
        enabled = isinstance(step, SequenceStep)
        self._repeat_spin.setEnabled(enabled)
        self._delay_spin.setEnabled(enabled)
        if not enabled:
            return
        self._repeat_spin.blockSignals(True)
        self._delay_spin.blockSignals(True)
        self._repeat_spin.setValue(max(1, int(step.repeat)))
        self._delay_spin.setValue(float(step.delay_s))
        self._repeat_spin.blockSignals(False)
        self._delay_spin.blockSignals(False)

    @pyqtSlot()
    def _on_option_changed(self, *args: object) -> None:
        """Write the option spin box values back onto the selected step."""
        item = self._list.currentItem()
        if item is None:
            return
        step = item.data(_STEP_ROLE)
        if not isinstance(step, SequenceStep):
            return
        step.repeat = self._repeat_spin.value()
        step.delay_s = self._delay_spin.value()
        item.setData(_STEP_ROLE, step)
        item.setText(self._step_label(step))

    # -- add / remove ------------------------------------------------------

    @pyqtSlot()
    def _on_add_step(self) -> None:
        """Prompt for a preset and append it as a new step."""
        if self._preset_mgr is None:
            QMessageBox.information(
                self,
                "No Presets",
                "No preset store is loaded yet.",
            )
            return
        names = self._preset_mgr.list_presets()
        if not names:
            QMessageBox.information(
                self,
                "No Presets",
                "Save or import a preset before building a sequence.",
            )
            return
        key, ok = QInputDialog.getItem(
            self,
            "Add Step",
            "Preset:",
            names,
            0,
            False,
        )
        if not ok or not key:
            return
        self.add_step(SequenceStep(preset_name=key))

    @pyqtSlot()
    def _on_remove_step(self) -> None:
        """Remove the currently-selected step."""
        row = self._list.currentRow()
        if row < 0:
            return
        item = self._list.takeItem(row)
        del item

    # -- persistence -------------------------------------------------------

    @pyqtSlot()
    def _on_save_sequence(self) -> None:
        """Save the current sequence to a chosen ``*.mux16seq`` file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save sequence",
            "",
            _SEQUENCE_FILE_FILTER,
        )
        if not path:
            return
        if not path.endswith(_SEQUENCE_FILE_SUFFIX):
            path += _SEQUENCE_FILE_SUFFIX
        try:
            self.build_sequence().save_to_path(path)
        except OSError as exc:
            logger.warning("Could not save sequence %s: %s", path, exc)
            QMessageBox.warning(
                self, "Save Failed", f"Could not save sequence: {exc}"
            )
            return
        logger.info("Saved sequence to %s", path)

    @pyqtSlot()
    def _on_load_sequence(self) -> None:
        """Load a ``*.mux16seq`` file into the list."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load sequence",
            "",
            _SEQUENCE_FILE_FILTER,
        )
        if not path:
            return
        try:
            sequence = Sequence.load_from_path(path)
        except (OSError, ValueError) as exc:
            logger.warning("Could not load sequence %s: %s", path, exc)
            QMessageBox.warning(
                self, "Load Failed", f"Could not load sequence: {exc}"
            )
            return
        self.load_sequence(sequence)
        logger.info("Loaded sequence from %s", path)

    # -- run / stop --------------------------------------------------------

    @pyqtSlot()
    def _on_run(self) -> None:
        """Build a SequenceRunner and start the current sequence."""
        if self._runner is not None:
            return
        if self._preset_mgr is None or self._engine is None:
            QMessageBox.warning(
                self,
                "Not Ready",
                "Sequencer is not wired to a preset store and engine.",
            )
            return
        sequence = self.build_sequence()
        if not sequence.steps:
            QMessageBox.information(
                self, "Empty Sequence", "Add at least one step first."
            )
            return

        connection = (
            self._connection_provider()
            if self._connection_provider is not None
            else None
        )

        try:
            runner = SequenceRunner.from_sequence(
                self._engine,
                connection,
                sequence,
                self._preset_mgr,
                parent=self,
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(
                self,
                "Invalid Sequence",
                f"Cannot start the sequence: {exc}",
            )
            return

        runner.sequence_progress.connect(self._on_progress)
        runner.sequence_finished.connect(self._on_finished)
        runner.sequence_error.connect(self._on_error)
        self._runner = runner

        self._update_buttons(running=True)
        self._progress_label.setText(
            f"Step 0 of {runner.total_steps}"
        )
        self.sequence_started.emit()
        runner.start()

    @pyqtSlot()
    def _on_stop(self) -> None:
        """Abort the running sequence and the in-flight engine step."""
        if self._runner is None:
            return
        self._runner.stop()
        # The runner owns no serial I/O; abort the in-flight engine step
        # so the current measurement actually stops.
        engine = self._engine
        if engine is not None and hasattr(engine, "abort"):
            try:
                engine.abort()
            except Exception as exc:  # noqa: BLE001 - best-effort stop
                logger.warning("Engine abort during stop failed: %s", exc)
        self._teardown_runner()
        self._progress_label.setText("Stopped")

    # -- runner signal handlers -------------------------------------------

    @pyqtSlot(int, int)
    def _on_progress(self, completed: int, total: int) -> None:
        """Update the progress label as each step completes."""
        self._progress_label.setText(f"Step {completed} of {total}")

    @pyqtSlot()
    def _on_finished(self) -> None:
        """Restore controls when the sequence completes."""
        self._teardown_runner()
        self._progress_label.setText("Complete")

    @pyqtSlot(str)
    def _on_error(self, message: str) -> None:
        """Restore controls and surface a sequence error."""
        self._teardown_runner()
        self._progress_label.setText(f"Error: {message}")
        QMessageBox.critical(self, "Sequence Error", message)

    # -- internal ----------------------------------------------------------

    def _teardown_runner(self) -> None:
        """Drop the finished runner and re-enable the controls."""
        runner = self._runner
        self._runner = None
        if runner is not None:
            runner.deleteLater()
        self._update_buttons(running=False)
        self.sequence_stopped.emit()

    def _update_buttons(self, running: bool) -> None:
        """Toggle which controls are enabled based on run state."""
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._add_btn.setEnabled(not running)
        self._remove_btn.setEnabled(not running)
        self._save_btn.setEnabled(not running)
        self._load_btn.setEnabled(not running)
        self._list.setEnabled(not running)
