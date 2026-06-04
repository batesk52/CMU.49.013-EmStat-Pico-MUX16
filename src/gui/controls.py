"""GUI control panels for connection, technique, channels, and measurement.

Provides six panel widgets that compose together in the main window:

- ``ConnectionPanel`` -- COM port selection, connect/disconnect, status
- ``TechniquePanel`` -- technique dropdown with dynamic parameter fields
- ``ElectrodeConfigPanel`` -- 3-way radio selector for wiring mode
  (external CH15, on-board CH16, or manual per-WE pairing in CH1-CH14)
- ``ChannelPanel`` -- 4x4 grid of channel checkboxes (CH1-CH16),
  used in external + on-board modes
- ``ManualChannelPanel`` -- 14-row per-WE table with RE/CE pairing,
  used in manual mode only
- ``MeasurementControlPanel`` -- Start, Stop, Halt, Resume buttons

All panels emit signals for state changes. No panel performs serial I/O
directly; the main window wires panel signals to the engine and connection.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from src.data.models import (
    EXTERNAL_RE_CE_CHANNEL,
    MODE_C_MAX_CHANNEL,
    ON_BOARD_RE_CE_CHANNEL,
)
from src.techniques.scripts import supported_techniques, technique_params

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# Friendly technique display names
# -----------------------------------------------------------------------

_TECHNIQUE_LABELS: dict[str, str] = {
    "cv": "Cyclic Voltammetry (CV)",
    "lsv": "Linear Sweep Voltammetry (LSV)",
    "dpv": "Differential Pulse Voltammetry (DPV)",
    "swv": "Square Wave Voltammetry (SWV)",
    "npv": "Normal Pulse Voltammetry (NPV)",
    "acv": "AC Voltammetry (ACV)",
    "ca": "Chronoamperometry (CA)",
    "fca": "Fixed-Potential CA (FCA)",
    "cp": "Chronopotentiometry (CP)",
    "ocp": "Open Circuit Potential (OCP)",
    "eis": "EIS (Potentiostatic)",
    "geis": "EIS (Galvanostatic)",
    "pad": "Preconcentration + DPV (PAD)",
    "lsp": "Linear Sweep Potentiometry (LSP)",
    "fcv": "Fast Cyclic Voltammetry (FCV)",
    "ca_alt_mux": "CA (MUX-Alternating)",
    "cp_alt_mux": "CP (MUX-Alternating)",
    "ocp_alt_mux": "OCP (MUX-Alternating)",
}

# Parameter display names and units for spin box labels
_PARAM_LABELS: dict[str, tuple[str, str]] = {
    "e_begin": ("E begin", "V"),
    "e_end": ("E end", "V"),
    "e_step": ("E step", "V"),
    "e_vertex1": ("E vertex 1", "V"),
    "e_vertex2": ("E vertex 2", "V"),
    "e_pulse": ("E pulse", "V"),
    "e_dc": ("E DC", "V"),
    "e_ac": ("E AC", "V"),
    "e_cond": ("E conditioning", "V"),
    "e_dep": ("E deposition", "V"),
    "e_eq": ("E equilibration", "V"),
    "scan_rate": ("Scan rate", "V/s"),
    "t_pulse": ("t pulse", "s"),
    "t_run": ("t run", "s"),
    "t_interval": ("t interval", "s"),
    "t_base": ("t base", "s"),
    "t_cond": ("t conditioning", "s"),
    "t_dep": ("t deposition", "s"),
    "t_eq": ("t equilibration", "s"),
    "amplitude": ("Amplitude", "V"),
    "frequency": ("Frequency", "Hz"),
    "freq_start": ("Freq start", "Hz"),
    "freq_end": ("Freq end", "Hz"),
    "i_dc": ("I DC", "A"),
    "i_ac": ("I AC", "A"),
    "n_scans": ("# Scans", ""),
    "n_freq": ("# Frequencies", ""),
    "settle_time": ("Settle time", "s"),
    "samples_per_visit": ("Samples per channel visit", ""),
    "cr": ("Current range", ""),
    "bw_hz": ("Max Bandwidth", "Hz"),
}

# Current range options for combo box
_CURRENT_RANGES = [
    "100n", "2u", "4u", "8u", "16u",
    "32u", "63u", "100u", "1m", "10m", "100m",
]

# Max bandwidth options for combo box (Hz).
# Mode-2 sweep range; default 400 preserves legacy behavior.
_BANDWIDTH_HZ = [0.4, 4, 40, 400, 4000, 40000, 200000]


# =======================================================================
# ConnectionPanel
# =======================================================================


class ConnectionPanel(QGroupBox):
    """COM port selection, connect/disconnect, and status display.

    Signals:
        connect_requested(str): Emitted with the selected port name
            when the user clicks Connect.
        disconnect_requested(): Emitted when the user clicks Disconnect.
    """

    connect_requested = pyqtSignal(str)
    disconnect_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Connection", parent)
        self._connected = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the panel layout."""
        layout = QVBoxLayout(self)

        # Port selection row
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.setMinimumWidth(120)
        port_row.addWidget(self._port_combo, 1)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setToolTip("Scan for available COM ports")
        self._refresh_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self._refresh_btn)
        layout.addLayout(port_row)

        # Connect / Disconnect buttons
        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        btn_row.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        btn_row.addWidget(self._disconnect_btn)
        layout.addLayout(btn_row)

        # Status indicator + firmware version
        status_row = QHBoxLayout()
        self._status_label = QLabel("Disconnected")
        self._status_label.setStyleSheet("color: #888; font-weight: bold;")
        status_row.addWidget(self._status_label)
        layout.addLayout(status_row)

        self._firmware_label = QLabel("Firmware: —")
        self._firmware_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._firmware_label)

        # Initial port scan
        self.refresh_ports()

    # ---- Public API ------------------------------------------------------

    def refresh_ports(self) -> None:
        """Scan system for available serial ports and update combo box."""
        self._port_combo.clear()
        try:
            from serial.tools.list_ports import comports

            ports = sorted(comports(), key=lambda p: p.device)
            for port_info in ports:
                label = f"{port_info.device}"
                if port_info.description and port_info.description != "n/a":
                    label += f" — {port_info.description}"
                self._port_combo.addItem(label, port_info.device)
        except ImportError:
            logger.warning("serial.tools.list_ports not available.")

    def selected_port(self) -> str:
        """Return the currently selected port device path.

        Returns:
            The port string (e.g., 'COM3' or '/dev/ttyUSB0').
            Falls back to the combo box text if no data is stored.
        """
        data = self._port_combo.currentData()
        if data:
            return str(data)
        return self._port_combo.currentText().split(" — ")[0].strip()

    def set_connected(self, firmware: str = "") -> None:
        """Update UI to reflect a connected state.

        Args:
            firmware: Firmware version string to display.
        """
        self._connected = True
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(True)
        self._port_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Connected")
        self._status_label.setStyleSheet(
            "color: #2ca02c; font-weight: bold;"
        )
        if firmware:
            self._firmware_label.setText(f"Firmware: {firmware}")

    def set_disconnected(self) -> None:
        """Update UI to reflect a disconnected state."""
        self._connected = False
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)
        self._port_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._status_label.setText("Disconnected")
        self._status_label.setStyleSheet(
            "color: #888; font-weight: bold;"
        )
        self._firmware_label.setText("Firmware: —")

    def set_error(self, message: str) -> None:
        """Display an error state.

        Args:
            message: Short error description to show.
        """
        self._status_label.setText(f"Error: {message}")
        self._status_label.setStyleSheet(
            "color: #d62728; font-weight: bold;"
        )
        # Re-enable the controls so the user can retry after a failure.
        self._connect_btn.setEnabled(True)
        self._port_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)

    def set_connecting(self) -> None:
        """Show a busy state while the connect handshake runs off-thread.

        Disables both connect AND disconnect controls so the user can't
        fire a second connect, or a disconnect, while a handshake is in
        flight (which would race two operations on one connection).
        """
        self._connect_btn.setEnabled(False)
        self._disconnect_btn.setEnabled(False)
        self._port_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._status_label.setText("Connecting...")
        self._status_label.setStyleSheet(
            "color: #888; font-weight: bold;"
        )

    @property
    def is_connected(self) -> bool:
        """Whether the panel reflects a connected state."""
        return self._connected

    # ---- Private slots ---------------------------------------------------

    def _on_connect_clicked(self) -> None:
        """Emit connect_requested with the selected port."""
        port = self.selected_port()
        if port:
            self.connect_requested.emit(port)
        else:
            self.set_error("No port selected")

    def _on_disconnect_clicked(self) -> None:
        """Emit disconnect_requested."""
        self.disconnect_requested.emit()


# =======================================================================
# TechniquePanel
# =======================================================================


class TechniquePanel(QGroupBox):
    """Technique selection with dynamic parameter fields.

    Uses ``supported_techniques()`` to populate the dropdown and
    ``technique_params()`` to generate appropriate spin boxes for
    each technique's default parameters.

    Signals:
        technique_changed(str): Emitted with the technique key when
            the user selects a different technique.
        params_changed(): Emitted when any parameter value changes.
        save_preset_requested(): Emitted when the user clicks Save.
    """

    technique_changed = pyqtSignal(str)
    params_changed = pyqtSignal()
    preset_selected = pyqtSignal(str)  # preset key
    save_preset_requested = pyqtSignal()
    delete_preset_requested = pyqtSignal(str)  # preset key to delete

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Technique", parent)
        self._param_widgets: dict[str, QWidget] = {}
        self._deletable_keys: set[str] = set()
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the panel layout."""
        layout = QVBoxLayout(self)

        # Preset selector
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._preset_combo.addItem("(No Preset)", "")
        self._preset_combo.currentIndexChanged.connect(
            self._on_preset_selected
        )
        preset_row.addWidget(self._preset_combo, 1)

        # Icon-only buttons to keep the preset row compact; tooltips
        # carry the labels for discoverability.
        style = self.style()

        self._save_preset_btn = QPushButton()
        self._save_preset_btn.setIcon(
            style.standardIcon(
                QStyle.StandardPixmap.SP_DialogSaveButton
            )
        )
        self._save_preset_btn.setToolTip("Save current settings as preset")
        self._save_preset_btn.setFixedWidth(32)
        self._save_preset_btn.clicked.connect(
            self.save_preset_requested.emit
        )
        preset_row.addWidget(self._save_preset_btn)

        self._delete_preset_btn = QPushButton()
        self._delete_preset_btn.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        self._delete_preset_btn.setToolTip("Delete the selected preset")
        self._delete_preset_btn.setFixedWidth(32)
        self._delete_preset_btn.setEnabled(False)
        self._delete_preset_btn.clicked.connect(self._on_delete_clicked)
        preset_row.addWidget(self._delete_preset_btn)
        layout.addLayout(preset_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # Technique selector
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("Technique:"))
        self._technique_combo = QComboBox()
        self._technique_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        # Populate with supported techniques
        for tech_key in supported_techniques():
            label = _TECHNIQUE_LABELS.get(tech_key, tech_key.upper())
            self._technique_combo.addItem(label, tech_key)

        self._technique_combo.currentIndexChanged.connect(
            self._on_technique_selected
        )
        selector_row.addWidget(self._technique_combo, 1)
        layout.addLayout(selector_row)

        # Parameter area (flat — the outer Settings dock scrolls).
        self._param_container = QWidget()
        self._param_layout = QGridLayout(self._param_container)
        self._param_layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self._param_container)

        # Load params for the initial technique
        if self._technique_combo.count() > 0:
            self._load_params_for_technique(
                self._technique_combo.currentData()
            )

    # ---- Public API ------------------------------------------------------

    def selected_technique(self) -> str:
        """Return the currently selected technique key.

        Returns:
            Lowercase technique identifier (e.g., 'cv', 'dpv').
        """
        return self._technique_combo.currentData() or ""

    def get_params(self) -> dict[str, Any]:
        """Collect current parameter values from the spin boxes.

        Returns:
            Dict mapping parameter names to their current values.
        """
        params: dict[str, Any] = {}
        for name, widget in self._param_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                params[name] = widget.value()
            elif isinstance(widget, QSpinBox):
                params[name] = widget.value()
            elif isinstance(widget, QComboBox):
                # bw_hz stores numeric Hz value in itemData; recover
                # it as float/int so _format_si() formats it correctly.
                if name == "bw_hz":
                    data = widget.currentData()
                    if data is None:
                        # Fallback: try to parse the visible text
                        try:
                            data = float(widget.currentText())
                        except (TypeError, ValueError):
                            data = 400
                    params[name] = data
                else:
                    params[name] = widget.currentText()
        return params

    def set_technique(self, technique: str) -> None:
        """Programmatically select a technique.

        Args:
            technique: Technique key (e.g., 'cv').
        """
        for i in range(self._technique_combo.count()):
            if self._technique_combo.itemData(i) == technique.lower():
                self._technique_combo.setCurrentIndex(i)
                return

    def set_params(self, params: dict[str, Any]) -> None:
        """Programmatically set parameter values.

        Only sets values for parameters that exist in the current
        technique's widget set.

        Args:
            params: Dict mapping parameter names to values.
        """
        for name, value in params.items():
            widget = self._param_widgets.get(name)
            if widget is None:
                continue
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QComboBox):
                idx = widget.findText(str(value))
                if idx >= 0:
                    widget.setCurrentIndex(idx)

    def refresh_presets(
        self,
        presets: dict[str, str],
        deletable: Optional[set[str]] = None,
    ) -> None:
        """Reload the preset combo box.

        Args:
            presets: Dict mapping preset keys to display names.
            deletable: Keys the user is allowed to delete (built-ins
                excluded). If None, no preset is deletable.
        """
        self._deletable_keys = deletable or set()
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("(No Preset)", "")
        for key, name in sorted(presets.items()):
            self._preset_combo.addItem(name, key)
        self._preset_combo.blockSignals(False)
        # Selection resets to "(No Preset)" after a rebuild.
        self._delete_preset_btn.setEnabled(False)

    # ---- Internal --------------------------------------------------------

    def _on_preset_selected(self, index: int) -> None:
        """Handle preset combo box selection change."""
        key = self._preset_combo.itemData(index)
        self._delete_preset_btn.setEnabled(
            bool(key) and key in self._deletable_keys
        )
        if key:
            self.preset_selected.emit(key)

    def _on_delete_clicked(self) -> None:
        """Emit delete request for the currently selected preset."""
        key = self._preset_combo.currentData()
        if key:
            self.delete_preset_requested.emit(key)

    def _on_technique_selected(self, index: int) -> None:
        """Handle technique combo box selection change."""
        tech_key = self._technique_combo.itemData(index)
        if tech_key:
            self._load_params_for_technique(tech_key)
            self.technique_changed.emit(tech_key)

    def _load_params_for_technique(self, technique: str) -> None:
        """Rebuild parameter fields for the selected technique.

        Args:
            technique: Technique key.
        """
        # Clear existing parameter widgets
        self._param_widgets.clear()
        while self._param_layout.count():
            item = self._param_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # Get default params for this technique
        try:
            defaults = technique_params(technique)
        except ValueError:
            logger.warning("No params for technique: %s", technique)
            return

        # Create a row for each parameter
        row = 0
        for param_name, default_value in defaults.items():
            label_text, unit = _PARAM_LABELS.get(
                param_name, (param_name, "")
            )
            display = f"{label_text}"
            if unit:
                display += f" ({unit})"

            label = QLabel(display)
            self._param_layout.addWidget(label, row, 0)

            widget = self._create_param_widget(
                param_name, default_value
            )
            self._param_layout.addWidget(widget, row, 1)
            self._param_widgets[param_name] = widget
            row += 1

        # Add vertical spacer at the bottom
        self._param_layout.setRowStretch(row, 1)

    def _create_param_widget(
        self, name: str, default: Any
    ) -> QWidget:
        """Create an appropriate input widget for a parameter.

        Args:
            name: Parameter name.
            default: Default value.

        Returns:
            A QDoubleSpinBox, QSpinBox, or QComboBox.
        """
        if name == "cr":
            # Current range uses a combo box
            combo = QComboBox()
            for cr in _CURRENT_RANGES:
                combo.addItem(cr)
            # Set to default
            idx = combo.findText(str(default))
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda: self.params_changed.emit()
            )
            return combo

        if name == "bw_hz":
            # Max bandwidth uses a combo box of Hz values.
            # Each item stores the numeric Hz value as itemData so
            # callers can recover a float/int rather than a string.
            combo = QComboBox()
            for hz in _BANDWIDTH_HZ:
                # Use int label when value is integer, else float repr
                label = (
                    str(int(hz))
                    if float(hz).is_integer()
                    else str(hz)
                )
                combo.addItem(label, hz)
            # Default selection (matches scripts default bw_hz=400)
            try:
                default_hz = float(default)
            except (TypeError, ValueError):
                default_hz = 400.0
            idx = combo.findText(
                str(int(default_hz))
                if default_hz.is_integer()
                else str(default_hz)
            )
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda: self.params_changed.emit()
            )
            return combo

        if isinstance(default, int):
            spin = QSpinBox()
            spin.setRange(0, 10000)
            spin.setValue(default)
            spin.valueChanged.connect(
                lambda: self.params_changed.emit()
            )
            return spin

        # Float parameter
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-10.0, 1e8)
        spin.setSingleStep(_guess_step(default))
        spin.setValue(float(default))
        spin.valueChanged.connect(
            lambda: self.params_changed.emit()
        )
        return spin


def _guess_step(value: float) -> float:
    """Guess a reasonable spin box step size from a default value.

    Args:
        value: The default parameter value.

    Returns:
        A step size (order-of-magnitude smaller than the value).
    """
    if value == 0:
        return 0.001
    abs_val = abs(value)
    if abs_val >= 100:
        return 1.0
    if abs_val >= 1:
        return 0.1
    if abs_val >= 0.01:
        return 0.001
    if abs_val >= 0.0001:
        return 0.00001
    return abs_val / 10.0


# =======================================================================
# ChannelPanel
# =======================================================================


class ChannelPanel(QGroupBox):
    """4x4 grid of channel checkboxes (CH1-CH16) with batch controls.

    Signals:
        channels_changed(list): Emitted with the list of selected
            1-indexed channel numbers whenever any checkbox toggles.
    """

    channels_changed = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Channels", parent)
        self._checkboxes: list[QCheckBox] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the 4x4 checkbox grid with Select All / None buttons."""
        layout = QVBoxLayout(self)

        # 4x4 grid
        grid = QGridLayout()
        grid.setSpacing(4)
        for i in range(16):
            row = i // 4
            col = i % 4
            cb = QCheckBox(f"CH{i + 1}")
            cb.setChecked(i == 0)  # CH1 checked by default
            cb.toggled.connect(self._on_checkbox_toggled)
            grid.addWidget(cb, row, col)
            self._checkboxes.append(cb)
        layout.addLayout(grid)

        # Select all / none buttons
        btn_row = QHBoxLayout()
        self._select_all_btn = QPushButton("Select All")
        self._select_all_btn.clicked.connect(self.select_all)
        btn_row.addWidget(self._select_all_btn)

        self._select_none_btn = QPushButton("Select None")
        self._select_none_btn.clicked.connect(self.select_none)
        btn_row.addWidget(self._select_none_btn)
        layout.addLayout(btn_row)

    # ---- Public API ------------------------------------------------------

    def selected_channels(self) -> list[int]:
        """Return the list of 1-indexed selected channel numbers.

        Returns:
            Sorted list of channel numbers (e.g., ``[1, 3, 5]``).
        """
        return [
            i + 1
            for i, cb in enumerate(self._checkboxes)
            if cb.isChecked()
        ]

    def select_all(self) -> None:
        """Check all 16 channel checkboxes."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self._emit_channels_changed()

    def select_none(self) -> None:
        """Uncheck all 16 channel checkboxes."""
        for cb in self._checkboxes:
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._emit_channels_changed()

    def set_channels(self, channels: list[int]) -> None:
        """Programmatically set which channels are checked.

        Args:
            channels: List of 1-indexed channel numbers to check.
        """
        for i, cb in enumerate(self._checkboxes):
            cb.blockSignals(True)
            cb.setChecked((i + 1) in channels)
            cb.blockSignals(False)
        self._emit_channels_changed()

    # ---- Internal --------------------------------------------------------

    def _on_checkbox_toggled(self, checked: bool) -> None:
        """Handle any channel checkbox toggle."""
        self._emit_channels_changed()

    def _emit_channels_changed(self) -> None:
        """Emit channels_changed with the current selection."""
        self.channels_changed.emit(self.selected_channels())


# =======================================================================
# MeasurementControlPanel
# =======================================================================


class MeasurementControlPanel(QGroupBox):
    """Start, Stop (abort), Halt, and Resume buttons.

    Button enable/disable logic:

    - **Idle**: Start enabled, others disabled
    - **Running**: Stop and Halt enabled, Start disabled, Resume disabled
    - **Halted**: Resume and Stop enabled, Start and Halt disabled

    Signals:
        start_requested(): User clicked Start.
        stop_requested(): User clicked Stop (abort).
        halt_requested(): User clicked Halt.
        resume_requested(): User clicked Resume.
    """

    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    halt_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    auto_save_changed = pyqtSignal(bool)
    auto_save_dir_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Measurement", parent)
        self._auto_save_dir: str = ""
        self._setup_ui()
        self.set_idle()

    def _setup_ui(self) -> None:
        """Build the button layout."""
        layout = QVBoxLayout(self)

        # Top row: Start / Stop
        top_row = QHBoxLayout()
        self._start_btn = QPushButton("Start")
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #2ca02c; color: white; "
            "font-weight: bold; padding: 6px; }"
        )
        self._start_btn.clicked.connect(self.start_requested.emit)
        top_row.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #d62728; color: white; "
            "font-weight: bold; padding: 6px; }"
        )
        self._stop_btn.clicked.connect(self.stop_requested.emit)
        top_row.addWidget(self._stop_btn)
        layout.addLayout(top_row)

        # Bottom row: Halt / Resume
        bottom_row = QHBoxLayout()
        self._halt_btn = QPushButton("Halt")
        self._halt_btn.clicked.connect(self.halt_requested.emit)
        bottom_row.addWidget(self._halt_btn)

        self._resume_btn = QPushButton("Resume")
        self._resume_btn.clicked.connect(self.resume_requested.emit)
        bottom_row.addWidget(self._resume_btn)
        layout.addLayout(bottom_row)

        # Auto-save section
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        self._auto_save_cb = QCheckBox("Auto-save CSV during measurement")
        self._auto_save_cb.toggled.connect(self._on_auto_save_toggled)
        layout.addWidget(self._auto_save_cb)

        dir_row = QHBoxLayout()
        self._dir_label = QLabel("Directory: (not set)")
        self._dir_label.setStyleSheet("color: #666; font-size: 11px;")
        dir_row.addWidget(self._dir_label, 1)

        self._browse_btn = QPushButton("Browse...")
        self._browse_btn.setMaximumWidth(80)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        dir_row.addWidget(self._browse_btn)
        layout.addLayout(dir_row)

    # ---- Public state management -----------------------------------------

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
        """Set button states for a halted measurement."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._halt_btn.setEnabled(False)
        self._resume_btn.setEnabled(True)

    def set_disabled(self) -> None:
        """Disable all buttons (e.g., when not connected)."""
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._halt_btn.setEnabled(False)
        self._resume_btn.setEnabled(False)

    # ---- Auto-save API ---------------------------------------------------

    def is_auto_save_enabled(self) -> bool:
        """Return whether auto-save is checked."""
        return self._auto_save_cb.isChecked()

    def auto_save_directory(self) -> str:
        """Return the configured auto-save directory."""
        return self._auto_save_dir

    def set_auto_save(
        self, enabled: bool, directory: str = ""
    ) -> None:
        """Programmatically set auto-save state.

        Args:
            enabled: Whether to enable auto-save.
            directory: Output directory path.
        """
        self._auto_save_cb.blockSignals(True)
        self._auto_save_cb.setChecked(enabled)
        self._auto_save_cb.blockSignals(False)
        if directory:
            self._auto_save_dir = directory
            self._dir_label.setText(
                f"Directory: {os.path.basename(directory)}"
            )
            self._dir_label.setToolTip(directory)

    # ---- Private slots ---------------------------------------------------

    def _on_auto_save_toggled(self, checked: bool) -> None:
        """Handle auto-save checkbox toggle."""
        self.auto_save_changed.emit(checked)

    def _on_browse_clicked(self) -> None:
        """Open directory picker for auto-save output."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Auto-Save Directory",
            self._auto_save_dir or "",
        )
        if path:
            self._auto_save_dir = path
            self._dir_label.setText(
                f"Directory: {os.path.basename(path)}"
            )
            self._dir_label.setToolTip(path)
            self.auto_save_dir_changed.emit(path)


# =======================================================================
# ElectrodeConfigPanel
# =======================================================================


class ElectrodeConfigPanel(QGroupBox):
    """Three-way radio selector for electrode wiring configuration.

    Maps the user's physical wiring choice to one of three modes
    consumed by :class:`src.data.models.TechniqueConfig`:

    * **external** (default) -- separate external Ag/AgCl + Pt electrodes
      wired into MUX RE/CE position 15 (``EXTERNAL_RE_CE_CHANNEL``).
      All 16 WE channels (CH1-CH16) remain user-selectable.
    * **on_board** -- on-board combined RE+CE on MUX position 16
      (``ON_BOARD_RE_CE_CHANNEL``).  All 16 WE channels remain
      user-selectable.
    * **manual** -- operator-supplied per-WE RE/CE pairing, both
      constrained to CH1-CH14 (``MODE_C_MAX_CHANNEL``).  CH15+CH16
      are infrastructure-reserved in this mode.

    See ``docs/enclosure_design.md`` for the physical wiring contract.

    Signals:
        mode_changed(str): Emitted with ``"external"``, ``"on_board"``,
            or ``"manual"`` when the selected radio changes.
    """

    mode_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Electrode Configuration", parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the radio panel."""
        layout = QVBoxLayout(self)
        layout.setSpacing(2)

        # Mutually exclusive button group so only one mode is active.
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        self._radio_external = QRadioButton(
            "Separate CE + RE (external)"
        )
        self._radio_external.setToolTip(
            "External Ag/AgCl + Pt wired into MUX position "
            f"{EXTERNAL_RE_CE_CHANNEL}. All 16 WE channels selectable."
        )
        self._radio_external.setChecked(True)  # default mode
        self._group.addButton(self._radio_external)
        layout.addWidget(self._radio_external)

        self._radio_on_board = QRadioButton(
            "On-board RE/CE (combined)"
        )
        self._radio_on_board.setToolTip(
            "On-board combined RE+CE shorted at MUX position "
            f"{ON_BOARD_RE_CE_CHANNEL}. All 16 WE channels selectable."
        )
        self._group.addButton(self._radio_on_board)
        layout.addWidget(self._radio_on_board)

        self._radio_manual = QRadioButton(
            "Manual (per-WE pairing)"
        )
        self._radio_manual.setToolTip(
            "Operator-supplied per-WE RE/CE pairing. Both WE and "
            f"RE/CE constrained to CH1-CH{MODE_C_MAX_CHANNEL} "
            "(CH15+CH16 reserved as infrastructure)."
        )
        self._group.addButton(self._radio_manual)
        layout.addWidget(self._radio_manual)

        # Emit on any selection change.  Connect to toggled(True) only
        # to fire once per user action (not twice for the un-checked +
        # checked pair).
        self._radio_external.toggled.connect(self._on_radio_toggled)
        self._radio_on_board.toggled.connect(self._on_radio_toggled)
        self._radio_manual.toggled.connect(self._on_radio_toggled)

    # ---- Public API ------------------------------------------------------

    def selected_mode(self) -> str:
        """Return the currently selected wiring mode.

        Returns:
            One of ``"external"``, ``"on_board"``, or ``"manual"``.
        """
        if self._radio_manual.isChecked():
            return "manual"
        if self._radio_on_board.isChecked():
            return "on_board"
        return "external"

    def set_mode(self, mode: str) -> None:
        """Programmatically select a mode.

        Args:
            mode: One of ``"external"``, ``"on_board"``, ``"manual"``.
                Unknown values are ignored.
        """
        mode = (mode or "").lower()
        if mode == "external":
            self._radio_external.setChecked(True)
        elif mode == "on_board":
            self._radio_on_board.setChecked(True)
        elif mode == "manual":
            self._radio_manual.setChecked(True)

    # ---- Private slots ---------------------------------------------------

    def _on_radio_toggled(self, checked: bool) -> None:
        """Emit mode_changed once when a radio gets checked."""
        if checked:
            self.mode_changed.emit(self.selected_mode())


# =======================================================================
# ManualChannelPanel
# =======================================================================


class ManualChannelPanel(QGroupBox):
    """14-row table for per-WE channel pairing in Mode C (manual).

    Each row represents one of the manually-pairable channels
    (CH1 through CH``MODE_C_MAX_CHANNEL`` == CH14).  CH15 and CH16
    are infrastructure-reserved for the ``external`` / ``on_board``
    modes and do NOT appear here.

    Each row has:

    * An ``Enable`` checkbox -- when checked, the row's WE channel
      is included in the measurement.
    * A ``RE/CE`` combo box listing CH1-CH``MODE_C_MAX_CHANNEL``.

    Three bulk-set buttons below the table:

    * **Apply same-position** -- for every enabled WE row N, set
      RE/CE = N (each WE paired with its own physical position).
    * **Apply CH1 to all** -- for every enabled WE row, set RE/CE = 1.
    * **Apply CH13 to all** -- for every enabled WE row, set RE/CE = 13.

    Signals:
        pairs_changed(list, list): Emitted with two parallel lists
            (enabled WE channels, matching RE/CE channels) whenever
            any enable or RE/CE selection changes.  The lists are
            sorted by WE channel number; lengths always match.
    """

    pairs_changed = pyqtSignal(list, list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            f"Manual Channel Pairing (CH1-CH{MODE_C_MAX_CHANNEL})",
            parent,
        )
        self._enable_boxes: list[QCheckBox] = []
        self._re_ce_combos: list[QComboBox] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the 14-row pairing table and bulk-set buttons."""
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Header row
        grid = QGridLayout()
        grid.setSpacing(4)
        grid.addWidget(QLabel("<b>WE</b>"), 0, 0)
        grid.addWidget(QLabel("<b>Enable</b>"), 0, 1)
        grid.addWidget(QLabel("<b>RE/CE</b>"), 0, 2)

        # Per-channel rows (CH1..CH14)
        for ch in range(1, MODE_C_MAX_CHANNEL + 1):
            row = ch  # row 0 is the header

            grid.addWidget(QLabel(f"CH{ch}"), row, 0)

            cb = QCheckBox()
            # Default CH1 to checked, matching ChannelPanel behaviour
            cb.setChecked(ch == 1)
            cb.toggled.connect(self._on_state_changed)
            grid.addWidget(cb, row, 1)
            self._enable_boxes.append(cb)

            combo = QComboBox()
            for target in range(1, MODE_C_MAX_CHANNEL + 1):
                combo.addItem(f"CH{target}", target)
            # Default RE/CE = CH1 (combo index 0)
            combo.setCurrentIndex(0)
            combo.currentIndexChanged.connect(
                lambda _: self._on_state_changed()
            )
            grid.addWidget(combo, row, 2)
            self._re_ce_combos.append(combo)

        layout.addLayout(grid)

        # Bulk-set buttons
        btn_row = QHBoxLayout()
        self._apply_same_btn = QPushButton("Apply same-position")
        self._apply_same_btn.setToolTip(
            "For every enabled WE, set RE/CE = WE channel number"
        )
        self._apply_same_btn.clicked.connect(
            self._on_apply_same_position
        )
        btn_row.addWidget(self._apply_same_btn)

        self._apply_ch1_btn = QPushButton("Apply CH1 to all")
        self._apply_ch1_btn.setToolTip(
            "For every enabled WE, set RE/CE = CH1"
        )
        self._apply_ch1_btn.clicked.connect(
            lambda: self._apply_uniform_re_ce(1)
        )
        btn_row.addWidget(self._apply_ch1_btn)

        self._apply_ch13_btn = QPushButton("Apply CH13 to all")
        self._apply_ch13_btn.setToolTip(
            "For every enabled WE, set RE/CE = CH13"
        )
        self._apply_ch13_btn.clicked.connect(
            lambda: self._apply_uniform_re_ce(13)
        )
        btn_row.addWidget(self._apply_ch13_btn)
        layout.addLayout(btn_row)

    # ---- Public API ------------------------------------------------------

    def selected_pairs(self) -> tuple[list[int], list[int]]:
        """Return the (WE, RE/CE) lists for currently enabled rows.

        Returns:
            ``(we_channels, re_ce_channels)`` -- two parallel lists
            sorted by WE channel.  ``len(we) == len(re_ce)``.  An empty
            tuple of lists is returned if no rows are enabled.
        """
        we: list[int] = []
        re_ce: list[int] = []
        for idx, (cb, combo) in enumerate(
            zip(self._enable_boxes, self._re_ce_combos)
        ):
            if cb.isChecked():
                we.append(idx + 1)  # 1-indexed channel
                data = combo.currentData()
                re_ce.append(
                    int(data) if data is not None else 1
                )
        # zip-based collection is already in CH1..CH14 order, but be
        # explicit so callers can rely on sortedness.
        if we:
            pairs = sorted(zip(we, re_ce))
            we = [p[0] for p in pairs]
            re_ce = [p[1] for p in pairs]
        return we, re_ce

    def set_pairs(
        self, we_channels: list[int], re_ce_channels: list[int]
    ) -> None:
        """Programmatically set the enabled rows and their RE/CE.

        Channels not present in *we_channels* are unchecked.  RE/CE
        combos for unchecked rows retain their previous value.

        Args:
            we_channels: 1-indexed WE channels to enable
                (1..``MODE_C_MAX_CHANNEL``).
            re_ce_channels: Parallel RE/CE channel list.  If lengths
                do not match, the shorter list wins and remaining
                rows are left at their current RE/CE.
        """
        we_set = set(we_channels)
        # Block signals to avoid an avalanche of pairs_changed during
        # programmatic load.
        for cb in self._enable_boxes:
            cb.blockSignals(True)
        for combo in self._re_ce_combos:
            combo.blockSignals(True)

        try:
            for idx, cb in enumerate(self._enable_boxes):
                cb.setChecked((idx + 1) in we_set)

            for we, re_ce in zip(we_channels, re_ce_channels):
                if 1 <= we <= MODE_C_MAX_CHANNEL:
                    combo = self._re_ce_combos[we - 1]
                    target_idx = combo.findData(int(re_ce))
                    if target_idx >= 0:
                        combo.setCurrentIndex(target_idx)
        finally:
            for cb in self._enable_boxes:
                cb.blockSignals(False)
            for combo in self._re_ce_combos:
                combo.blockSignals(False)

        self._emit_pairs_changed()

    # ---- Internal --------------------------------------------------------

    def _on_apply_same_position(self) -> None:
        """For every enabled row, set RE/CE = WE channel number."""
        for idx, (cb, combo) in enumerate(
            zip(self._enable_boxes, self._re_ce_combos)
        ):
            if cb.isChecked():
                we_channel = idx + 1
                target_idx = combo.findData(we_channel)
                if target_idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(target_idx)
                    combo.blockSignals(False)
        self._emit_pairs_changed()

    def _apply_uniform_re_ce(self, re_ce: int) -> None:
        """For every enabled row, set RE/CE to a single channel.

        Args:
            re_ce: Target RE/CE channel (1..``MODE_C_MAX_CHANNEL``).
        """
        if not (1 <= re_ce <= MODE_C_MAX_CHANNEL):
            return
        for cb, combo in zip(
            self._enable_boxes, self._re_ce_combos
        ):
            if cb.isChecked():
                target_idx = combo.findData(re_ce)
                if target_idx >= 0:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(target_idx)
                    combo.blockSignals(False)
        self._emit_pairs_changed()

    def _on_state_changed(self, *args: Any) -> None:
        """Handle any enable-checkbox or RE/CE combo change."""
        self._emit_pairs_changed()

    def _emit_pairs_changed(self) -> None:
        """Emit ``pairs_changed`` with the current selection."""
        we, re_ce = self.selected_pairs()
        self.pairs_changed.emit(we, re_ce)
