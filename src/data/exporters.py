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
import logging
import math
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

        # For EIS: compute impedance magnitude and phase from
        # zreal and zimag if not already present
        is_eis = "zreal" in all_vars and "zimag" in all_vars
        if is_eis:
            all_vars.add("impedance")
            all_vars.add("phase")

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
                # Compute derived EIS values
                vars_with_derived = dict(dp.variables)
                if is_eis:
                    zr = dp.variables.get("zreal", 0.0)
                    zi = dp.variables.get("zimag", 0.0)
                    vars_with_derived["impedance"] = math.sqrt(
                        zr * zr + zi * zi
                    )
                    vars_with_derived["phase"] = math.degrees(
                        math.atan2(zi, zr)
                    )

                row: list[Any] = [dp.timestamp]
                for col in columns:
                    row.append(vars_with_derived.get(col, ""))
                writer.writerow(row)


# ---------------------------------------------------------------------------
# PsSessionExporter (delegated to pssession_exporter module)
# ---------------------------------------------------------------------------

from src.data.pssession_exporter import PsSessionExporter  # noqa: F401, E402


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
