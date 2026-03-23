"""CSV and .pssession file export for measurement results.

Provides two exporter classes:

- ``CSVExporter``: writes one CSV file per channel with metadata headers
  and technique-appropriate column ordering.
- ``PsSessionExporter``: writes a single UTF-16 encoded JSON file in
  PalmSens .pssession format for compatibility with CMU.49.011
  analysis pipelines.

Both exporters create timestamped output directories under the project
``exports/`` folder following the ``YYYYMMDD_HHMMSS_technique`` naming
convention.
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from typing import Any

from src.data.models import ChannelData, MeasurementResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Technique-to-column ordering
# ---------------------------------------------------------------------------

# Preferred column order per technique family.  Variables not listed
# here are appended alphabetically after the preferred columns.
_VOLTAMMETRY_COLS = [
    "set_potential",
    "potential",
    "current",
]
_AMPEROMETRY_COLS = [
    "current",
    "set_potential",
    "potential",
]
_POTENTIOMETRY_COLS = [
    "potential",
    "current",
]
_EIS_COLS = [
    "set_frequency",
    "impedance",
    "zreal",
    "zimag",
    "phase",
]

_TECHNIQUE_COLUMN_MAP: dict[str, list[str]] = {
    "lsv": _VOLTAMMETRY_COLS,
    "dpv": _VOLTAMMETRY_COLS,
    "swv": _VOLTAMMETRY_COLS,
    "npv": _VOLTAMMETRY_COLS,
    "acv": _VOLTAMMETRY_COLS,
    "cv": _VOLTAMMETRY_COLS,
    "fcv": _VOLTAMMETRY_COLS,
    "lsp": _VOLTAMMETRY_COLS,
    "pad": _VOLTAMMETRY_COLS,
    "ca": _AMPEROMETRY_COLS,
    "fca": _AMPEROMETRY_COLS,
    "ca_alt_mux": _AMPEROMETRY_COLS,
    "cp": _POTENTIOMETRY_COLS,
    "cp_alt_mux": _POTENTIOMETRY_COLS,
    "ocp": _POTENTIOMETRY_COLS,
    "ocp_alt_mux": _POTENTIOMETRY_COLS,
    "eis": _EIS_COLS,
    "geis": _EIS_COLS,
}


def _ordered_columns(
    technique: str, available: set[str]
) -> list[str]:
    """Return column names in preferred order for the technique.

    Columns listed in the technique preference that exist in
    *available* appear first (in preference order), followed by any
    remaining columns sorted alphabetically.

    Args:
        technique: Lowercase technique identifier.
        available: Set of variable names present in the data.

    Returns:
        Ordered list of column names.
    """
    preferred = _TECHNIQUE_COLUMN_MAP.get(technique, [])
    ordered: list[str] = [c for c in preferred if c in available]
    remaining = sorted(available - set(ordered))
    return ordered + remaining


# ---------------------------------------------------------------------------
# CSVExporter
# ---------------------------------------------------------------------------


class CSVExporter:
    """Exports measurement results to per-channel CSV files.

    Each CSV file contains a metadata header block (comment lines
    prefixed with ``#``) followed by a standard header row and data
    rows.  Column order is technique-aware so the most relevant
    variables appear first.

    Example usage::

        exporter = CSVExporter()
        paths = exporter.export_csv(result, "exports/20260315_120000_cv")
    """

    def export_csv(
        self,
        result: MeasurementResult,
        output_dir: str,
    ) -> list[str]:
        """Write per-channel CSV files for a measurement result.

        Creates *output_dir* if it does not exist.  One file is written
        per channel that contains data points, named ``ch01.csv``,
        ``ch02.csv``, etc.

        Args:
            result: The measurement result to export.
            output_dir: Directory to write CSV files into.

        Returns:
            List of absolute file paths written.
        """
        os.makedirs(output_dir, exist_ok=True)
        written: list[str] = []

        for ch in result.measured_channels:
            ch_data = result.channel_data(ch)
            if not ch_data.data_points:
                continue

            filepath = os.path.join(output_dir, f"ch{ch:02d}.csv")
            self._write_channel_csv(
                filepath, ch_data, result
            )
            written.append(os.path.abspath(filepath))
            logger.info(
                "Wrote CSV for channel %d: %s", ch, filepath
            )

        return written

    # Alias used by main_window._write_csv_files
    def export(
        self,
        result: MeasurementResult,
        output_dir: str,
    ) -> list[str]:
        """Alias for :meth:`export_csv` (GUI compatibility).

        Args:
            result: The measurement result to export.
            output_dir: Directory to write CSV files into.

        Returns:
            List of absolute file paths written.
        """
        return self.export_csv(result, output_dir)

    # -- Internal -----------------------------------------------------------

    @staticmethod
    def _write_channel_csv(
        filepath: str,
        ch_data: ChannelData,
        result: MeasurementResult,
    ) -> None:
        """Write a single channel's data to a CSV file.

        Args:
            filepath: Destination file path.
            ch_data: Filtered channel data.
            result: Full measurement result (for metadata).
        """
        # Collect all variable names across data points
        all_vars: set[str] = set()
        for dp in ch_data.data_points:
            all_vars.update(dp.variables.keys())
        columns = _ordered_columns(ch_data.technique, all_vars)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            # Metadata header
            f.write(f"# Technique: {result.technique}\n")
            f.write(f"# Channel: {ch_data.channel}\n")
            if result.start_time is not None:
                f.write(
                    f"# Timestamp: "
                    f"{result.start_time.isoformat()}\n"
                )
            serial = result.device_info.get("serial", "")
            firmware = result.device_info.get("firmware", "")
            if serial:
                f.write(f"# Device Serial: {serial}\n")
            if firmware:
                f.write(f"# Firmware Version: {firmware}\n")
            if result.params:
                params_str = ", ".join(
                    f"{k}={v}" for k, v in result.params.items()
                )
                f.write(f"# Parameters: {params_str}\n")
            f.write("#\n")

            # Data rows
            writer = csv.writer(f)
            writer.writerow(["timestamp"] + columns)
            for dp in ch_data.data_points:
                row: list[Any] = [dp.timestamp]
                for col in columns:
                    row.append(dp.variables.get(col, ""))
                writer.writerow(row)


# ---------------------------------------------------------------------------
# PsSessionExporter
# ---------------------------------------------------------------------------


class PsSessionExporter:
    """Exports measurement results to PalmSens .pssession format.

    The .pssession format is a UTF-16 LE encoded JSON file used by
    PalmSens software (PSTrace) and compatible with the CMU.49.011
    analysis pipeline.  The JSON structure matches the PalmSens SDK
    session object hierarchy so that
    ``psession_parser.extract_experiments_from_session()`` can
    parse the output directly.

    Example usage::

        exporter = PsSessionExporter()
        path = exporter.export_pssession(result, "run.pssession")
    """

    def export_pssession(
        self,
        result: MeasurementResult,
        output_path: str,
    ) -> str:
        """Write a .pssession file for a measurement result.

        Creates parent directories if they do not exist.

        Args:
            result: The measurement result to export.
            output_path: Destination file path (should end in
                ``.pssession``).

        Returns:
            Absolute path to the written file.
        """
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        session = self._build_session(result)

        with open(
            output_path, "w", encoding="utf-16-le"
        ) as f:
            json.dump(session, f, indent=2, ensure_ascii=False)

        abs_path = os.path.abspath(output_path)
        logger.info("Wrote .pssession file: %s", abs_path)
        return abs_path

    # -- Internal -----------------------------------------------------------

    @staticmethod
    def _build_session(
        result: MeasurementResult,
    ) -> dict[str, Any]:
        """Build the PalmSens-compatible session JSON structure.

        Produces output consumable by CMU.49.011's
        ``psession_parser.extract_experiments_from_session()``.

        Args:
            result: The measurement result.

        Returns:
            Dictionary matching the real PalmSens .pssession schema.
        """
        technique = result.technique.lower()
        method_id = _TECHNIQUE_TO_METHOD_ID.get(technique, technique)
        method_str = _build_method_string(method_id, result)
        axis_cfg = _TECHNIQUE_AXIS_CONFIG.get(
            technique, _TECHNIQUE_AXIS_CONFIG["_default"]
        )
        measured = result.measured_channels
        ch_range = (
            f"channels {measured[0]}-{measured[-1]}"
            if measured
            else ""
        )
        title = _TECHNIQUE_TITLES.get(technique, technique.upper())
        measurement_title = (
            f"{title} - {ch_range}" if ch_range else title
        )

        # Build one Curve per channel (X=time/potential, Y=current/etc.)
        curves: list[dict[str, Any]] = []
        for ch in measured:
            ch_data = result.channel_data(ch)
            if not ch_data.data_points:
                continue

            x_values = _extract_x_values(ch_data, axis_cfg)
            y_values = _extract_y_values(ch_data, axis_cfg)

            if not x_values or not y_values:
                continue

            curve: dict[str, Any] = {
                "Title": f"{title} {axis_cfg['y_short']} vs "
                         f"{axis_cfg['x_short']} Channel {ch}",
                "XAxisDataArray": {
                    "Type": axis_cfg["x_array_type"],
                    "Description": axis_cfg["x_description"],
                    "DataValues": [{"V": v} for v in x_values],
                    "Unit": axis_cfg["x_unit"],
                },
                "YAxisDataArray": {
                    "Type": axis_cfg["y_array_type"],
                    "Description": f"channel{ch}",
                    "DataValues": [
                        _make_y_data_value(v, axis_cfg)
                        for v in y_values
                    ],
                    "Unit": axis_cfg["y_unit"],
                },
                "XAxis": {"Name": axis_cfg["x_axis_name"]},
                "YAxis": {"Name": axis_cfg["y_axis_name"]},
            }
            curves.append(curve)

        measurement: dict[str, Any] = {
            "Title": measurement_title,
            "Method": method_str,
            "Type": "PalmSens.Comm.GenericCommMeasurement",
            "DeviceSerial": result.device_info.get("serial", ""),
            "DeviceFW": result.device_info.get("firmware", ""),
            "Curves": curves,
            "EISDataList": [],
        }

        session: dict[str, Any] = {
            "Type": "PalmSens.DataFiles.SessionFile",
            "CoreVersion": "1.0.0",
            "MethodForMeasurement": method_str,
            "Measurements": [measurement],
        }
        return session


# ---------------------------------------------------------------------------
# .pssession helper constants and functions
# ---------------------------------------------------------------------------

# Technique name → PalmSens METHOD_ID
_TECHNIQUE_TO_METHOD_ID: dict[str, str] = {
    "cv": "cv",
    "lsv": "lsv",
    "dpv": "dpv",
    "swv": "swv",
    "npv": "npv",
    "acv": "acv",
    "fcv": "fcv",
    "lsp": "lsp",
    "pad": "pad",
    "ca": "ad",
    "fca": "ad",
    "ca_alt_mux": "ad",
    "cp": "cp",
    "ocp": "ocp",
    "cp_alt_mux": "cp",
    "ocp_alt_mux": "ocp",
    "eis": "eis",
    "geis": "geis",
}

# Friendly titles for measurement names
_TECHNIQUE_TITLES: dict[str, str] = {
    "cv": "Cyclic Voltammetry",
    "lsv": "Linear Sweep Voltammetry",
    "dpv": "Differential Pulse Voltammetry",
    "swv": "Square Wave Voltammetry",
    "npv": "Normal Pulse Voltammetry",
    "acv": "AC Voltammetry",
    "fcv": "Fast Cyclic Voltammetry",
    "lsp": "Linear Sweep Potentiometry",
    "pad": "Pulsed Amperometric Detection",
    "ca": "Chronoamperometry",
    "fca": "Fast Chronoamperometry",
    "ca_alt_mux": "Chronoamperometry",
    "cp": "Chronopotentiometry",
    "ocp": "Open Circuit Potentiometry",
    "cp_alt_mux": "Chronopotentiometry",
    "ocp_alt_mux": "Open Circuit Potentiometry",
    "eis": "Electrochemical Impedance Spectroscopy",
    "geis": "Galvanostatic EIS",
}

# Axis configuration per technique family.
# Keys: x_var (variable name in DataPoint), y_var, axis labels, unit dicts,
# PalmSens DataArray type strings, short names for curve titles.
_AMPEROMETRY_AXIS: dict[str, Any] = {
    "x_var": "__time__",
    "y_var": "current",
    "x_axis_name": "Time",
    "y_axis_name": "Current",
    "x_short": "t",
    "y_short": "i",
    "x_description": "time",
    "x_array_type": "PalmSens.Data.DataArrayTime",
    "y_array_type": "PalmSens.Data.DataArrayCurrents",
    "x_unit": {
        "Type": "PalmSens.Units.Time",
        "S": "s", "Q": "Time", "A": "t",
    },
    "y_unit": {
        "Type": "PalmSens.Units.Ampere",
        "S": "A", "Q": "Current", "A": "i",
    },
    "y_is_current": True,
}

_VOLTAMMETRY_AXIS: dict[str, Any] = {
    "x_var": "set_potential",
    "y_var": "current",
    "x_axis_name": "Potential",
    "y_axis_name": "Current",
    "x_short": "E",
    "y_short": "i",
    "x_description": "potential",
    "x_array_type": "PalmSens.Data.DataArrayPotentials",
    "y_array_type": "PalmSens.Data.DataArrayCurrents",
    "x_unit": {
        "Type": "PalmSens.Units.Volt",
        "S": "V", "Q": "Potential", "A": "E",
    },
    "y_unit": {
        "Type": "PalmSens.Units.Ampere",
        "S": "A", "Q": "Current", "A": "i",
    },
    "y_is_current": True,
}

_POTENTIOMETRY_AXIS: dict[str, Any] = {
    "x_var": "__time__",
    "y_var": "potential",
    "x_axis_name": "Time",
    "y_axis_name": "Potential",
    "x_short": "t",
    "y_short": "E",
    "x_description": "time",
    "x_array_type": "PalmSens.Data.DataArrayTime",
    "y_array_type": "PalmSens.Data.DataArrayPotentials",
    "x_unit": {
        "Type": "PalmSens.Units.Time",
        "S": "s", "Q": "Time", "A": "t",
    },
    "y_unit": {
        "Type": "PalmSens.Units.Volt",
        "S": "V", "Q": "Potential", "A": "E",
    },
    "y_is_current": False,
}

_EIS_AXIS: dict[str, Any] = {
    "x_var": "zreal",
    "y_var": "zimag",
    "x_axis_name": "Z_real",
    "y_axis_name": "Z_imag",
    "x_short": "Z'",
    "y_short": "-Z''",
    "x_description": "zreal",
    "x_array_type": "PalmSens.Data.DataArrayGeneric",
    "y_array_type": "PalmSens.Data.DataArrayGeneric",
    "x_unit": {
        "Type": "PalmSens.Units.Ohm",
        "S": "Ohm", "Q": "Impedance", "A": "Z",
    },
    "y_unit": {
        "Type": "PalmSens.Units.Ohm",
        "S": "Ohm", "Q": "Impedance", "A": "Z",
    },
    "y_is_current": False,
}

_TECHNIQUE_AXIS_CONFIG: dict[str, dict[str, Any]] = {
    "cv": _VOLTAMMETRY_AXIS,
    "lsv": _VOLTAMMETRY_AXIS,
    "dpv": _VOLTAMMETRY_AXIS,
    "swv": _VOLTAMMETRY_AXIS,
    "npv": _VOLTAMMETRY_AXIS,
    "acv": _VOLTAMMETRY_AXIS,
    "fcv": _VOLTAMMETRY_AXIS,
    "lsp": _VOLTAMMETRY_AXIS,
    "pad": _VOLTAMMETRY_AXIS,
    "ca": _AMPEROMETRY_AXIS,
    "fca": _AMPEROMETRY_AXIS,
    "ca_alt_mux": _AMPEROMETRY_AXIS,
    "cp": _POTENTIOMETRY_AXIS,
    "ocp": _POTENTIOMETRY_AXIS,
    "cp_alt_mux": _POTENTIOMETRY_AXIS,
    "ocp_alt_mux": _POTENTIOMETRY_AXIS,
    "eis": _EIS_AXIS,
    "geis": _EIS_AXIS,
    "_default": _AMPEROMETRY_AXIS,
}


def _build_method_string(
    method_id: str, result: MeasurementResult
) -> str:
    """Build a minimal MethodForMeasurement INI string.

    Only the ``METHOD_ID`` line is required for CMU.49.011
    parsing; additional fields are included for informational
    purposes.
    """
    lines = [
        f"METHOD_ID={method_id}",
        f"TECHNIQUE={result.technique}",
    ]
    if result.channels:
        ch_bits = sum(1 << (ch - 1) for ch in result.channels)
        lines.append(f"USE_MUX_CH={ch_bits}")
    for k, v in result.params.items():
        lines.append(f"{k.upper()}={v}")
    return "\r\n".join(lines) + "\r\n"


def _extract_x_values(
    ch_data: ChannelData, axis_cfg: dict[str, Any]
) -> list[float]:
    """Extract X-axis values from channel data."""
    x_var = axis_cfg["x_var"]
    if x_var == "__time__":
        return ch_data.timestamps()
    return ch_data.values(x_var)


def _extract_y_values(
    ch_data: ChannelData, axis_cfg: dict[str, Any]
) -> list[float]:
    """Extract Y-axis values from channel data."""
    return ch_data.values(axis_cfg["y_var"])


def _make_y_data_value(
    value: float, axis_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Create a DataValues entry for a Y-axis value.

    Current readings include ``C`` (current range) and ``S``
    (status) fields to match PalmSens format.
    """
    if axis_cfg.get("y_is_current"):
        return {"V": value, "C": 0, "S": 0}
    return {"V": value}


# ---------------------------------------------------------------------------
# Convenience: create timestamped output directory
# ---------------------------------------------------------------------------


def make_export_dir(
    base_dir: str, technique: str
) -> str:
    """Create a timestamped export directory.

    The directory name follows the ``YYYYMMDD_HHMMSS_technique``
    convention.

    Args:
        base_dir: Parent directory (e.g., ``exports/``).
        technique: Technique identifier for the directory name.

    Returns:
        Absolute path to the created directory.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dirname = f"{timestamp}_{technique}"
    path = os.path.join(base_dir, dirname)
    os.makedirs(path, exist_ok=True)
    return os.path.abspath(path)
