"""Stage-2 hardware validation for PR #7 (COM6, 8.6k resistor on Ch1).

Default external config (RE/CE=15) closes the circuit. Runs:
  T-CA  : 20s CA, auto-save on -> data integrity (no drops) + incr==final
  T-CV  : closed-cycle CV, n_scans=2 -> both sweeps present + markers (C1)
  T-MUX : ca_alt_mux (device-side loop) -> E5 marker probe (expect no C/-)
  T-EIS : EIS on resistor -> E4 (bare-int accept) + Nyquist ~ (8.6k, 0)
Reports per-test; safe params (<=0.1 V, 100 uA range, ~12 uA).
"""

from __future__ import annotations

import csv
import glob
import logging
import os
import sys
import time

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from src.comms.serial_connection import (  # noqa: E402
    PicoConnection,
    PicoConnectionError,
)
from src.engine.measurement_engine import MeasurementEngine  # noqa: E402
from src.data.models import AutoSaveConfig, TechniqueConfig  # noqa: E402

PORT = "COM6"
CH = 1
EXPORT = os.path.join("exports", "_hw_validate_stage2")

_logs: list[str] = []


class _Cap(logging.Handler):
    def emit(self, record):
        try:
            _logs.append(self.format(record))
        except Exception:
            pass


_cap = _Cap()
_cap.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_cap)
logging.getLogger().setLevel(logging.DEBUG)


def _run(conn, technique, params, label, auto_dir=None):
    cfg = TechniqueConfig(
        technique=technique,
        params=params,
        channels=[CH],
        auto_save=AutoSaveConfig(enabled=True, output_dir=auto_dir)
        if auto_dir
        else None,
    )
    eng = MeasurementEngine()
    eng._connection = conn
    eng._config = cfg
    st = {"last_dp": None, "finished": None, "err": None}
    eng.data_point_ready.connect(lambda dp: st.update(last_dp=time.monotonic()))
    eng.measurement_finished.connect(lambda r: st.update(finished=time.monotonic()))
    eng.measurement_error.connect(
        lambda m: st.update(err=m, finished=time.monotonic())
    )
    mark0 = len(_logs)
    print(f"\n=== {label} ===")
    eng._run_measurement()
    runlogs = _logs[mark0:]
    markers = [
        ln.split("MARKER")[1].strip().split("(")[0].strip()
        for ln in runlogs
        if "MARKER" in ln
    ]
    lat = (
        st["finished"] - st["last_dp"]
        if st["finished"] and st["last_dp"]
        else None
    )
    res = eng.result
    print(
        f"  err={st['err']} points={res.num_points if res else 0} "
        f"latency={lat:.2f}s" if lat else f"  err={st['err']}"
    )
    print(f"  markers={markers}")
    return eng.result


def main() -> int:
    conn = PicoConnection()
    try:
        conn.connect(PORT)
        print(f"Connected fw={conn.firmware_version!r}")
    except PicoConnectionError as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    # T-CA: data integrity + auto-save incremental==final
    res = _run(
        conn,
        "ca",
        {"e_dc": 0.1, "t_run": 20.0, "t_interval": 0.1, "cr": "100u", "bw_hz": 400},
        "T-CA 20s (expect ~200 pts, ~12uA steady)",
        auto_dir=EXPORT,
    )
    if res:
        cur = [dp.variables.get("current") for dp in res.data_points]
        cur = [c for c in cur if c is not None]
        if cur:
            am = sum(abs(c) for c in cur) / len(cur)
            print(f"  |I|mean={am:.3e} A  R={0.1/am:,.0f} ohm")
        # incremental CSV row count vs result
        files = glob.glob(os.path.join(EXPORT, "*", f"ch{CH:02d}.csv"))
        if files:
            with open(files[-1], newline="") as fh:
                rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")]
            print(
                f"  incremental CSV data rows={len(rows)-1} vs result "
                f"points={res.num_points}  "
                f"{'MATCH' if len(rows)-1 == res.num_points else 'MISMATCH'}"
            )

    # T-CV: closed cycle, both sweeps present (C1)
    res = _run(
        conn,
        "cv",
        {
            "e_begin": -0.1, "e_vertex1": 0.1, "e_vertex2": -0.1,
            "e_step": 0.01, "scan_rate": 0.1, "n_scans": 2,
            "cr": "100u", "bw_hz": 400,
        },
        "T-CV closed-cycle n_scans=2 (C1: both sweeps)",
    )
    if res:
        pots = [dp.variables.get("set_potential") for dp in res.data_points]
        pots = [p for p in pots if p is not None]
        if pots:
            print(
                f"  set_potential range: min={min(pots):.3f} max={max(pots):.3f} "
                f"(both sweeps present if it rises AND falls)"
            )

    # T-MUX: ca_alt_mux device-side loop -> E5 marker probe
    res = _run(
        conn,
        "ca_alt_mux",
        {
            "e_dc": 0.1, "t_run": 8.0, "t_interval": 0.5, "settle_time": 0.1,
            "samples_per_visit": 1, "cr": "100u", "bw_hz": 400,
        },
        "T-MUX ca_alt_mux (E5: scan markers?)",
    )

    # T-EIS: bare-int accept (E4) + Nyquist ~ (8.6k, 0)
    res = _run(
        conn,
        "eis",
        {
            "freq_start": 10000.0, "freq_end": 10.0, "n_freq": 8,
            "e_dc": 0.0, "e_ac": 0.01, "cr": "100u",
        },
        "T-EIS 10kHz->10Hz n_freq=8 (E4 + resistor Nyquist)",
    )
    if res:
        zr = [dp.variables.get("zreal") for dp in res.data_points]
        zi = [dp.variables.get("zimag") for dp in res.data_points]
        zr = [z for z in zr if z is not None]
        zi = [z for z in zi if z is not None]
        if zr:
            print(
                f"  zreal: {min(zr):.0f}..{max(zr):.0f} ohm  "
                f"zimag: {min(zi):.0f}..{max(zi):.0f} ohm  "
                f"(resistor => zreal~8.6k, zimag~0)"
            )

    conn.disconnect()
    print("\nStage-2 complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
