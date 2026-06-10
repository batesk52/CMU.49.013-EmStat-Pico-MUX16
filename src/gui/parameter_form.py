"""Reusable per-technique parameter editor (CMU.17.034).

``ParameterForm`` builds an editable grid of spin boxes / combos for a
technique's parameters, seeded from a values dict and read back via
:meth:`get_params`. It is the same form the single-run technique panel
renders, factored out so each sequence step can embed its own editable
copy (a PSTrace-"Scripts"-style block).

The widget-building logic and label/range constants are shared with
``controls.py`` (imported here) so both editors stay in lock-step; this
module never imports anything that would import it back.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

# Reuse the single-run panel's label/range tables and step heuristic so
# the two editors render identically. These are module-level constants in
# controls.py; importing them (rather than duplicating) keeps them in sync.
from src.gui.controls import (
    _BANDWIDTH_HZ,
    _CURRENT_RANGES,
    _PARAM_LABELS,
    _guess_step,
)
from src.techniques.scripts import technique_params


def create_param_widget(name: str, default: Any, on_change) -> QWidget:
    """Create the appropriate input widget for a single parameter.

    Mirrors ``TechniquePanel._create_param_widget`` but takes an explicit
    ``on_change`` callback so it is reusable outside that class.

    Args:
        name: Parameter name (drives special-cased widgets like ``cr``).
        default: Seed value.
        on_change: Zero-arg callable fired whenever the value changes.

    Returns:
        A ``QComboBox`` (``cr`` / ``bw_hz``), ``QSpinBox`` (int) or
        ``QDoubleSpinBox`` (float).
    """
    if name == "cr":
        combo = QComboBox()
        for cr in _CURRENT_RANGES:
            combo.addItem(cr)
        idx = combo.findText(str(default))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.currentIndexChanged.connect(lambda: on_change())
        return combo

    if name == "bw_hz":
        combo = QComboBox()
        for hz in _BANDWIDTH_HZ:
            label = str(int(hz)) if float(hz).is_integer() else str(hz)
            combo.addItem(label, hz)
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
        combo.currentIndexChanged.connect(lambda: on_change())
        return combo

    if isinstance(default, int):
        spin = QSpinBox()
        spin.setRange(0, 10000)
        spin.setValue(default)
        spin.valueChanged.connect(lambda: on_change())
        return spin

    spin = QDoubleSpinBox()
    spin.setDecimals(6)
    spin.setRange(-10.0, 1e8)
    spin.setSingleStep(_guess_step(default))
    spin.setValue(float(default))
    spin.valueChanged.connect(lambda: on_change())
    return spin


def read_param_widget(name: str, widget: QWidget) -> Any:
    """Read one parameter widget's current value (inverse of create)."""
    if isinstance(widget, QDoubleSpinBox):
        return widget.value()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QComboBox):
        if name == "bw_hz":
            data = widget.currentData()
            if data is None:
                try:
                    data = float(widget.currentText())
                except (TypeError, ValueError):
                    data = 400
            return data
        return widget.currentText()
    return None


class ParameterForm(QWidget):
    """An editable grid of a technique's parameters.

    Build it, call :meth:`set_config` with a technique + seed values, edit
    in the GUI, and read the result back with :meth:`get_params`.

    Signals:
        params_changed(): Emitted whenever any field changes.
    """

    params_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._technique: str = ""
        self._widgets: dict[str, QWidget] = {}
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)

    def set_config(self, technique: str, params: dict[str, Any]) -> None:
        """Rebuild the form for ``technique`` and seed it with ``params``.

        Every parameter the technique defines gets a row (so the user sees
        the full set), with the seed value applied where supplied.

        Args:
            technique: Technique identifier.
            params: Values to pre-fill (a subset is fine).
        """
        self._technique = technique.lower()
        self._widgets.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        try:
            defaults = technique_params(self._technique)
        except ValueError:
            return
        # Seed defaults with the supplied values (supplied wins).
        merged = {**defaults, **{k: params[k] for k in params if k in defaults}}

        row = 0
        for name, value in merged.items():
            label_text, unit = _PARAM_LABELS.get(name, (name, ""))
            display = f"{label_text} ({unit})" if unit else label_text
            self._grid.addWidget(QLabel(display), row, 0)
            widget = create_param_widget(
                name, value, self.params_changed.emit
            )
            self._grid.addWidget(widget, row, 1)
            self._widgets[name] = widget
            row += 1

    def get_params(self) -> dict[str, Any]:
        """Return the current values as a parameter dict."""
        return {
            name: read_param_widget(name, widget)
            for name, widget in self._widgets.items()
        }

    @property
    def technique(self) -> str:
        """The technique this form is currently configured for."""
        return self._technique
