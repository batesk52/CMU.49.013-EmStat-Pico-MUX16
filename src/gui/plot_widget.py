"""Live pyqtgraph plot widget with per-channel curves.

Provides a ``LivePlotWidget`` that receives real-time data points from
the measurement engine and renders per-channel curves with distinct
colors.  Axis labels and plot configuration adapt automatically to the
selected electrochemical technique.

Typical usage from the main window::

    plot = LivePlotWidget()
    plot.set_technique("cv")
    engine.data_point_ready.connect(plot.on_data_point)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import pyqtSlot

from src.data.models import DataPoint

# ---------------------------------------------------------------------------
# 16-color palette for per-channel curves
# ---------------------------------------------------------------------------

# Distinct, colorblind-friendly palette (16 colours).  Each entry is an
# RGBA tuple suitable for ``pg.mkPen()``.
CHANNEL_COLORS: list[tuple[int, int, int]] = [
    (31, 119, 180),   # CH1  - blue
    (255, 127, 14),   # CH2  - orange
    (44, 160, 44),    # CH3  - green
    (214, 39, 40),    # CH4  - red
    (148, 103, 189),  # CH5  - purple
    (140, 86, 75),    # CH6  - brown
    (227, 119, 194),  # CH7  - pink
    (127, 127, 127),  # CH8  - grey
    (188, 189, 34),   # CH9  - olive
    (23, 190, 207),   # CH10 - cyan
    (174, 199, 232),  # CH11 - light blue
    (255, 187, 120),  # CH12 - light orange
    (152, 223, 138),  # CH13 - light green
    (255, 152, 150),  # CH14 - light red
    (197, 176, 213),  # CH15 - light purple
    (196, 156, 148),  # CH16 - light brown
]

# ---------------------------------------------------------------------------
# Technique axis presets
# ---------------------------------------------------------------------------

# Maps technique identifiers to (x_label, y_label, x_var, y_var) tuples.
# x_var/y_var are the DataPoint.variables keys used for each axis.
_TECHNIQUE_AXES: dict[str, tuple[str, str, str, str]] = {
    # Voltammetric: I vs E
    "cv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "fcv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "lsv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "lsp": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "dpv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "swv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "npv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "acv": ("Potential (V)", "Current (A)", "set_potential", "current"),
    "pad": ("Potential (V)", "Current (A)", "set_potential", "current"),
    # Amperometric: I vs t
    "ca": ("Time (s)", "Current (A)", "time", "current"),
    "fca": ("Time (s)", "Current (A)", "time", "current"),
    "ca_alt_mux": ("Time (s)", "Current (A)", "time", "current"),
    # Potentiometric: E vs t
    "cp": (
        "Time (s)",
        "Potential (V)",
        "time",
        "measured_potential",
    ),
    "cp_alt_mux": (
        "Time (s)",
        "Potential (V)",
        "time",
        "measured_potential",
    ),
    "ocp": (
        "Time (s)",
        "Potential (V)",
        "time",
        "measured_potential",
    ),
    "ocp_alt_mux": (
        "Time (s)",
        "Potential (V)",
        "time",
        "measured_potential",
    ),
    # Impedance: -Z'' vs Z' (Nyquist)
    "eis": ("Z' (Ohm)", "-Z'' (Ohm)", "impedance", "phase"),
    "geis": ("Z' (Ohm)", "-Z'' (Ohm)", "impedance", "phase"),
}

# Default axis configuration for unknown techniques.
_DEFAULT_AXES = ("X", "Y", None, None)


class LivePlotWidget(pg.PlotWidget):
    """Real-time plot widget with per-channel curves.

    Subclasses ``pyqtgraph.PlotWidget`` to provide:

    * Per-channel curves with a 16-colour palette.
    * Technique-aware axis labels that update automatically.
    * ``add_point(channel, x, y)`` for real-time point appending.
    * ``on_data_point(DataPoint)`` slot for direct engine signal
      connection.
    * Auto-range that respects manual zoom/pan overrides.
    * Clear/reset between measurements.

    Args:
        parent: Optional parent QWidget.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent=parent, background="w")

        # Current technique (lowercase key)
        self._technique: str = ""

        # Variable names to extract from DataPoint for each axis
        self._x_var: Optional[str] = None
        self._y_var: Optional[str] = None

        # Per-channel data buffers: channel (1-indexed) -> (x_list, y_list)
        self._data: dict[int, tuple[list[float], list[float]]] = {}

        # Per-channel PlotDataItem curves
        self._curves: dict[int, pg.PlotDataItem] = {}

        # Whether the user has manually zoomed/panned (disables auto-range)
        self._user_interacted: bool = False

        # Configure plot appearance
        self._setup_plot()

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _setup_plot(self) -> None:
        """Configure default plot appearance."""
        plot_item = self.getPlotItem()
        if plot_item is None:
            return

        plot_item.showGrid(x=True, y=True, alpha=0.3)
        plot_item.setLabel("bottom", "X")
        plot_item.setLabel("left", "Y")

        # Enable auto-range by default
        self.enableAutoRange()

        # Detect manual zoom/pan to disable auto-range
        view_box = plot_item.getViewBox()
        if view_box is not None:
            view_box.sigRangeChangedManually.connect(
                self._on_range_changed_manually
            )

    def _on_range_changed_manually(self) -> None:
        """Mark that the user has manually adjusted the view."""
        self._user_interacted = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_technique(self, technique: str) -> None:
        """Configure axis labels and variable mapping for a technique.

        Looks up the technique in the preset table and updates axis
        labels and the variable names used to extract x/y values from
        incoming ``DataPoint`` objects.

        Args:
            technique: Lowercase technique identifier (e.g., 'cv',
                'eis', 'ca').
        """
        technique = technique.lower()
        self._technique = technique

        x_label, y_label, x_var, y_var = _TECHNIQUE_AXES.get(
            technique, _DEFAULT_AXES
        )
        self._x_var = x_var
        self._y_var = y_var

        self.set_axes(x_label, y_label)

    def set_axes(self, x_label: str, y_label: str) -> None:
        """Set the axis labels on the plot.

        Args:
            x_label: Label for the bottom (x) axis.
            y_label: Label for the left (y) axis.
        """
        plot_item = self.getPlotItem()
        if plot_item is not None:
            plot_item.setLabel("bottom", x_label)
            plot_item.setLabel("left", y_label)

    def add_point(self, channel: int, x: float, y: float) -> None:
        """Append a single data point to the specified channel's curve.

        Creates the curve on first use for the channel.  Updates
        auto-range unless the user has manually zoomed/panned.

        Args:
            channel: 1-indexed MUX channel number (1-16).
            x: X-axis value.
            y: Y-axis value.
        """
        if channel not in self._data:
            self._data[channel] = ([], [])
            self._create_curve(channel)

        x_buf, y_buf = self._data[channel]
        x_buf.append(x)
        y_buf.append(y)

        # Update the curve with the full buffer
        curve = self._curves.get(channel)
        if curve is not None:
            curve.setData(
                np.array(x_buf, dtype=np.float64),
                np.array(y_buf, dtype=np.float64),
            )

        # Restore auto-range if user hasn't manually interacted
        if not self._user_interacted:
            self.enableAutoRange()

    @pyqtSlot(object)
    def on_data_point(self, data_point: DataPoint) -> None:
        """Slot for ``MeasurementEngine.data_point_ready`` signal.

        Extracts the appropriate x and y values from the data point
        based on the current technique's variable mapping and calls
        ``add_point()``.

        For EIS techniques, the y-axis is negated (Nyquist convention:
        -Z'' vs Z').

        Args:
            data_point: A decoded ``DataPoint`` from the engine.
        """
        channel = data_point.channel

        if self._x_var is not None and self._y_var is not None:
            x = data_point.get(self._x_var, 0.0)
            y = data_point.get(self._y_var, 0.0)

            # Nyquist convention: negate imaginary impedance
            if self._technique in ("eis", "geis"):
                y = -y

            self.add_point(channel, x, y)
        elif data_point.timestamp is not None:
            # Fallback: use timestamp as x and first variable as y
            variables = data_point.variables
            if variables:
                first_value = next(iter(variables.values()))
                self.add_point(
                    channel, data_point.timestamp, first_value
                )

    def clear_plot(self) -> None:
        """Remove all curves and reset data buffers.

        Call this between measurements to start with a clean plot.
        """
        for curve in self._curves.values():
            self.removeItem(curve)
        self._curves.clear()
        self._data.clear()
        self._user_interacted = False
        self.enableAutoRange()

    def reset(self) -> None:
        """Full reset: clear plot and reset technique configuration.

        Restores the widget to its initial state (no technique, default
        axis labels).
        """
        self.clear_plot()
        self._technique = ""
        self._x_var = None
        self._y_var = None
        self.set_axes("X", "Y")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_curve(self, channel: int) -> None:
        """Create a PlotDataItem for the given channel.

        Args:
            channel: 1-indexed channel number.
        """
        color_idx = (channel - 1) % len(CHANNEL_COLORS)
        color = CHANNEL_COLORS[color_idx]
        pen = pg.mkPen(color=color, width=2)

        curve = self.plot(
            [],
            [],
            pen=pen,
            name=f"CH{channel}",
        )
        self._curves[channel] = curve
