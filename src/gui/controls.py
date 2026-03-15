"""GUI control panels for connection, technique, channels, and measurement.

Provides four panel widgets that compose the left-hand sidebar of the
main application window:

- ``ConnectionPanel`` -- COM port selection and device connection
- ``TechniquePanel`` -- Technique selection with dynamic parameter fields
- ``ChannelPanel`` -- 4x4 grid of MUX channel checkboxes
- ``MeasurementControlPanel`` -- Start / Stop / Halt / Resume buttons

All panels emit signals when user actions occur; the main window wires
these to the measurement engine and connection manager.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.techniques.scripts import supported_techniques, technique_params

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Human-readable display names for techniques.
_TECHNIQUE_DISPLAY: dict[str, str] = {
    "lsv": "LSV - Linear Sweep Voltammetry",
    "dpv": "DPV - Differential Pulse Voltammetry",
    "swv": "SWV - Square Wave Voltammetry",
    "npv": "NPV - Normal Pulse Voltammetry",
    "acv": "ACV - AC Voltammetry",
    "cv": "CV - Cyclic Voltammetry",
    "ca": "CA - Chronoamperometry",
    "fca": "FCA - Fixed-potential Chronoamperometry",
    "cp": "CP - Chronopotentiometry",
    "ocp": "OCP - Open Circuit Potential",
    "eis": "EIS - Electrochemical Impedance",
    "geis": "GEIS - Galvanostatic EIS",
    "pad": "PAD - Pulsed Amperometric Detection",
    "lsp": "LSP - Linear Sweep Potentiometry",
    "fcv": "FCV - Fast Cyclic Voltammetry",
    "ca_alt_mux": "CA (MUX-alt) - Chronoamperometry",
    "cp_alt_mux": "CP (MUX-alt) - Chronopotentiometry",
    "ocp_alt_mux": "OCP (MUX-alt) - Open Circuit Potential",
}

# Parameter metadata for building spin boxes.
# Maps parameter name -> (label, suffix/unit, decimals, min, max, step).
_PARAM_META: dict[str, tuple[str, str, int, float, float, float]] = {
    "e_begin": ("E begin", "V", 3, -2.0, 2.0, 0.01),
    "e_end": ("E end", "V", 3, -2.0, 2.0, 0.01),
    "e_step": ("E step", "V", 4, 0.0001, 0.5, 0.001),
    "e_vertex1": ("E vertex 1", "V", 3, -2.0, 2.0, 0.01),
    "e_vertex2": ("E vertex 2", "V", 3, -2.0, 2.0, 0.01),
    "e_pulse": ("E pulse", "V", 3, 0.001, 1.0, 0.005),
    "e_dc": ("E DC", "V", 3, -2.0, 2.0, 0.01),
    "e_ac": ("E AC", "V", 4, 0.0001, 0.5, 0.001),
    "e_cond": ("E conditioning", "V", 3, -2.0, 2.0, 0.01),
    "e_dep": ("E deposition", "V", 3, -2.0, 2.0, 0.01),
    "e_eq": ("E equilibration", "V", 3, -2.0, 2.0, 0.01),
    "scan_rate": ("Scan rate", "V/s", 3, 0.001, 10.0, 0.01),
    "amplitude": ("Amplitude", "V", 4, 0.0001, 0.5, 0.001),
    "frequency": ("Frequency", "Hz", 1, 0.1, 100000.0, 1.0),
    "t_pulse": ("t pulse", "s", 3, 0.001, 10.0, 0.005),
    "t_base": ("t base", "s", 3, 0.01, 60.0, 0.1),
    "t_run": ("t run", "s", 1, 0.1, 36000.0, 1.0),
    "t_interval": ("t interval", "s", 3, 0.001, 60.0, 0.1),
    "t_cond": ("t conditioning", "s", 2, 0.01, 60.0, 0.1),
    "t_dep": ("t deposition", "s", 1, 0.1, 3600.0, 1.0),
    "t_eq": ("t equilibration", "s", 2, 0.01, 60.0, 0.1),
    "i_dc": ("I DC", "A", 6, -0.1, 0.1, 0.00001),
    "i_ac": ("I AC", "A", 6, 0.0, 0.1, 0.00001),
    "freq_start": ("Freq start", "Hz", 1, 0.01, 1000000.0, 100.0),
    "freq_end": ("Freq end", "Hz", 4, 0.0001, 100000.0, 0.1),
    "n_freq": ("Num frequencies", "", 0, 1.0, 1000.0, 1.0),
    "n_scans": ("Num scans", "", 0, 1.0, 100.0, 1.0),
    "cr": ("Current range", "", 0, 0.0, 0.0, 0.0),  # special
}

# Current range options for the dropdown.
_CR_OPTIONS: list[tuple[str, str]] = [
    ("100n", "100 nA"),
    ("2u", "2 uA"),
    ("4u", "4 uA"),
    ("8u", "8 uA"),
    ("16u", "16 uA"),
    ("32u", "32 uA"),
    ("63u", "63 uA"),
    ("100u", "100 uA"),
    ("1m", "1 mA"),
    ("10m", "10 mA"),
    ("100m", "100 mA"),
]


def _detect_serial_ports() -> list[str]:
    """Return a list of available serial port names.

    Uses ``serial.tools.list_ports`` from pyserial to detect attached
    ports.  Returns an empty list if detection fails.
    """
    try:
        from serial.tools.list_ports import comports

        return [p.device for p in sorted(comports())]
    except Exception:
        logger.debug("Serial port detection failed.", exc_info=True)
        return []


# =====================================================================
# ConnectionPanel
# =====================================================================


class ConnectionPanel(QGroupBox):
    """Panel for serial port selection and device connection.

    Provides a COM port dropdown (auto-populated), connect/disconnect
    buttons, a firmware version label, and a colour-coded status
    indicator.

    Signals:
        connect_requested(str): Emitted with the selected port name
            when the user clicks Connect.
        disconnect_requested(): Emitted when the user clicks
            Disconnect.
    """

    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Connection", parent)
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the panel layout."""
        layout = QVBoxLayout(self)

        # Port selection row
        port_row = QHBoxLayout()
        port_label = QLabel("Port:")
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(120)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Rescan serial ports")
        self._refresh_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(port_label)
        port_row.addWidget(self._port_combo, stretch=1)
        port_row.addWidget(self._refresh_btn)
        layout.addLayout(port_row)

        # Connect / disconnect buttons
        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("Connect")
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self._connect_btn)
        btn_row.addWidget(self._disconnect_btn)
        layout.addLayout(btn_row)

        # Status indicator
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._status_label = QLabel("Disconnected")
        self._status_label.setStyleSheet("color: red; font-weight: bold;")
        status_row.addWidget(self._status_label, stretch=1)
        layout.addLayout(status_row)

        # Firmware version
        fw_row = QHBoxLayout()
        fw_row.addWidget(QLabel("Firmware:"))
        self._firmware_label = QLabel("N/A")
        fw_row.addWidget(self._firmware_label, stretch=1)
        layout.addLayout(fw_row)

        # Initial port scan
        self.refresh_ports()

    # -- Public API -----------------------------------------------------

    def refresh_ports(self) -> None:
        """Rescan available serial ports and update the dropdown."""
        current = self._port_combo.currentText()
        self._port_combo.clear()
        ports = _detect_serial_ports()
        self._port_combo.addItems(ports)
        # Restore previous selection if still available
        idx = self._port_combo.findText(current)
        if idx >= 0:
            self._port_combo.setCurrentIndex(idx)

    def set_connected(self, firmware: str = "") -> None:
        """Update UI to reflect a connected state.

        Args:
            firmware: Firmware version string to display.
        """
        self._status_label.setText("Connected")
        self._status_label.setStyleSheet(
            "color: green; font-weight: bold;"
        )
        self._firmware_label.setText(firmware or "N/A")
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._port_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)

    def set_disconnected(self) -> None:
        """Update UI to reflect a disconnected state."""
        self._status_label.setText("Disconnected")
        self._status_label.setStyleSheet(
            "color: red; font-weight: bold;"
        )
        self._firmware_label.setText("N/A")
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._port_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)

    def selected_port(self) -> str:
        """Return the currently selected port name."""
        return self._port_combo.currentText()

    # -- Internal -------------------------------------------------------

    def _on_connect(self) -> None:
        """Handle Connect button click."""
        port = self._port_combo.currentText()
        if port:
            self.connect_requested.emit(port)
        else:
            logger.warning("No serial port selected.")

    def _on_disconnect(self) -> None:
        """Handle Disconnect button click."""
        self.disconnect_requested.emit()


# =====================================================================
# TechniquePanel
# =====================================================================


class TechniquePanel(QGroupBox):
    """Panel for technique selection and dynamic parameter editing.

    Populates the technique dropdown from ``supported_techniques()`` and
    dynamically rebuilds parameter fields when the technique changes,
    using ``technique_params()`` for default values.

    Signals:
        technique_changed(str): Emitted with the technique key when
            the user selects a different technique.
        params_changed(): Emitted whenever any parameter value changes.
    """

    technique_changed = pyqtSignal(str)
    params_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Technique", parent)
        self._param_widgets: dict[str, QWidget] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the panel layout."""
        layout = QVBoxLayout(self)

        # Technique dropdown
        tech_row = QHBoxLayout()
        tech_row.addWidget(QLabel("Technique:"))
        self._technique_combo = QComboBox()
        techniques = supported_techniques()
        for tech in techniques:
            display = _TECHNIQUE_DISPLAY.get(tech, tech.upper())
            self._technique_combo.addItem(display, userData=tech)
        tech_row.addWidget(self._technique_combo, stretch=1)
        layout.addLayout(tech_row)

        # Parameter form (dynamically populated)
        self._param_form = QFormLayout()
        self._param_container = QWidget()
        self._param_container.setLayout(self._param_form)
        layout.addWidget(self._param_container)

        # Connect technique change
        self._technique_combo.currentIndexChanged.connect(
            self._on_technique_changed
        )

        # Build initial parameter fields
        if techniques:
            self._rebuild_params(techniques[0])

    # -- Public API -----------------------------------------------------

    def selected_technique(self) -> str:
        """Return the currently selected technique key."""
        return self._technique_combo.currentData() or ""

    def current_params(self) -> dict[str, Any]:
        """Return the current parameter values as a dict.

        Returns:
            Dictionary mapping parameter names to their current values.
            The ``cr`` key is a string (current range code); all others
            are numeric.
        """
        params: dict[str, Any] = {}
        for name, widget in self._param_widgets.items():
            if isinstance(widget, QComboBox):
                params[name] = widget.currentData()
            elif isinstance(widget, QSpinBox):
                params[name] = widget.value()
            elif isinstance(widget, QDoubleSpinBox):
                params[name] = widget.value()
        return params

    # -- Internal -------------------------------------------------------

    def _on_technique_changed(self, _index: int) -> None:
        """Handle technique dropdown change."""
        tech = self.selected_technique()
        if tech:
            self._rebuild_params(tech)
            self.technique_changed.emit(tech)

    def _rebuild_params(self, technique: str) -> None:
        """Clear and rebuild parameter fields for the given technique.

        Args:
            technique: Technique identifier.
        """
        # Clear existing widgets
        self._param_widgets.clear()
        while self._param_form.rowCount() > 0:
            self._param_form.removeRow(0)

        # Get defaults for this technique
        try:
            defaults = technique_params(technique)
        except ValueError:
            logger.warning(
                "No defaults for technique %r", technique
            )
            return

        # Build a field for each parameter
        for param_name, default_value in defaults.items():
            self._add_param_field(param_name, default_value)

    def _add_param_field(
        self, name: str, default_value: Any
    ) -> None:
        """Add a single parameter field to the form.

        Args:
            name: Parameter name (e.g., 'e_begin', 'cr').
            default_value: Default value for the field.
        """
        meta = _PARAM_META.get(name)

        if name == "cr":
            # Current range: use a combo box
            combo = QComboBox()
            for code, display in _CR_OPTIONS:
                combo.addItem(display, userData=code)
            # Set default selection
            default_str = str(default_value)
            for i, (code, _) in enumerate(_CR_OPTIONS):
                if code == default_str:
                    combo.setCurrentIndex(i)
                    break
            combo.currentIndexChanged.connect(
                lambda: self.params_changed.emit()
            )
            label = meta[0] if meta else name
            self._param_form.addRow(label + ":", combo)
            self._param_widgets[name] = combo
            return

        if meta is not None:
            label, suffix, decimals, min_val, max_val, step = meta
        else:
            # Fallback for unknown parameters
            label = name
            suffix = ""
            decimals = 6
            min_val = -1e9
            max_val = 1e9
            step = 0.001

        if isinstance(default_value, int) and decimals == 0:
            # Integer parameter
            spin = QSpinBox()
            spin.setRange(int(min_val), int(max_val))
            spin.setSingleStep(int(step) if step >= 1 else 1)
            spin.setValue(int(default_value))
            if suffix:
                spin.setSuffix(f" {suffix}")
            spin.valueChanged.connect(
                lambda: self.params_changed.emit()
            )
            self._param_form.addRow(label + ":", spin)
            self._param_widgets[name] = spin
        else:
            # Float parameter
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
            spin.setRange(min_val, max_val)
            spin.setSingleStep(step)
            spin.setValue(float(default_value))
            if suffix:
                spin.setSuffix(f" {suffix}")
            spin.valueChanged.connect(
                lambda: self.params_changed.emit()
            )
            self._param_form.addRow(label + ":", spin)
            self._param_widgets[name] = spin


# =====================================================================
# ChannelPanel
# =====================================================================


class ChannelPanel(QGroupBox):
    """Panel with a 4x4 grid of channel checkboxes (1-16).

    Provides Select All / Select None buttons for quick bulk selection.

    Signals:
        channels_changed(list): Emitted with the list of selected
            1-indexed channel numbers whenever the selection changes.
    """

    channels_changed = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Channels", parent)
        self._checkboxes: list[QCheckBox] = []
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the panel layout."""
        layout = QVBoxLayout(self)

        # 4x4 grid of channel checkboxes
        grid = QGridLayout()
        for i in range(16):
            row = i // 4
            col = i % 4
            ch_num = i + 1
            cb = QCheckBox(f"CH{ch_num}")
            cb.setChecked(ch_num == 1)  # Default: CH1 selected
            cb.stateChanged.connect(self._on_checkbox_changed)
            grid.addWidget(cb, row, col)
            self._checkboxes.append(cb)
        layout.addLayout(grid)

        # Select All / Select None buttons
        btn_row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select All")
        self._select_none_btn = QPushButton("Select None")
        self._select_all_btn.clicked.connect(self._select_all)
        self._select_none_btn.clicked.connect(self._select_none)
        btn_row.addWidget(self._select_all_btn)
        btn_row.addWidget(self._select_none_btn)
        layout.addLayout(btn_row)

    # -- Public API -----------------------------------------------------

    def selected_channels(self) -> list[int]:
        """Return sorted list of selected 1-indexed channel numbers."""
        return sorted(
            i + 1
            for i, cb in enumerate(self._checkboxes)
            if cb.isChecked()
        )

    def set_selected_channels(self, channels: list[int]) -> None:
        """Programmatically set which channels are selected.

        Args:
            channels: List of 1-indexed channel numbers to select.
                All others will be deselected.
        """
        channel_set = set(channels)
        for i, cb in enumerate(self._checkboxes):
            cb.blockSignals(True)
            cb.setChecked((i + 1) in channel_set)
            cb.blockSignals(False)
        self.channels_changed.emit(self.selected_channels())

    # -- Internal -------------------------------------------------------

    def _on_checkbox_changed(self, _state: int) -> None:
        """Handle any channel checkbox state change."""
        self.channels_changed.emit(self.selected_channels())

    def _select_all(self) -> None:
        """Check all 16 channel checkboxes."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self.channels_changed.emit(self.selected_channels())

    def _select_none(self) -> None:
        """Uncheck all 16 channel checkboxes."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self.channels_changed.emit(self.selected_channels())


# =====================================================================
# MeasurementControlPanel
# =====================================================================


class MeasurementControlPanel(QGroupBox):
    """Panel with Start, Stop, Halt, and Resume buttons.

    Manages button enable/disable states based on measurement lifecycle:

    - **Idle:** Start enabled, others disabled.
    - **Running:** Stop and Halt enabled, Start and Resume disabled.
    - **Halted:** Resume and Stop enabled, Start and Halt disabled.

    Signals:
        start_requested(): Emitted when Start is clicked.
        stop_requested(): Emitted when Stop (abort) is clicked.
        halt_requested(): Emitted when Halt is clicked.
        resume_requested(): Emitted when Resume is clicked.
    """

    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    halt_requested = pyqtSignal()
    resume_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Measurement", parent)
        self._build_ui()
        self.set_idle()

    def _build_ui(self) -> None:
        """Construct the panel layout."""
        layout = QVBoxLayout(self)

        # First row: Start and Stop
        row1 = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._stop_btn = QPushButton("Stop")
        self._start_btn.clicked.connect(self.start_requested.emit)
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        row1.addWidget(self._start_btn)
        row1.addWidget(self._stop_btn)
        layout.addLayout(row1)

        # Second row: Halt and Resume
        row2 = QHBoxLayout()
        self._halt_btn = QPushButton("Halt")
        self._resume_btn = QPushButton("Resume")
        self._halt_btn.clicked.connect(self.halt_requested.emit)
        self._resume_btn.clicked.connect(self.resume_requested.emit)
        row2.addWidget(self._halt_btn)
        row2.addWidget(self._resume_btn)
        layout.addLayout(row2)

    # -- Public API: state management -----------------------------------

    def set_idle(self) -> None:
        """Set button states for idle (no measurement running)."""
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._halt_btn.setEnabled(False)
        self._resume_btn.setEnabled(False)

    def set_running(self) -> None:
        """Set button states for an active measurement."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._halt_btn.setEnabled(True)
        self._resume_btn.setEnabled(False)

    def set_halted(self) -> None:
        """Set button states for a halted (paused) measurement."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._halt_btn.setEnabled(False)
        self._resume_btn.setEnabled(True)
