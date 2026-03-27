"""Re-export measurement CSVs to .pssession using PsSessionExporter.

Reads the 3 real measurement export directories (CV, EIS, CA), reconstructs
MeasurementResult objects from their CSV files, and exports each to
.pssession in exports/exports_new/ for comparison with the old exports.
"""

import csv
import os
import sys
from datetime import datetime

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from src.data.models import DataPoint, MeasurementResult
from src.data.pssession_exporter import PsSessionExporter


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_csv(path: str, channel: int) -> tuple[list[DataPoint], dict[str, str]]:
    """Parse a CSV file with # metadata headers into DataPoints + metadata."""
    points = []
    metadata = {}
    header_line = None

    with open(path, "r") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if line.startswith("#"):
                # Metadata line: "# key: value"
                content = line.lstrip("#").strip()
                if ":" in content:
                    key, val = content.split(":", 1)
                    metadata[key.strip()] = val.strip()
                continue
            # First non-comment line is the column header
            header_line = line
            break

        if header_line is None:
            return points, metadata

        fieldnames = [f.strip() for f in header_line.split(",")]
        reader = csv.DictReader(f, fieldnames=fieldnames)
        for row in reader:
            ts = float(row["timestamp"])
            variables = {}
            for k, v in row.items():
                if k != "timestamp" and v:
                    try:
                        variables[k] = float(v)
                    except ValueError:
                        pass
            points.append(DataPoint(timestamp=ts, channel=channel, variables=variables))

    return points, metadata


def parse_params(param_str: str) -> dict[str, object]:
    """Parse 'key=val, key=val' into dict with numeric coercion."""
    params = {}
    for item in param_str.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Try int, then float, then keep as string
        try:
            # Check if it looks like an int (no decimal point, no e/E)
            if "." not in v and "e" not in v.lower():
                params[k] = int(v)
            else:
                params[k] = float(v)
        except ValueError:
            params[k] = v
    return params


def build_measurement_result(
    csv_dir: str, channels: list[int]
) -> MeasurementResult:
    """Build a MeasurementResult from per-channel CSVs in a directory."""
    all_points = []
    metadata = {}

    for ch in channels:
        csv_path = os.path.join(csv_dir, f"ch{ch:02d}.csv")
        if not os.path.exists(csv_path):
            print(f"  WARNING: {csv_path} not found, skipping")
            continue
        points, meta = parse_csv(csv_path, channel=ch)
        all_points.extend(points)
        if not metadata:
            metadata = meta  # Use first channel's metadata
        print(f"  Parsed {csv_path}: {len(points)} points")

    # Extract fields from metadata
    technique = metadata.get("Technique", "unknown")
    timestamp_str = metadata.get("Timestamp", "")
    device_serial = metadata.get("Device Serial", "")
    firmware = metadata.get("Firmware Version", "")
    param_str = metadata.get("Parameters", "")

    # Parse start time
    start_time = None
    if timestamp_str:
        try:
            start_time = datetime.fromisoformat(timestamp_str)
        except ValueError:
            print(f"  WARNING: Could not parse timestamp: {timestamp_str}")

    # Parse parameters
    params = parse_params(param_str) if param_str else {}

    # Build device info
    device_info = {}
    if device_serial:
        device_info["serial"] = device_serial
    if firmware:
        device_info["firmware"] = firmware

    result = MeasurementResult(
        data_points=all_points,
        technique=technique,
        start_time=start_time,
        device_info=device_info,
        params=params,
        channels=channels,
    )
    print(f"  Result: technique={result.technique}, {result.num_points} points, "
          f"channels={result.measured_channels}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    export_base = os.path.join(PROJECT_ROOT, "exports", "exports")
    output_dir = os.path.join(PROJECT_ROOT, "exports", "exports_new")
    os.makedirs(output_dir, exist_ok=True)

    exporter = PsSessionExporter()

    # --- CV ---
    print("\n=== CV ===")
    cv_dir = os.path.join(export_base, "20260326_163103_cv_ch1_4_multiplex_for_final_compare")
    cv_result = build_measurement_result(cv_dir, channels=[1, 4])
    cv_path = exporter.export_pssession(cv_result, os.path.join(output_dir, "cv_ch1_4.pssession"))
    print(f"  Exported: {cv_path}")

    # --- EIS ---
    print("\n=== EIS ===")
    eis_dir = os.path.join(export_base, "20260326_163439_eis_ch1_4_multiplex_for_final_compare")
    eis_result = build_measurement_result(eis_dir, channels=[1, 4])
    eis_path = exporter.export_pssession(eis_result, os.path.join(output_dir, "eis_ch1_4.pssession"))
    print(f"  Exported: {eis_path}")

    # --- CA ---
    print("\n=== CA ===")
    ca_dir = os.path.join(export_base, "20260326_165644_ca_alt_mux")
    ca_result = build_measurement_result(ca_dir, channels=[1, 4])
    ca_path = exporter.export_pssession(ca_result, os.path.join(output_dir, "ca_alt_mux_ch1_4.pssession"))
    print(f"  Exported: {ca_path}")

    # --- Summary ---
    print("\n=== Output Files ===")
    for fname in sorted(os.listdir(output_dir)):
        fpath = os.path.join(output_dir, fname)
        size = os.path.getsize(fpath)
        print(f"  {fname}: {size:,} bytes ({size/1024:.1f} KB)")


if __name__ == "__main__":
    main()
