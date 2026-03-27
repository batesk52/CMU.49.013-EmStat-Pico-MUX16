"""PsSessionExporter: writes PSTrace-compatible .pssession files.

Produces UTF-16 LE encoded, minified JSON with BOM and trailing BOM
that matches the PalmSens PSTrace serialisation exactly. Supports
CV, LSV, DPV, SWV, CA, and EIS techniques.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

from src.data.models import MeasurementResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNIT_VOLT: dict[str, str] = {
    "Type": "PalmSens.Units.Volt",
    "S": "V",
    "Q": "Potential",
    "A": "E",
}

UNIT_MICRO_AMPERE: dict[str, str] = {
    "Type": "PalmSens.Units.MicroAmpere",
    "S": "A",
    "Q": "Current",
    "A": "i",
}

UNIT_TIME: dict[str, str] = {
    "Type": "PalmSens.Units.Time",
    "S": "s",
    "Q": "Time",
    "A": "t",
}

UNIT_MICRO_COULOMB: dict[str, str] = {
    "Type": "PalmSens.Units.MicroCoulomb",
    "S": "C",
    "Q": "Charge",
    "A": "Q",
}

# Technique name → integer code used in PSTrace method strings
_TECHNIQUE_NUMBER: dict[str, int] = {
    "cv": 5,
    "lsv": 2,
    "dpv": 3,
    "swv": 4,
    "ca": 7,
    "ca_alt_mux": 7,
    "eis": 14,
}

# Technique name → friendly title
_TECHNIQUE_TITLES: dict[str, str] = {
    "cv": "Cyclic Voltammetry",
    "lsv": "Linear Sweep Voltammetry",
    "dpv": "Differential Pulse Voltammetry",
    "swv": "Square Wave Voltammetry",
    "ca": "Chronoamperometry",
    "ca_alt_mux": "Chronoamperometry",
    "eis": "Impedance Spectroscopy",
}

# EIS techniques that use ImpedimetricMeasurement type
_EIS_TECHNIQUES = {"eis", "geis"}

# Techniques that use curves (non-EIS)
_CURVE_TECHNIQUES = {
    "cv", "lsv", "dpv", "swv", "ca", "ca_alt_mux",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def datetime_to_dotnet_ticks(dt: datetime) -> int:
    """Convert a datetime to .NET local DateTime ticks.

    .NET ticks are 100-nanosecond intervals since 0001-01-01 00:00:00.
    This returns ticks for the local time representation.
    """
    unix_ts = dt.timestamp()
    return int(unix_ts * 10_000_000) + 621_355_968_000_000_000


def datetime_to_dotnet_utc_ticks(dt: datetime) -> int:
    """Convert a datetime to .NET UTC DateTime ticks.

    Returns ticks for the UTC representation.
    """
    if dt.tzinfo is not None:
        utc_ts = dt.astimezone(timezone.utc).timestamp()
    else:
        utc_ts = dt.timestamp()
    # For UTC ticks we add the UTC offset
    utc_offset_seconds = time.timezone
    if time.daylight and time.localtime(dt.timestamp()).tm_isdst:
        utc_offset_seconds = time.altzone
    utc_ticks = (
        int(utc_ts * 10_000_000)
        + 621_355_968_000_000_000
        + utc_offset_seconds * 10_000_000
    )
    return utc_ticks


def default_appearance(color: str = "-16776961") -> dict[str, Any]:
    """Return a default PalmSens VisualSettings dict."""
    return {
        "Type": "PalmSens.Plottables.VisualSettings",
        "AutoAssignColor": True,
        "Color": color,
        "LineWidth": 2,
        "SymbolSize": 5,
        "SymbolType": 0,
        "SymbolFill": True,
        "NoLine": False,
    }


def default_appearance_subscan(
    color: str = "-16776961",
) -> dict[str, Any]:
    """Return a VisualSettings for EIS frequency sub-scan curves."""
    return {
        "Type": "PalmSens.Plottables.VisualSettings",
        "AutoAssignColor": True,
        "Color": color,
        "LineWidth": 1,
        "SymbolSize": 5,
        "SymbolType": 0,
        "SymbolFill": True,
        "NoLine": False,
    }


def random_hash() -> list[int]:
    """Return a 48-byte random hash (like SHA-384)."""
    return [random.randint(0, 255) for _ in range(48)]


def build_method_string(
    method_id: str, result: MeasurementResult
) -> str:
    """Build a PSTrace-compatible MethodForMeasurement string.

    Includes the 3-line PSTrace header and TECHNIQUE as integer code.
    """
    technique = result.technique.lower()
    tech_num = _TECHNIQUE_NUMBER.get(technique, 0)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "#PSTrace, Version=5.12.1031.30690",
        f"#{now_str}",
        "#",
        "#Method file version",
        "METHOD_VERSION=1",
        "#Technique and application",
        f"METHOD_ID={method_id}",
        f"TECHNIQUE={tech_num}",
        "NOTES=",
    ]

    # Add technique parameters in scientific notation
    for k, v in result.params.items():
        key_upper = k.upper()
        if isinstance(v, bool):
            lines.append(f"{key_upper}={v}")
        elif isinstance(v, float):
            lines.append(f"{key_upper}={v:.7E}")
        elif isinstance(v, int):
            lines.append(f"{key_upper}={v}")
        else:
            lines.append(f"{key_upper}={v}")

    # MUX channels
    if result.channels:
        ch_bits = sum(1 << (ch - 1) for ch in result.channels)
        lines.append(f"USE_MUX_CH={ch_bits}")

    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# PsSessionExporter
# ---------------------------------------------------------------------------


class PsSessionExporter:
    """Exports measurement results to PalmSens .pssession format.

    The .pssession format is a UTF-16 LE encoded JSON file with BOM
    used by PalmSens PSTrace software and compatible with CMU.49.011
    analysis pipelines.

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

        Creates parent directories if they do not exist. The file is
        written with UTF-16 BOM, minified JSON, and trailing BOM to
        match PSTrace output exactly.

        Args:
            result: The measurement result to export.
            output_path: Destination file path.

        Returns:
            Absolute path to the written file.
        """
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        session = self._build_session(result)

        # Write raw bytes: BOM + minified JSON as UTF-16-LE + trailing BOM
        json_str = json.dumps(
            session, separators=(",", ":"), ensure_ascii=False
        )
        with open(output_path, "wb") as f:
            f.write(b"\xff\xfe")  # UTF-16 LE BOM
            f.write(json_str.encode("utf-16-le"))
            f.write("\ufeff".encode("utf-16-le"))  # trailing BOM

        abs_path = os.path.abspath(output_path)
        logger.info("Wrote .pssession file: %s", abs_path)
        return abs_path

    # -- Internal -----------------------------------------------------------

    @staticmethod
    def _build_session(
        result: MeasurementResult,
    ) -> dict[str, Any]:
        """Build the PalmSens-compatible session JSON structure."""
        # Lazy imports to avoid circular dependency
        from src.data.pssession_curves import (
            build_curves_measurement,
        )
        from src.data.pssession_eis import (
            build_eis_measurement,
        )

        technique = result.technique.lower()
        method_id = technique
        if technique in ("ca", "ca_alt_mux", "fca"):
            method_id = "ad"
        method_str = build_method_string(method_id, result)

        title = _TECHNIQUE_TITLES.get(technique, technique.upper())

        if technique in _EIS_TECHNIQUES:
            measurement = build_eis_measurement(
                result, title, method_str
            )
        else:
            measurement = build_curves_measurement(
                result, title, method_str
            )

        session: dict[str, Any] = {
            "Type": "PalmSens.DataFiles.SessionFile",
            "CoreVersion": "5.12.1031.0",
            "MethodForMeasurement": method_str,
            "Measurements": [measurement],
        }
        return session
