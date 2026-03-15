"""Validation script for src/data/exporters.py."""

import json
import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from src.data.exporters import (
    CSVExporter,
    PsSessionExporter,
    make_export_dir,
)
from src.data.models import DataPoint, MeasurementResult


def _make_result():
    """Create a test MeasurementResult with 2 channels."""
    result = MeasurementResult(
        technique="cv",
        start_time=datetime(2026, 3, 15, 12, 0, 0),
        device_info={"serial": "ESP1234", "firmware": "1.6.0"},
        params={"e_begin": -0.5, "e_end": 0.5, "scan_rate": 0.1},
        channels=[1, 3],
    )
    # Channel 1: 3 data points
    for i in range(3):
        result.add_point(
            DataPoint(
                timestamp=float(i),
                channel=1,
                variables={
                    "set_potential": -0.5 + i * 0.5,
                    "current": 1e-6 * (i + 1),
                    "measured_potential": -0.49 + i * 0.5,
                },
            )
        )
    # Channel 3: 2 data points
    for i in range(2):
        result.add_point(
            DataPoint(
                timestamp=float(i),
                channel=3,
                variables={
                    "set_potential": -0.5 + i * 0.5,
                    "current": 2e-6 * (i + 1),
                },
            )
        )
    return result


def test_csv_export():
    """Test CSVExporter produces correct files."""
    result = _make_result()
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = CSVExporter()
        paths = exporter.export_csv(result, tmpdir)
        assert len(paths) == 2, f"Expected 2 files, got {len(paths)}"

        # Check ch01.csv
        ch01 = os.path.join(tmpdir, "ch01.csv")
        assert os.path.exists(ch01), "ch01.csv missing"
        with open(ch01, "r") as f:
            lines = f.readlines()
        # Metadata lines start with #
        meta_lines = [l for l in lines if l.startswith("#")]
        assert len(meta_lines) >= 4, (
            f"Expected >=4 metadata lines, got {len(meta_lines)}"
        )
        # Check technique in header
        assert any("Technique: cv" in l for l in meta_lines)
        assert any("Device Serial: ESP1234" in l for l in meta_lines)
        assert any("Firmware Version: 1.6.0" in l for l in meta_lines)
        # Check data header row
        data_lines = [l for l in lines if not l.startswith("#")]
        header = data_lines[0].strip()
        assert header.startswith("timestamp"), f"Bad header: {header}"
        # CV columns: set_potential, measured_potential, current
        assert "set_potential" in header
        assert "current" in header
        # 3 data rows for ch1
        data_rows = [l for l in data_lines[1:] if l.strip()]
        assert len(data_rows) == 3, (
            f"Expected 3 data rows, got {len(data_rows)}"
        )

        # Check ch03.csv
        ch03 = os.path.join(tmpdir, "ch03.csv")
        assert os.path.exists(ch03), "ch03.csv missing"
        with open(ch03, "r") as f:
            lines = f.readlines()
        data_lines = [l for l in lines if not l.startswith("#")]
        data_rows = [l for l in data_lines[1:] if l.strip()]
        assert len(data_rows) == 2, (
            f"Expected 2 data rows for ch3, got {len(data_rows)}"
        )

        # Test export() alias
        paths2 = exporter.export(result, tmpdir)
        assert len(paths2) == 2, "export() alias failed"

    print("  CSV export: PASS")


def test_pssession_export():
    """Test PsSessionExporter produces valid UTF-16 JSON."""
    result = _make_result()
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "test.pssession")
        exporter = PsSessionExporter()
        path = exporter.export_pssession(result, output)
        assert os.path.exists(path), ".pssession file missing"

        # Read as UTF-16 LE
        with open(path, "r", encoding="utf-16-le") as f:
            content = f.read()
        session = json.loads(content)

        # Verify structure
        assert session["Version"] == "1.0"
        assert session["Technique"] == "CV"
        assert session["DeviceInfo"]["Serial"] == "ESP1234"
        assert session["DeviceInfo"]["FirmwareVersion"] == "1.6.0"
        assert session["StartTime"] == "2026-03-15T12:00:00"
        assert len(session["Measurements"]) == 2
        assert session["Measurements"][0]["Channel"] == 1
        assert session["Measurements"][0]["NDataPoints"] == 3
        assert session["Measurements"][1]["Channel"] == 3
        assert session["Measurements"][1]["NDataPoints"] == 2
        # Check curves exist
        curves = session["Measurements"][0]["Curves"]
        curve_titles = [c["Title"] for c in curves]
        assert "set_potential" in curve_titles
        assert "current" in curve_titles

    print("  PsSession export: PASS")


def test_make_export_dir():
    """Test timestamped directory creation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = make_export_dir(tmpdir, "cv")
        assert os.path.isdir(path), "Export dir not created"
        dirname = os.path.basename(path)
        assert dirname.endswith("_cv"), (
            f"Expected dir ending with _cv, got {dirname}"
        )
        # Check timestamp format (YYYYMMDD_HHMMSS)
        parts = dirname.rsplit("_", 1)
        assert len(parts[0]) == 15, (
            f"Expected 15-char timestamp, got {len(parts[0])}"
        )

    print("  make_export_dir: PASS")


def test_eis_column_order():
    """Test EIS technique gets impedance columns."""
    result = MeasurementResult(
        technique="eis",
        start_time=datetime(2026, 3, 15, 12, 0, 0),
        channels=[1],
    )
    result.add_point(
        DataPoint(
            timestamp=0.0,
            channel=1,
            variables={
                "frequency": 1000.0,
                "impedance": 500.0,
                "impedance_real": 400.0,
                "impedance_imaginary": -300.0,
                "phase": -36.87,
            },
        )
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        exporter = CSVExporter()
        exporter.export_csv(result, tmpdir)
        with open(os.path.join(tmpdir, "ch01.csv"), "r") as f:
            lines = f.readlines()
        data_lines = [l for l in lines if not l.startswith("#")]
        header = data_lines[0].strip().split(",")
        # First column is timestamp, then EIS columns in order
        assert header[1] == "frequency", (
            f"Expected frequency first, got {header[1]}"
        )
        assert header[2] == "impedance"
        assert header[3] == "impedance_real"
        assert header[4] == "impedance_imaginary"
        assert header[5] == "phase"

    print("  EIS column order: PASS")


if __name__ == "__main__":
    print("Validating exporters...")
    test_csv_export()
    test_pssession_export()
    test_make_export_dir()
    test_eis_column_order()
    print("All validations PASSED")
