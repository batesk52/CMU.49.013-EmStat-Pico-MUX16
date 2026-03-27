"""Validation script for the rewritten PsSessionExporter.

Creates mock MeasurementResult objects for CV, CA, and EIS, exports
them via PsSessionExporter, then re-reads and validates the output.
"""

import json
import math
import os
import sys
import tempfile

# Ensure project root is on path
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from datetime import datetime

from src.data.models import DataPoint, MeasurementResult
from src.data.pssession_exporter import PsSessionExporter

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record a pass/fail check."""
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


def read_pssession(path: str) -> tuple[bytes, dict]:
    """Read a .pssession file and return raw bytes + parsed JSON."""
    with open(path, "rb") as f:
        raw = f.read()
    # Decode: skip BOM (2 bytes), decode UTF-16-LE
    text = raw[2:].decode("utf-16-le")
    # Strip trailing BOM if present
    if text.endswith("\ufeff"):
        text = text[:-1]
    data = json.loads(text)
    return raw, data


# ---------------------------------------------------------------------------
# Mock data builders
# ---------------------------------------------------------------------------


def make_cv_result() -> MeasurementResult:
    """CV with 3 scans, channels 1 and 4, 30 pts per scan."""
    result = MeasurementResult(
        technique="cv",
        start_time=datetime(2026, 3, 26, 12, 0, 0),
        device_info={"serial": "ES4-12345", "firmware": "1.6"},
        params={
            "e_begin": -0.85,
            "e_vertex1": 0.85,
            "e_vertex2": -0.85,
            "e_step": 0.01,
            "scan_rate": 0.1,
            "n_scans": 3,
        },
        channels=[1, 4],
    )
    n_scans = 3
    pts_per_scan = 30
    for ch in [1, 4]:
        for scan in range(n_scans):
            for i in range(pts_per_scan):
                t = (
                    scan * pts_per_scan + i
                ) * 0.05 + (ch - 1) * 0.001
                pot = -0.85 + i * 0.01
                cur = (pot * 1e-5) + (ch * 1e-6)
                result.add_point(
                    DataPoint(
                        timestamp=t,
                        channel=ch,
                        variables={
                            "set_potential": pot,
                            "current": cur,
                        },
                    )
                )
    return result


def make_ca_result() -> MeasurementResult:
    """CA with channels 1 and 4, 20 pts each."""
    result = MeasurementResult(
        technique="ca_alt_mux",
        start_time=datetime(2026, 3, 26, 12, 5, 0),
        device_info={"serial": "ES4-12345", "firmware": "1.6"},
        params={"e_dc": 0.7, "t_interval": 0.5, "t_run": 10.0},
        channels=[1, 4],
    )
    for ch in [1, 4]:
        for i in range(20):
            t = 2.0 + i * 0.5 + (ch - 1) * 0.001
            cur = 5e-6 * math.exp(-i * 0.1) * ch
            result.add_point(
                DataPoint(
                    timestamp=t,
                    channel=ch,
                    variables={
                        "set_potential": 0.7,
                        "current": cur,
                    },
                )
            )
    return result


def make_eis_result() -> MeasurementResult:
    """EIS with channels 1 and 4, 10 frequencies each."""
    result = MeasurementResult(
        technique="eis",
        start_time=datetime(2026, 3, 26, 12, 10, 0),
        device_info={"serial": "ES4-12345", "firmware": "1.6"},
        params={
            "freq_start": 100000.0,
            "freq_end": 0.1,
            "e_dc": 0.0,
            "e_ac": 0.01,
        },
        channels=[1, 4],
    )
    freqs = [100000, 50000, 10000, 5000, 1000, 500, 100, 50, 10, 1]
    for ch in [1, 4]:
        for i, f in enumerate(freqs):
            zr = 100.0 + i * 50.0 + ch * 10.0
            zi = -(50.0 + i * 30.0 + ch * 5.0)
            result.add_point(
                DataPoint(
                    timestamp=float(i),
                    channel=ch,
                    variables={
                        "set_frequency": float(f),
                        "zreal": zr,
                        "zimag": zi,
                    },
                )
            )
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cv(tmpdir: str) -> None:
    """Validate CV export."""
    print("\n=== CV Export ===")
    result = make_cv_result()
    path = os.path.join(tmpdir, "cv_test.pssession")
    exporter = PsSessionExporter()
    exporter.export_pssession(result, path)

    raw, data = read_pssession(path)

    # Encoding checks
    check("BOM present", raw[:2] == b"\xff\xfe")
    check(
        "Minified JSON (no newlines)",
        b"\n" not in raw[2:-4],
    )
    check(
        "Trailing BOM",
        raw[-2:] == "\ufeff".encode("utf-16-le"),
    )

    # Structure checks
    check(
        "Top Type",
        data["Type"] == "PalmSens.DataFiles.SessionFile",
    )
    check("CoreVersion", data["CoreVersion"] == "5.12.1031.0")

    meas = data["Measurements"][0]
    check("Measurement Title", meas["Title"] == "Cyclic Voltammetry")
    check(
        "Measurement Type",
        meas["Type"] == "PalmSens.Comm.GenericCommMeasurement",
    )
    check("DeviceUsed", meas["DeviceUsed"] == 9)
    check("TimeStamp present", meas["TimeStamp"] > 0)
    check("UTCTimeStamp present", meas["UTCTimeStamp"] > 0)
    check("EISDataList empty", meas["EISDataList"] == [])

    # CV should have 6 curves (3 scans x 2 channels)
    check(
        "6 curves (3 scans x 2 ch)",
        len(meas["Curves"]) == 6,
        f"got {len(meas['Curves'])}",
    )

    if meas["Curves"]:
        c0 = meas["Curves"][0]
        check(
            "Curve title format",
            "CV i vs E Scan 1 Channel 1" in c0["Title"],
        )
        check(
            "Curve has Appearance",
            "Type" in c0.get("Appearance", {}),
        )
        check("Curve has Hash (48)", len(c0.get("Hash", [])) == 48)
        check(
            "Curve Type",
            c0["Type"] == "PalmSens.Plottables.Curve",
        )
        check("XAxis is int 0", c0["XAxis"] == 0)
        check("YAxis is int 0", c0["YAxis"] == 0)
        check("MeasType first=1", c0["MeasType"] == 1)
        check(
            "CorrosionButlerVolmer",
            c0["CorrosionButlerVolmer"] == [0, 0],
        )

        # Check current is in µA range
        ya = c0["YAxisDataArray"]
        check(
            "Y Unit is MicroAmpere",
            ya["Unit"]["Type"] == "PalmSens.Units.MicroAmpere",
        )
        check(
            "Y ArrayType=2",
            ya["ArrayType"] == 2,
        )
        check(
            "Y DataValueType=CurrentReading",
            ya["DataValueType"] == "PalmSens.Data.CurrentReading",
        )
        # YAxisDataArray Type in Curves should be generic DataArray
        check(
            "Y Type in Curve is DataArray",
            ya["Type"] == "PalmSens.Data.DataArray",
        )
        # Current values should be in µA range (not 1e-6)
        first_v = ya["DataValues"][0]["V"]
        check(
            "Current in µA range",
            abs(first_v) > 1e-3,
            f"V={first_v}",
        )
        # Current DataValues should have C and S fields
        check(
            "Current has C field",
            "C" in ya["DataValues"][0],
        )

        # X axis checks
        xa = c0["XAxisDataArray"]
        check(
            "X ArrayType=1 (potential)",
            xa["ArrayType"] == 1,
        )
        check(
            "X Description is channel name",
            xa["Description"] == "channel1",
        )
        check(
            "Potential has S and R fields",
            "S" in xa["DataValues"][0]
            and "R" in xa["DataValues"][0],
        )

    # DataSet checks
    ds = meas.get("DataSet", {})
    check(
        "DataSet present",
        ds.get("Type") == "PalmSens.Data.DataSetCommon",
    )
    check(
        "DataSet has Values",
        len(ds.get("Values", [])) > 0,
        f"got {len(ds.get('Values', []))} arrays",
    )
    if ds.get("Values"):
        # First array should be time
        check(
            "DataSet[0] is time array",
            ds["Values"][0]["Type"]
            == "PalmSens.Data.DataArrayTime",
        )
        # DataSet current arrays should use DataArrayCurrents
        cur_arrays = [
            a
            for a in ds["Values"]
            if a["ArrayType"] == 2
        ]
        if cur_arrays:
            check(
                "DataSet current uses DataArrayCurrents",
                cur_arrays[0]["Type"]
                == "PalmSens.Data.DataArrayCurrents",
            )


def test_ca(tmpdir: str) -> None:
    """Validate CA export."""
    print("\n=== CA Export ===")
    result = make_ca_result()
    path = os.path.join(tmpdir, "ca_test.pssession")
    exporter = PsSessionExporter()
    exporter.export_pssession(result, path)

    raw, data = read_pssession(path)

    check("BOM present", raw[:2] == b"\xff\xfe")

    meas = data["Measurements"][0]
    check(
        "Title is Chronoamperometry",
        meas["Title"] == "Chronoamperometry",
    )
    check(
        "2 curves (2 channels)",
        len(meas["Curves"]) == 2,
        f"got {len(meas['Curves'])}",
    )

    if meas["Curves"]:
        c0 = meas["Curves"][0]
        check(
            "CA title format",
            "CA i vs t Channel" in c0["Title"],
        )
        # Time should be zero-based
        xa = c0["XAxisDataArray"]
        check(
            "X is time array",
            xa["Type"] == "PalmSens.Data.DataArrayTime",
        )
        first_t = xa["DataValues"][0]["V"]
        check(
            "Time is zero-based",
            abs(first_t) < 0.01,
            f"first_t={first_t}",
        )

        # Current in µA
        ya = c0["YAxisDataArray"]
        first_v = ya["DataValues"][0]["V"]
        check(
            "Current in µA range",
            abs(first_v) > 0.001,
            f"V={first_v}",
        )


def test_eis(tmpdir: str) -> None:
    """Validate EIS export."""
    print("\n=== EIS Export ===")
    result = make_eis_result()
    path = os.path.join(tmpdir, "eis_test.pssession")
    exporter = PsSessionExporter()
    exporter.export_pssession(result, path)

    raw, data = read_pssession(path)

    check("BOM present", raw[:2] == b"\xff\xfe")

    meas = data["Measurements"][0]
    check(
        "EIS Title",
        meas["Title"] == "Impedance Spectroscopy",
    )
    check(
        "EIS Type",
        meas["Type"]
        == "PalmSens.Techniques.ImpedimetricMeasurement",
    )
    check("Curves empty", meas["Curves"] == [])
    check(
        "EISDataList has 2 items",
        len(meas["EISDataList"]) == 2,
        f"got {len(meas['EISDataList'])}",
    )

    if meas["EISDataList"]:
        e0 = meas["EISDataList"][0]
        check(
            "EIS item Type",
            e0["Type"] == "PalmSens.Plottables.EISData",
        )
        check("ScanType=2", e0["ScanType"] == 2)
        check("FreqType=1", e0["FreqType"] == 1)
        check("CDC is None", e0["CDC"] is None)
        check("FitValues empty", e0["FitValues"] == [])
        check(
            "Title format",
            "CH 1: 10 freqs" in e0["Title"],
        )
        check("Hash length=48", len(e0.get("Hash", [])) == 48)

        # AppearanceFrequencySubScanCurves
        afssc = e0["AppearanceFrequencySubScanCurves"]
        check(
            "SubScanCurves count=10",
            len(afssc) == 10,
            f"got {len(afssc)}",
        )
        if afssc:
            check(
                "SubScanCurves entry has 2 appearances",
                len(afssc[0]) == 2,
            )

        # DataSet
        ds = e0.get("DataSet", {})
        check(
            "DataSet type is DataSetEIS",
            ds.get("Type") == "PalmSens.Data.DataSetEIS",
        )
        vals = ds.get("Values", [])
        check(
            "DataSet has 22 arrays",
            len(vals) == 22,
            f"got {len(vals)}",
        )

        if len(vals) >= 22:
            # Check array order
            check(
                "[0] Idc",
                vals[0]["Description"] == "Idc",
            )
            check(
                "[3] Frequency",
                vals[3]["Description"] == "Frequency",
            )
            check("[4] ZRe", vals[4]["Description"] == "ZRe")
            check("[5] ZIm", vals[5]["Description"] == "ZIm")
            check("[6] Z", vals[6]["Description"] == "Z")
            check("[7] Phase", vals[7]["Description"] == "Phase")
            check("[16] Y", vals[16]["Description"] == "Y")
            check(
                "[19] Capacitance",
                vals[19]["Description"] == "Capacitance",
            )
            check(
                "[21] Capacitance''",
                vals[21]["Description"] == "Capacitance''",
            )

            # Check ZRe unit
            check(
                "ZRe unit type",
                vals[4]["Unit"]["Type"]
                == "PalmSens.Units.ZRe",
            )
            # Check admittance array type
            check(
                "Y array type is DataArrayAdmittance",
                vals[16]["Type"]
                == "PalmSens.Data.DataArrayAdmittance",
            )
            # Check capacitance array type
            check(
                "Capacitance type is DataArrayCustomFunc",
                vals[19]["Type"]
                == "PalmSens.Data.DataArrayCustomFunc",
            )

    # Measurement-level DataSet
    mds = meas.get("DataSet", {})
    check(
        "Measurement DataSet is DataSetEIS",
        mds.get("Type") == "PalmSens.Data.DataSetEIS",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        test_cv(tmpdir)
        test_ca(tmpdir)
        test_eis(tmpdir)

    print(f"\n{'='*40}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print(f"{'='*40}")

    if FAIL > 0:
        sys.exit(1)
    else:
        print("All checks passed!")
        sys.exit(0)
