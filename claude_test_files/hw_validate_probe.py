"""Stage-1b hardware probe for PR #7 validation (COM6, device on Ch1).

Connects once, then runs a short low-potential CA on CH1 under two
electrode configs to find which one closes the circuit:
  (a) default external mode (RE/CE = 15)
  (b) RE/CE paired to CH1
Reports current (=> implied R), completion latency (the headline
save-prompt fix), and the marker sequence for each.

Run: PYTHONPATH=. python claude_test_files/hw_validate_probe.py
"""

from __future__ import annotations

import logging
import sys
import time

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from src.comms.serial_connection import (  # noqa: E402
    PicoConnection,
    PicoConnectionError,
)
from src.engine.measurement_engine import MeasurementEngine  # noqa: E402
from src.data.models import TechniqueConfig  # noqa: E402

PORT = "COM6"
CH = 1

_logs: list[str] = []


class _Cap(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _logs.append(self.format(record))
        except Exception:
            pass


_cap = _Cap()
_cap.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_cap)
logging.getLogger().setLevel(logging.DEBUG)


def run_ca(conn, label, mode, re_ce):
    params = {
        "e_dc": 0.05,
        "t_run": 2.0,
        "t_interval": 0.1,
        "cr": "100u",
        "bw_hz": 400,
    }
    cfg = TechniqueConfig(
        technique="ca",
        params=params,
        channels=[CH],
        electrode_config_mode=mode,
        re_ce_channels=re_ce if re_ce is not None else [],
    )
    eng = MeasurementEngine()
    eng._connection = conn
    eng._config = cfg
    st = {"last_dp": None, "n": 0, "finished": None, "err": None}
    eng.data_point_ready.connect(
        lambda dp: (st.update(last_dp=time.monotonic()), st.update(n=st["n"] + 1))
    )
    eng.measurement_finished.connect(
        lambda r: st.update(finished=time.monotonic())
    )
    eng.measurement_error.connect(
        lambda m: st.update(err=m, finished=time.monotonic())
    )

    mark0 = len(_logs)
    print(f"\n=== {label} (mode={mode}, re_ce={cfg.re_ce_channels}) ===")
    eng._run_measurement()

    runlogs = _logs[mark0:]
    markers = [
        ln.split("MARKER")[1].strip().split("(")[0].strip()
        for ln in runlogs
        if "MARKER" in ln
    ]
    warned = any("Ended without '+'" in ln for ln in runlogs)
    plus_seen = any("End-of-measurement marker received" in ln for ln in runlogs)

    res = eng.result
    currents = [
        dp.variables["current"]
        for dp in (res.data_points if res else [])
        if "current" in dp.variables
    ]
    print(f"  error: {st['err']}   points: {res.num_points if res else 0}")
    if currents:
        absmean = sum(abs(c) for c in currents) / len(currents)
        print(
            f"  |I|mean={absmean:.3e} A  first={currents[0]:.3e} "
            f"last={currents[-1]:.3e}"
        )
        if absmean > 5e-8:
            print(f"  ==> CLOSED: implied R = {0.05/absmean:,.0f} ohm")
        else:
            print("  ==> OPEN (|I| ~ noise floor)")
    if st["finished"] and st["last_dp"]:
        print(
            f"  completion latency: {st['finished']-st['last_dp']:.2f}s  "
            f"| '+' seen={plus_seen}  fallback={warned}"
        )
    print(f"  markers: {markers}")


def main() -> int:
    conn = PicoConnection()
    try:
        t0 = time.monotonic()
        conn.connect(PORT)
        print(
            f"Connected in {time.monotonic()-t0:.1f}s | "
            f"fw={conn.firmware_version!r}"
        )
    except PicoConnectionError as exc:
        print(f"CONNECT FAILED: {exc}\nFree COM6 (disconnect the GUI) and retry.")
        return 1

    run_ca(conn, "A) default external", "external", None)
    run_ca(conn, "B) RE/CE paired to CH1", "manual", [CH])

    conn.disconnect()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
