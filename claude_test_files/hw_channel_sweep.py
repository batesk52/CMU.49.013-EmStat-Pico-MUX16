"""Diagnostic: short CA on every MUX channel, report per-channel current.

Isolates a wiring fault when a dummy reads open: if the resistor's WE clip
is on a different channel than expected, that channel lights up; if NO
channel carries current, the fault is on the shared RE/CE side.

Usage:  PYTHONPATH=. python claude_test_files/hw_channel_sweep.py [cr]
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from src.comms.serial_connection import (  # noqa: E402
    PicoConnection,
    PicoConnectionError,
)
from src.data.models import TechniqueConfig  # noqa: E402
from src.engine.measurement_engine import MeasurementEngine  # noqa: E402

PORT = "COM6"


def main() -> int:
    cr = sys.argv[1] if len(sys.argv) > 1 else "100u"
    conn = PicoConnection()
    try:
        conn.connect(PORT)
        print(f"Connected on {PORT}  fw={conn.firmware_version!r}\n")
    except PicoConnectionError as exc:
        print(f"CONNECT FAILED: {exc}")
        return 1

    cfg = TechniqueConfig(
        technique="ca",
        params={"e_dc": 0.1, "t_run": 0.8, "t_interval": 0.1, "cr": cr,
                "bw_hz": 400},
        channels=list(range(1, 17)),
        electrode_config_mode="external",
        re_ce_channels=[],
    )
    eng = MeasurementEngine()
    eng._connection = conn
    eng._config = cfg

    by_ch: dict[int, list[float]] = defaultdict(list)
    eng.data_point_ready.connect(
        lambda dp: by_ch[dp.channel].append(
            dp.variables.get("current", float("nan"))
        )
    )
    err = {"m": None}
    eng.measurement_error.connect(lambda m: err.update(m=m))

    print(f"Sweeping CH1..CH16 (e_dc=0.1V, cr={cr}, 0.8s each)...\n")
    t0 = time.monotonic()
    eng._run_measurement()
    print(f"swept in {time.monotonic()-t0:.1f}s  error={err['m']}\n")

    print(f"  {'CH':>3}  {'n':>3}  {'|I|mean':>11}  implied")
    found = []
    for ch in range(1, 17):
        vals = [abs(v) for v in by_ch.get(ch, []) if v == v]  # drop NaN
        if not vals:
            print(f"  {ch:>3}    0           -       (no data)")
            continue
        m = sum(vals) / len(vals)
        if m > 5e-7:  # >0.5uA => meaningfully closed
            r = 0.1 / m
            tag = f"R~{r:,.0f} ohm  <== CLOSED"
            found.append(ch)
        else:
            tag = "open (noise floor)"
        print(f"  {ch:>3}  {len(vals):>3}  {m:>11.3e}  {tag}")

    print()
    if found:
        print(f">> Cell is CLOSED on channel(s): {found}")
    else:
        print(">> NO channel carries current — fault is on the shared RE/CE "
              "side (CE not landing, or RE/CE not on the board common "
              "terminals). Check those clips, not the WE channel.")

    conn.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
