"""Main application window for the EmStat Pico MUX16 Controller.

Assembles all GUI panels (connection, technique, channel, measurement
controls) with a live pyqtgraph plot in a dock-based QMainWindow layout.
Wires Qt signals between the control panels, measurement engine, and
plot widget so that data flows engine -> GUI exclusively through signals.

Typical launch::

    source ~/envs/cmu.49.013/Scripts/activate
    python -m src.gui.main_window

Or import and instantiate::

    from src.gui.main_window import MainWindow
    window = MainWindow()
    window.show()
"""

from __future__ import annotations

import csv
import logging
import os
import sys
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import (
    QEvent,
    QObject,
    Qt,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.comms.serial_connection import PicoConnection
from src.data.exporters import PsSessionExporter
from src.data.models import (
    EXTERNAL_RE_CE_CHANNEL,
    ON_BOARD_RE_CE_CHANNEL,
    AutoSaveConfig,
    MeasurementResult,
    TechniqueConfig,
)
from src.data.presets import Preset, PresetManager
from src.engine.measurement_engine import MeasurementEngine
from src.gui.controls import (
    ChannelPanel,
    ConnectionPanel,
    ElectrodeConfigPanel,
    ManualChannelPanel,
    MeasurementControlPanel,
    TechniquePanel,
)
from src.gui.eis_plot_container import EISPlotContainer
from src.gui.plot_widget import LivePlotWidget
from src.gui.toggle_switch import ToggleSwitch
from src.gui.workers import ConnectWorker

logger = logging.getLogger(__name__)

# Application metadata
APP_NAME = "EmStat Pico MUX16 Controller"
APP_VERSION = "0.1.0"


class _NoWheelScrollFilter(QObject):
    """Stop the mouse-wheel from changing combo/spin box values, while
    still scrolling the surrounding panel.

    Mouse-scrolling over a QComboBox or QSpinBox/QDoubleSpinBox silently
    changes its value — easy to do by accident, and dangerous on
    measurement parameters (t_eq, E vertex, current range, ...). The
    wheel over such a control NEVER changes its value (focused or not);
    instead the event is forwarded to the enclosing scroll area so the
    panel still scrolls (no dead zones over fields). The user must click
    and type, or use the dropdown, to change a value. Installed
    application-wide so every existing and future combo/spinbox is
    covered without per-widget plumbing.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(
            obj, (QComboBox, QAbstractSpinBox)
        ):
            # Forward the wheel to the nearest scroll area so the panel
            # scrolls, then consume it from the control so its value can
            # never change.
            parent = obj.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    QApplication.sendEvent(parent.viewport(), event)
                    break
                parent = parent.parentWidget()
            return True
        return False


class _LogSignalBridge(QObject):
    """Thread-safe bridge: emits a signal so the GUI thread updates."""

    log_message = pyqtSignal(str)


class _LogHandler(logging.Handler):
    """Logging handler that appends formatted records to a QPlainTextEdit.

    Uses a signal to marshal messages from any thread to the GUI thread,
    since QWidget.appendPlainText must only be called from the GUI thread.
    """

    def __init__(self, text_widget: QPlainTextEdit) -> None:
        super().__init__()
        self._bridge = _LogSignalBridge()
        self._bridge.log_message.connect(text_widget.appendPlainText)

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self._bridge.log_message.emit(msg)


class MainWindow(QMainWindow):
    """Main application window for the EmStat Pico MUX16 Controller.

    Provides a dock-based layout with:

    * **Left panel** -- Connection, Technique, Channel, and Measurement
      control panels stacked vertically.
    * **Centre** -- Live pyqtgraph plot widget for real-time data.
    * **Bottom** -- Log console showing application messages.
    * **Status bar** -- Connection state, measurement progress, and
      current channel indicator.
    * **Menu bar** -- File (export, quit), Device (connect, disconnect),
      Help (about).

    All serial I/O is delegated to ``PicoConnection`` and
    ``MeasurementEngine``; the GUI thread never performs blocking I/O.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 800)

        # Core objects
        self._connection = PicoConnection()
        self._engine = MeasurementEngine(parent=self)
        self._last_result: Optional[MeasurementResult] = None
        self._preset_mgr = PresetManager()
        self._auto_save_active = False
        # Background worker for the (blocking) connect handshake.
        self._connect_worker: Optional[ConnectWorker] = None
        self._connect_port: str = ""

        # Install app-wide filter that blocks accidental wheel-scroll
        # changes on combo boxes and spin boxes.
        self._no_wheel_filter = _NoWheelScrollFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._no_wheel_filter)

        # Build UI
        self._build_central_widget()
        # Put the dock tab bar at the TOP so it doesn't collide with the
        # ManualChannelPanel's bulk-set buttons in Mode C.
        self.setTabPosition(
            Qt.DockWidgetArea.LeftDockWidgetArea,
            QTabWidget.TabPosition.North,
        )
        self._build_control_dock()
        self._build_log_dock()
        # Tab the Log dock behind the Controls dock so Settings is the
        # default view; user clicks "Log" tab when a measurement runs.
        self.tabifyDockWidget(self._control_dock, self._log_dock)
        self._control_dock.raise_()
        self._build_menu_bar()
        self._build_status_bar()

        # Wire signals
        self._wire_signals()

        # Load presets into UI
        self._load_presets_into_ui()

        # Initial state
        self._update_ui_disconnected()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_central_widget(self) -> None:
        """Create the EIS plot container as the central widget."""
        self._plot_container = EISPlotContainer(parent=self)
        self._plot = self._plot_container.nyquist
        self.setCentralWidget(self._plot_container)

    def _build_control_dock(self) -> None:
        """Build left dock with stacked control panels."""
        dock = QDockWidget("Settings", self)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self._control_dock = dock

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self._conn_panel = ConnectionPanel()
        layout.addWidget(self._conn_panel)

        # Verbose-logging switch. Off = INFO (normal), on = DEBUG, which
        # surfaces the per-marker device trace and the "Ended without '+'
        # END_MEAS marker" warning — used to diagnose slow/missing
        # end-of-measurement save prompts. Centered directly under the
        # connection box so it's reachable before starting a run.
        log_row = QHBoxLayout()
        log_row.setContentsMargins(0, 2, 0, 2)
        log_row.setSpacing(6)
        self._log_caption = QLabel("Log output:")
        self._log_caption.setStyleSheet(
            "color: #f5f5f5; font-weight: bold; font-size: 12px;"
        )
        self._info_label = QLabel("INFO")
        self._debug_label = QLabel("DEBUG")
        self._log_switch = ToggleSwitch()
        self._log_switch.setToolTip(
            "Set logging to DEBUG. Shows the device's raw marker stream "
            "and end-of-measurement diagnostics. Verbose — leave off for "
            "normal use."
        )
        self._log_switch.toggled.connect(self._on_debug_log_toggled)
        log_row.addStretch()
        log_row.addWidget(self._log_caption)
        log_row.addSpacing(4)
        log_row.addWidget(self._info_label)
        log_row.addWidget(self._log_switch)
        log_row.addWidget(self._debug_label)
        log_row.addStretch()
        layout.addLayout(log_row)
        self._update_log_switch_labels(False)

        self._tech_panel = TechniquePanel()
        layout.addWidget(self._tech_panel)

        # Electrode-config radio sits between Technique and Channels so
        # the user picks the wiring mode before configuring channels.
        self._electrode_config_panel = ElectrodeConfigPanel()
        layout.addWidget(self._electrode_config_panel)

        # Channel grid (modes A and B: 16-WE workflow).
        self._chan_panel = ChannelPanel()
        layout.addWidget(self._chan_panel)

        # Manual channel pairing (mode C: 14-row per-WE table).  Hidden
        # by default; shown when the electrode-config panel switches to
        # "manual".
        self._manual_channel_panel = ManualChannelPanel()
        self._manual_channel_panel.setVisible(False)
        layout.addWidget(self._manual_channel_panel)

        self._meas_panel = MeasurementControlPanel()
        layout.addWidget(self._meas_panel)

        layout.addStretch()
        # Wrap container in a scroll area so the panel stack (especially
        # Mode C's 14-row ManualChannelPanel) doesn't overflow the dock.
        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

    def _build_log_dock(self) -> None:
        """Build left dock with a log console, tabbed behind Settings."""
        dock = QDockWidget("Log", self)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )

        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumBlockCount(2000)
        dock.setWidget(self._log_text)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self._log_dock = dock

        # Attach a logging handler to the root logger. Keep a reference so
        # closeEvent() can remove it — otherwise a stale handler keeps
        # firing log records into this (destroyed) widget after the window
        # closes, leaking the handler and risking a crash on a second
        # MainWindow (e.g. in tests).
        self._log_handler = _LogHandler(self._log_text)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        logging.getLogger().addHandler(self._log_handler)

    def _build_menu_bar(self) -> None:
        """Create the menu bar with File, Device, and Help menus."""
        menu_bar = self.menuBar()

        # -- File menu ----
        file_menu = menu_bar.addMenu("&File")

        self._export_action = QAction("&Export Results...", self)
        self._export_action.setShortcut("Ctrl+E")
        self._export_action.setEnabled(False)
        self._export_action.triggered.connect(self._on_export)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        # -- Device menu ----
        device_menu = menu_bar.addMenu("&Device")

        self._connect_action = QAction("&Connect", self)
        self._connect_action.triggered.connect(self._on_connect)
        device_menu.addAction(self._connect_action)

        self._disconnect_action = QAction("&Disconnect", self)
        self._disconnect_action.setEnabled(False)
        self._disconnect_action.triggered.connect(self._on_disconnect)
        device_menu.addAction(self._disconnect_action)

        # -- Help menu ----
        help_menu = menu_bar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _build_status_bar(self) -> None:
        """Create permanent status bar widgets."""
        self._status_conn = QLabel("Disconnected")
        self._status_conn.setStyleSheet(
            "padding: 0 8px; font-size: 11px;"
        )
        self.statusBar().addPermanentWidget(self._status_conn)

        self._status_progress = QLabel("Idle")
        self._status_progress.setStyleSheet(
            "padding: 0 8px; font-size: 11px;"
        )
        self.statusBar().addPermanentWidget(self._status_progress)

        self._status_channel = QLabel("CH: --")
        self._status_channel.setStyleSheet(
            "padding: 0 8px; font-size: 11px;"
        )
        self.statusBar().addPermanentWidget(self._status_channel)

        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        """Connect all panel, engine, and plot signals."""
        # Connection panel -> main window
        self._conn_panel.connect_requested.connect(self._on_connect)
        self._conn_panel.disconnect_requested.connect(
            self._on_disconnect
        )

        # Technique panel -> plot container
        self._tech_panel.technique_changed.connect(
            self._plot_container.set_technique
        )

        # Electrode-config panel -> swap visible channel panel
        self._electrode_config_panel.mode_changed.connect(
            self._on_electrode_mode_changed
        )

        # Measurement controls -> engine actions
        self._meas_panel.start_requested.connect(
            self._on_start_measurement
        )
        self._meas_panel.stop_requested.connect(
            self._on_stop_measurement
        )
        self._meas_panel.halt_requested.connect(
            self._on_halt_measurement
        )
        self._meas_panel.resume_requested.connect(
            self._on_resume_measurement
        )

        # Preset selector -> main window
        self._tech_panel.preset_selected.connect(
            self._on_preset_selected
        )

        # Save preset button -> main window
        self._tech_panel.save_preset_requested.connect(
            self._on_save_preset
        )
        self._tech_panel.delete_preset_requested.connect(
            self._on_delete_preset
        )

        # Engine signals -> GUI updates
        self._engine.data_point_ready.connect(
            self._plot_container.on_data_point
        )
        self._engine.measurement_started.connect(
            self._on_measurement_started
        )
        self._engine.measurement_finished.connect(
            self._on_measurement_finished
        )
        self._engine.measurement_error.connect(
            self._on_measurement_error
        )
        self._engine.channel_changed.connect(
            self._on_channel_changed
        )
        self._engine.auto_save_completed.connect(
            self._on_auto_save_completed
        )

    @pyqtSlot(bool)
    def _on_debug_log_toggled(self, enabled: bool) -> None:
        """Switch root logging between DEBUG (verbose) and INFO.

        Affects both the console and the in-app log dock, since both
        attach to the root logger.

        Args:
            enabled: True for DEBUG, False for INFO.
        """
        level = logging.DEBUG if enabled else logging.INFO
        logging.getLogger().setLevel(level)
        self._update_log_switch_labels(enabled)
        logger.info(
            "Logging level set to %s.", logging.getLevelName(level)
        )

    def _update_log_switch_labels(self, debug: bool) -> None:
        """Emphasize the active side of the INFO/DEBUG switch."""
        # Match the "Log output:" caption: same 12px size and bold weight
        # so the three labels read as one consistent group — only the
        # colour changes to mark the active side.
        active = "color: #f5f5f5; font-weight: bold; font-size: 12px;"
        inactive = "color: #808080; font-weight: bold; font-size: 12px;"
        self._info_label.setStyleSheet(inactive if debug else active)
        self._debug_label.setStyleSheet(active if debug else inactive)

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    @pyqtSlot()
    def _on_connect(self, port: str = "") -> None:
        """Handle connect request from panel or menu.

        Args:
            port: COM port path. If empty, reads from the connection
                panel's current selection.
        """
        if not port:
            port = self._conn_panel.selected_port()
        if not port:
            self._conn_panel.set_error("No port selected")
            return
        # A connect attempt is already in flight — ignore re-entry.
        if (
            self._connect_worker is not None
            and self._connect_worker.isRunning()
        ):
            return

        # The connect handshake does blocking serial I/O, so run it on a
        # worker thread and update the UI from its result signals — never
        # block the GUI event loop.
        self._conn_panel.set_connecting()
        self._connect_port = port
        worker = ConnectWorker(self._connection, port, self)
        worker.succeeded.connect(self._on_connect_succeeded)
        worker.failed.connect(self._on_connect_failed)
        # Free the QThread once it finishes so workers don't accumulate as
        # MainWindow children across repeated connect attempts.
        worker.finished.connect(worker.deleteLater)
        self._connect_worker = worker
        worker.start()

    @pyqtSlot(str)
    def _on_connect_succeeded(self, firmware: str) -> None:
        """Handle a successful connect handshake (GUI thread)."""
        self._connect_worker = None
        self._conn_panel.set_connected(firmware)
        self._update_ui_connected()
        logger.info(
            "Connected to %s (firmware: %s)",
            self._connect_port,
            firmware,
        )

    @pyqtSlot(str)
    def _on_connect_failed(self, message: str) -> None:
        """Handle a failed connect handshake (GUI thread)."""
        self._connect_worker = None
        self._conn_panel.set_error(message)
        logger.error("Connection failed: %s", message)

    @pyqtSlot()
    def _on_disconnect(self) -> None:
        """Handle disconnect request."""
        if self._engine.isRunning():
            self._engine.abort()
            self._engine.wait(3000)

        self._connection.disconnect()
        self._conn_panel.set_disconnected()
        self._update_ui_disconnected()
        logger.info("Disconnected.")

    # ------------------------------------------------------------------
    # Measurement lifecycle
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_start_measurement(self) -> None:
        """Gather config from panels and start the measurement engine."""
        if not self._connection.is_connected:
            self.statusBar().showMessage("Not connected to device.")
            return

        technique = self._tech_panel.selected_technique()
        params = self._tech_panel.get_params()

        # Resolve WE + RE/CE channels from the electrode-config mode.
        mode = self._electrode_config_panel.selected_mode()
        re_ce_channels: list[int] = []
        if mode == "manual":
            channels, re_ce_channels = (
                self._manual_channel_panel.selected_pairs()
            )
            if not channels:
                QMessageBox.warning(
                    self,
                    "No Channels",
                    "Enable at least one CH1-CH14 row before starting "
                    "in manual mode.",
                )
                self.statusBar().showMessage(
                    "No channels enabled in manual mode."
                )
                return
        else:
            channels = self._chan_panel.selected_channels()
            if not channels:
                QMessageBox.warning(
                    self,
                    "No Channels",
                    "Select at least one channel before starting.",
                )
                return
            # Mirror TechniqueConfig.__post_init__ defaulting so the
            # GUI side intent is explicit and visible.
            if mode == "external":
                re_ce_channels = [EXTERNAL_RE_CE_CHANNEL] * len(channels)
            else:  # on_board
                re_ce_channels = [ON_BOARD_RE_CE_CHANNEL] * len(channels)

        # Build auto-save config if enabled. Reset first so a stale True
        # from a previous run that ended in error can't mislabel this run.
        auto_save = None
        self._auto_save_active = False
        if self._meas_panel.is_auto_save_enabled():
            auto_dir = self._meas_panel.auto_save_directory()
            if not auto_dir:
                auto_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "exports",
                )
                auto_dir = os.path.normpath(auto_dir)
            auto_save = AutoSaveConfig(
                enabled=True, output_dir=auto_dir
            )
            self._auto_save_active = True

        try:
            config = TechniqueConfig(
                technique=technique,
                params=params,
                channels=channels,
                auto_save=auto_save,
                continuous=False,
                re_ce_channels=re_ce_channels,
                electrode_config_mode=mode,
            )
        except ValueError as exc:
            QMessageBox.warning(
                self,
                "Invalid Electrode Configuration",
                str(exc),
            )
            self.statusBar().showMessage(
                f"Invalid electrode config: {exc}"
            )
            return

        # Prepare the plot
        self._plot_container.clear_plot()
        self._plot_container.set_technique(technique)

        try:
            self._engine.start_measurement(self._connection, config)
        except RuntimeError as exc:
            QMessageBox.warning(
                self, "Engine Busy", str(exc)
            )

    @pyqtSlot()
    def _on_stop_measurement(self) -> None:
        """Abort the running measurement."""
        self._engine.abort()

    @pyqtSlot()
    def _on_halt_measurement(self) -> None:
        """Pause the running measurement."""
        self._engine.halt()
        self._meas_panel.set_halted()
        self._status_progress.setText("Halted")

    @pyqtSlot()
    def _on_resume_measurement(self) -> None:
        """Resume a halted measurement."""
        self._engine.resume()
        self._meas_panel.set_running()
        self._status_progress.setText("Running")

    @pyqtSlot(str)
    def _on_measurement_started(self, technique: str) -> None:
        """Handle measurement_started signal from engine."""
        # Prevent Export from silently writing the prior run's data
        # if the user invokes it before this run completes.
        self._last_result = None
        self._export_action.setEnabled(False)
        self._meas_panel.set_running()
        self._status_progress.setText(
            f"Running: {technique.upper()}"
        )
        self.statusBar().showMessage(
            f"Measurement started: {technique.upper()}"
        )
        logger.info("Measurement started: %s", technique)

    @pyqtSlot(object)
    def _on_measurement_finished(
        self, result: MeasurementResult
    ) -> None:
        """Handle measurement_finished signal from engine.

        Stores the result, updates UI to idle, and prompts for export.
        """
        self._last_result = result
        self._meas_panel.set_idle()
        self._export_action.setEnabled(True)
        self._status_progress.setText("Idle")
        self._plot_container.on_measurement_finished()

        n_points = result.num_points
        n_channels = len(result.measured_channels)
        self.statusBar().showMessage(
            f"Measurement complete: {n_points} points "
            f"across {n_channels} channel(s)"
        )
        logger.info(
            "Measurement complete: %d points, %d channel(s).",
            n_points,
            n_channels,
        )

        # If auto-save was active, data is already on disk
        if self._auto_save_active:
            self._auto_save_active = False
            QMessageBox.information(
                self,
                "Auto-Save Complete",
                f"Data auto-saved during measurement.\n"
                f"{n_points} points across {n_channels} channel(s).\n\n"
                "Use File > Export for additional formats.",
            )
        else:
            self._prompt_export(result)

    @pyqtSlot(str)
    def _on_measurement_error(self, message: str) -> None:
        """Handle measurement_error signal from engine."""
        self._meas_panel.set_idle()
        # Clear the auto-save flag: a run that ended in error (including a
        # user abort, which routes through measurement_error) must not
        # leave the flag set, or the next finished run would be mislabeled
        # as auto-saved.
        self._auto_save_active = False
        self._status_progress.setText("Error")
        self.statusBar().showMessage(f"Error: {message}")
        logger.error("Measurement error: %s", message)

        # Enable export if we collected any data (e.g. abort mid-EIS)
        if (
            self._engine.result is not None
            and self._engine.result.num_points > 0
        ):
            self._last_result = self._engine.result
            self._export_action.setEnabled(True)

        if "aborted" not in message.lower():
            QMessageBox.critical(
                self, "Measurement Error", message
            )

    @pyqtSlot(int)
    def _on_channel_changed(self, channel: int) -> None:
        """Update status bar with the current MUX channel."""
        self._status_channel.setText(f"CH: {channel}")

    @pyqtSlot(str)
    def _on_electrode_mode_changed(self, mode: str) -> None:
        """Swap which channel panel is visible based on wiring mode.

        Modes ``external`` and ``on_board`` use the 4x4 grid panel
        because all 16 WE channels are valid.  Mode ``manual``
        switches to the 14-row pairing table because CH15+CH16 are
        infrastructure-reserved.
        """
        if mode == "manual":
            self._chan_panel.setVisible(False)
            self._manual_channel_panel.setVisible(True)
        else:
            self._chan_panel.setVisible(True)
            self._manual_channel_panel.setVisible(False)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _prompt_export(self, result: MeasurementResult) -> None:
        """Ask the user whether to export results after measurement.

        Args:
            result: The completed measurement result.
        """
        reply = QMessageBox.question(
            self,
            "Export Results",
            f"Measurement complete ({result.num_points} points).\n"
            "Export results to CSV and .pssession?",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._do_export(result)

    @pyqtSlot()
    def _on_export(self) -> None:
        """Handle File > Export menu action."""
        if self._last_result is not None:
            self._do_export(self._last_result)
        else:
            QMessageBox.information(
                self,
                "No Data",
                "No measurement results to export.",
            )

    def _do_export(self, result: MeasurementResult) -> None:
        """Export measurement results to a user-chosen directory.

        Creates a timestamped subdirectory under the chosen path and
        writes per-channel CSV files. If the exporters module is
        available, also writes a .pssession file.

        Args:
            result: The measurement result to export.
        """
        default_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
            "exports",
        )
        default_dir = os.path.normpath(default_dir)

        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Export Directory",
            default_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not directory:
            return

        # Create timestamped subdirectory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        technique = result.technique or "unknown"
        export_dir = os.path.join(
            directory, f"{timestamp}_{technique}"
        )
        os.makedirs(export_dir, exist_ok=True)

        try:
            self._write_csv_files(result, export_dir)

            # Write .pssession file alongside the CSVs
            pssession_path = os.path.join(
                export_dir, f"{technique}.pssession"
            )
            ps_exporter = PsSessionExporter()
            ps_exporter.export_pssession(result, pssession_path)

            self.statusBar().showMessage(
                f"Results exported to {export_dir}"
            )
            logger.info("Results exported to %s", export_dir)
        except Exception as exc:
            logger.error("Export failed: %s", exc)
            QMessageBox.critical(
                self, "Export Error", f"Export failed: {exc}"
            )

    def _write_csv_files(
        self, result: MeasurementResult, export_dir: str
    ) -> None:
        """Write per-channel CSV files for the measurement result.

        If the ``src.data.exporters`` module is available, delegates
        to ``CSVExporter``.  Otherwise, performs a basic CSV write.

        Args:
            result: The measurement result.
            export_dir: Directory to write files into.
        """
        try:
            from src.data.exporters import CSVExporter

            exporter = CSVExporter()
            exporter.export(result, export_dir)
            return
        except ImportError:
            logger.debug(
                "Exporters module not available; "
                "falling back to basic CSV export."
            )

        # Fallback: basic CSV export per channel
        for ch in result.measured_channels:
            ch_data = result.channel_data(ch)
            if not ch_data.data_points:
                continue
            filepath = os.path.join(
                export_dir, f"ch{ch:02d}.csv"
            )
            # Collect all variable names across data points
            all_vars: set[str] = set()
            for dp in ch_data.data_points:
                all_vars.update(dp.variables.keys())
            var_names = sorted(all_vars)
            with open(filepath, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp"] + var_names)
                for dp in ch_data.data_points:
                    row = [dp.timestamp]
                    for vn in var_names:
                        row.append(dp.variables.get(vn, ""))
                    writer.writerow(row)

    # ------------------------------------------------------------------
    # Presets and auto-save
    # ------------------------------------------------------------------

    def _load_presets_into_ui(self) -> None:
        """Populate the technique panel preset combo box."""
        presets = {
            k: p.name
            for k, p in self._preset_mgr.get_all().items()
        }
        deletable = {
            k for k in presets if not self._preset_mgr.is_builtin(k)
        }
        self._tech_panel.refresh_presets(presets, deletable=deletable)

    @pyqtSlot()
    def _on_save_preset(self) -> None:
        """Prompt the user for a name and save current settings."""
        name, ok = QInputDialog.getText(
            self,
            "Save Preset",
            "Preset name:",
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        # Derive a key from the name (lowercase, underscores)
        key = name.lower().replace(" ", "_")

        technique = self._tech_panel.selected_technique()
        params = self._tech_panel.get_params()
        channels = self._chan_panel.selected_channels()
        auto_save = self._meas_panel.is_auto_save_enabled()

        preset = Preset(
            name=name,
            technique=technique,
            params=params,
            channels=channels,
            auto_save=auto_save,
            description=f"User preset: {name}",
        )
        self._preset_mgr.add_preset(key, preset)

        # Refresh the preset combo box
        self._load_presets_into_ui()

        self.statusBar().showMessage(f"Preset saved: {name}")
        logger.info("Saved preset: %s (key=%s)", name, key)

    @pyqtSlot(str)
    def _on_delete_preset(self, key: str) -> None:
        """Confirm and delete a user preset."""
        preset = self._preset_mgr.get_preset(key)
        if preset is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Preset",
            f'Delete preset "{preset.name}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._preset_mgr.delete_preset(key):
            self._load_presets_into_ui()
            self.statusBar().showMessage(
                f"Deleted preset: {preset.name}"
            )
            logger.info(
                "Deleted preset: %s (key=%s)", preset.name, key
            )
        else:
            QMessageBox.warning(
                self,
                "Delete Failed",
                f"Could not delete '{preset.name}' "
                "(built-in presets cannot be removed).",
            )

    @pyqtSlot(str)
    def _on_preset_selected(self, key: str) -> None:
        """Load a preset into the technique, channel, and auto-save panels."""
        preset = self._preset_mgr.get_preset(key)
        if preset is None:
            return

        self._tech_panel.set_technique(preset.technique)
        self._tech_panel.set_params(preset.params)
        self._chan_panel.set_channels(preset.channels)

        default_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "exports",
        )
        default_dir = os.path.normpath(default_dir)
        self._meas_panel.set_auto_save(
            preset.auto_save, default_dir
        )

        self.statusBar().showMessage(
            f"Loaded preset: {preset.name}"
        )
        logger.info("Loaded preset: %s", preset.name)

    @pyqtSlot(str)
    def _on_auto_save_completed(self, output_dir: str) -> None:
        """Update status bar when auto-save writes files."""
        dirname = os.path.basename(output_dir)
        self.statusBar().showMessage(
            f"Auto-saved to: {dirname}"
        )

    # ------------------------------------------------------------------
    # UI state management
    # ------------------------------------------------------------------

    def _update_ui_connected(self) -> None:
        """Update UI elements to reflect a connected state."""
        self._connect_action.setEnabled(False)
        self._disconnect_action.setEnabled(True)
        self._meas_panel.set_idle()
        self._status_conn.setText("Connected")

    def _update_ui_disconnected(self) -> None:
        """Update UI elements to reflect a disconnected state."""
        self._connect_action.setEnabled(True)
        self._disconnect_action.setEnabled(False)
        self._meas_panel.set_disabled()
        self._status_conn.setText("Disconnected")
        self._status_channel.setText("CH: --")
        self._status_progress.setText("Idle")

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _on_about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br><br>"
            "Python GUI for the PalmSens EmStat Pico with MUX16 "
            "multiplexer.<br><br>"
            "Supports 18 electrochemical techniques with real-time "
            "multi-channel plotting and CSV/.pssession export."
            "<br><br>"
            "Built with PyQt6 and pyqtgraph.",
        )

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        """Ensure clean shutdown on window close."""
        if self._engine.isRunning():
            self._engine.abort()
            self._engine.wait(3000)
        # Let an in-flight connect handshake finish so its thread isn't
        # destroyed while running.
        if (
            self._connect_worker is not None
            and self._connect_worker.isRunning()
        ):
            self._connect_worker.wait(6000)
        if self._connection.is_connected:
            self._connection.disconnect()
        # Detach the log handler so it stops emitting into this widget
        # once the window is gone (prevents leak / use-after-free).
        if getattr(self, "_log_handler", None) is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None
        event.accept()


# ------------------------------------------------------------------
# Application entry point
# ------------------------------------------------------------------


def main() -> None:
    """Launch the EmStat Pico MUX16 Controller application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import traceback

    def exception_hook(exc_type, exc_value, exc_tb):
        """Print unhandled exceptions instead of silently crashing."""
        traceback.print_exception(exc_type, exc_value, exc_tb)
        logging.error(
            "Unhandled exception: %s", exc_value, exc_info=True
        )

    sys.excepthook = exception_hook

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
