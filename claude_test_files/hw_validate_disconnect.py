"""Live bench harness for PR #12 (CMU.17.035) — disconnected RE/CE guard.

Runs one CA on CH1 through the REAL MeasurementEngine path so the actual
ship behaviour is exercised: ElectrodeHealthMonitor.observe -> trip ->
cell_off -> measurement_error. The engine's diagnostic capture is enabled
(EMSTAT_RAW_CAPTURE) and a background thread tails it so you watch the
engine's own ``consecutive_overload`` count climb live as you pull an
electrode. After the run it parses the capture and prints a verdict:
which signal the device actually produced (nan / overload-status / device
error) and how many points it took to trip — the data needed to confirm or
right-size DEFAULT_DISCONNECT_RUN before merge.

Usage (one labelled run per invocation):
    PYTHONPATH=. python claude_test_files/hw_validate_disconnect.py <label> [ch] [cr] [t_run]

Examples:
    ... hw_validate_disconnect.py run1_baseline 3        # untouched baseline, CH3
    ... hw_validate_disconnect.py run2_pull_ce  3        # pull CE ~5 s in
    ... hw_validate_disconnect.py run3_pull_re  3        # pull RE ~5 s in
    ... hw_validate_disconnect.py run4_sensitive 3 10u   # one notch too sensitive
"""

from __future__ import annotations

import os
import sys
import threading
import time

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from src.comms.electrode_health import DEFAULT_DISCONNECT_RUN  # noqa: E402
from src.comms.protocol import STATUS_OVERLOAD, STATUS_UNDERLOAD  # noqa: E402

_SI = {"a": -18, "f": -15, "p": -12, "n": -9, "u": -6, "m": -3,
       " ": 0, "k": 3, "M": 6}
from src.comms.serial_connection import (  # noqa: E402
    PicoConnection,
    PicoConnectionError,
)
from src.data.models import TechniqueConfig  # noqa: E402
from src.engine.measurement_engine import MeasurementEngine  # noqa: E402

PORT = "COM6"
CH = 1
CAPTURE_DIR = "bench_captures"


# --------------------------------------------------------------------------
# Raw-packet signal classification (mirrors protocol._parse_variable)
# --------------------------------------------------------------------------
def _decode_current(main: str) -> float:
    """Decode a ``ba<hex7><prefix>`` current field to amps (NaN-safe)."""
    if len(main) < 10:
        return float("nan")
    hexs, pre = main[2:9], main[9]
    if "nan" in (hexs + pre).lower():
        return float("nan")
    try:
        return (int(hexs, 16) - 2 ** 27) * (10 ** _SI.get(pre, 0))
    except ValueError:
        return float("nan")


def classify_rx(payload: str) -> dict:
    """Return the disconnect-signature flags present in one raw RX line.

    Keys: nan, overload, underload, device_error (bools) and current (A).
    overload/underload = any variable carries a ``,1<hex>`` status field
    with the STATUS_OVERLOAD (0x0002) / STATUS_UNDERLOAD (0x0004) bit set
    (e.g. the ``,10002`` the PR comment anticipated, or the ``,10004`` we
    actually observe on a disconnect).
    """
    flags = {"nan": False, "overload": False, "underload": False,
             "device_error": False, "current": None}
    line = payload.strip()
    if not line:
        return flags
    if line.startswith("!"):
        flags["device_error"] = True
        return flags
    if "nan" in line.lower():
        flags["nan"] = True
    body = line[1:] if line[:1] in ("P", "p") else line
    for var in body.split(";"):
        parts = var.split(",")
        if parts[0].startswith("ba"):
            flags["current"] = _decode_current(parts[0])
        for meta in parts[1:]:
            meta = meta.strip()
            if meta.startswith("1"):
                try:
                    st = int(meta[1:], 16)
                except ValueError:
                    continue
                if st & STATUS_OVERLOAD:
                    flags["overload"] = True
                if st & STATUS_UNDERLOAD:
                    flags["underload"] = True
    return flags


# --------------------------------------------------------------------------
# Background tail of the engine's capture file -> live terminal readout
# --------------------------------------------------------------------------
class CaptureTail(threading.Thread):
    def __init__(self, path: str, threshold: int) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.threshold = threshold
        self._stop = threading.Event()
        self.tripped_at = None  # consecutive count when threshold first hit
        self.in_underload = False
        self.underload_seen = False

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # Wait for the engine to create the file.
        t0 = time.monotonic()
        while not os.path.exists(self.path):
            if self._stop.is_set() or time.monotonic() - t0 > 15:
                return
            time.sleep(0.05)
        last_beat = time.monotonic()
        healthy_points = 0
        announced_run = -1
        try:
            fh = open(self.path, "r", encoding="utf-8")
        except OSError as exc:
            print(f"  [tail] could not open capture: {exc}")
            return
        with fh:
            while not self._stop.is_set():
                line = fh.readline()
                if not line:
                    # Heartbeat so the operator sees the run is alive.
                    now = time.monotonic()
                    if now - last_beat > 2.0:
                        if self.in_underload:
                            print(f"  ... current collapsed / underload "
                                  f"(points={healthy_points})")
                        else:
                            print(f"  ... cell healthy  "
                                  f"(points={healthy_points})")
                        last_beat = now
                    time.sleep(0.05)
                    continue
                line = line.rstrip("\n")
                if "  HEALTH  " in line:
                    n = _extract_int(line, "consecutive_overload=")
                    if n is None:
                        continue
                    if n == 0:
                        healthy_points += 1
                        announced_run = -1
                    elif n != announced_run:
                        announced_run = n
                        bar = "#" * min(n, self.threshold)
                        print(f"  >> OVERLOAD RUN = {n:2d} {bar}")
                        last_beat = time.monotonic()
                        if n >= self.threshold and self.tripped_at is None:
                            self.tripped_at = n
                            print(
                                f"  *** TRIP at {n} consecutive — engine will "
                                f"cell_off + error ***"
                            )
                elif "  RX  " in line:
                    payload = line.split("  RX  ", 1)[1]
                    f = classify_rx(payload)
                    if f["device_error"]:
                        print(f"  RX device-error: {payload[:80]}")
                    elif f["nan"] or f["overload"]:
                        tag = ",".join(
                            k for k in ("nan", "overload") if f[k]
                        )
                        print(f"  RX [{tag}]: {payload[:80]}")
                    elif f["underload"]:
                        if not self.in_underload:
                            self.in_underload = True
                            self.underload_seen = True
                            cur = f["current"]
                            amps = (f"{cur * 1e9:.1f} nA"
                                    if cur is not None else "?")
                            print(f"  !! CURRENT COLLAPSED -> {amps}, "
                                  f"UNDERLOAD (0x4) — disconnect signature; "
                                  f"the guard does NOT key on this")
                            last_beat = time.monotonic()
                    elif f["current"] is not None and self.in_underload:
                        # A healthy finite current after a collapse.
                        self.in_underload = False
                        print("  .. current recovered to healthy")


def _extract_int(line: str, key: str):
    i = line.find(key)
    if i < 0:
        return None
    rest = line[i + len(key):].strip().split()[0]
    try:
        return int(rest)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Post-run analysis of the full capture file
# --------------------------------------------------------------------------
def analyse(path: str, threshold: int) -> None:
    if not os.path.exists(path):
        print("  (no capture file written)")
        return
    rx, health = [], []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            if "  RX  " in raw:
                rx.append(raw.rstrip("\n").split("  RX  ", 1)[1])
            elif "  HEALTH  " in raw:
                n = _extract_int(raw, "consecutive_overload=")
                if n is not None:
                    health.append(n)

    total = len(health)
    max_run = max(health) if health else 0
    tripped = max_run >= threshold
    # Tally signatures across all RX lines.
    nan_ct = ovl_ct = und_ct = err_ct = 0
    first_guard_idx = None   # first overload/nan/error (what the guard sees)
    first_event_idx = None   # first ANY abnormality incl. underload
    for idx, payload in enumerate(rx):
        f = classify_rx(payload)
        if f["nan"]:
            nan_ct += 1
        if f["overload"]:
            ovl_ct += 1
        if f["underload"]:
            und_ct += 1
        if f["device_error"]:
            err_ct += 1
        if first_guard_idx is None and (
            f["nan"] or f["overload"] or f["device_error"]
        ):
            first_guard_idx = idx
        if first_event_idx is None and (
            f["nan"] or f["overload"] or f["device_error"] or f["underload"]
        ):
            first_event_idx = idx

    print("\n  ---- capture summary ----")
    print(f"  health points logged : {total}")
    print(f"  max consecutive run  : {max_run}  (threshold {threshold})")
    print(f"  tripped              : {'YES' if tripped else 'no'}")
    print(
        f"  RX signatures        : nan={nan_ct}  "
        f"overload(0x0002)={ovl_ct}  underload(0x0004)={und_ct}  "
        f"device-error(!)={err_ct}"
    )
    if tripped:
        if err_ct:
            verdict = "DEVICE-ERROR (!) — existing error path catches it"
        elif nan_ct and ovl_ct:
            verdict = "nan + overload-status — guard assumption CONFIRMED"
        elif nan_ct:
            verdict = "nan current — guard assumption CONFIRMED"
        elif ovl_ct:
            verdict = "overload-status (0x0002) — guard assumption CONFIRMED"
        else:
            verdict = "UNKNOWN — tripped but no nan/overload/!"
        print(f"  >> SIGNAL = {verdict}")
    elif und_ct:
        print(f"  >> SIGNAL = UNDERLOAD (0x0004) / near-zero current on "
              f"{und_ct} pts — the cell opened but the guard did NOT trip "
              f"(it keys only on overload/nan). ASSUMPTION NOT MET.")
    else:
        print("  >> SIGNAL = none — cell stayed healthy (no event)")

    show_idx = first_guard_idx if first_guard_idx is not None else first_event_idx
    if show_idx is not None:
        lo = max(0, show_idx - 3)
        hi = min(len(rx), show_idx + 12)
        print(f"\n  RX context around first event (idx {show_idx}):")
        for i in range(lo, hi):
            print(f"    [{i:4d}] {rx[i][:90]}")
    print(f"\n  capture log: {os.path.abspath(path)}")


# --------------------------------------------------------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    label = sys.argv[1]
    ch = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    cr = sys.argv[3] if len(sys.argv) > 3 else "100u"
    t_run = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0

    os.makedirs(CAPTURE_DIR, exist_ok=True)
    cap_path = os.path.join(CAPTURE_DIR, f"{label}.log")
    if os.path.exists(cap_path):
        os.remove(cap_path)  # fresh log per labelled run
    os.environ["EMSTAT_RAW_CAPTURE"] = cap_path

    threshold = DEFAULT_DISCONNECT_RUN
    print("=" * 68)
    print(f"  PR #12 disconnect test — run '{label}'")
    print(f"  CA on CH{ch}: e_dc=0.1V  t_interval=0.1s  t_run={t_run:g}s  cr={cr}")
    print(f"  trip threshold = {threshold} consecutive overloaded/NaN points")
    print(f"  capture -> {cap_path}")
    print("=" * 68)

    conn = PicoConnection()
    try:
        conn.connect(PORT)
        print(f"Connected on {PORT}  fw={conn.firmware_version!r}\n")
    except PicoConnectionError as exc:
        print(f"CONNECT FAILED: {exc}\n(Close the GUI so COM6 is free, then retry.)")
        return 1

    cfg = TechniqueConfig(
        technique="ca",
        params={
            "e_dc": 0.1,
            "t_run": t_run,
            "t_interval": 0.1,
            "cr": cr,
            "bw_hz": 400,
        },
        channels=[ch],
        electrode_config_mode="external",
        re_ce_channels=[],
    )
    eng = MeasurementEngine()
    eng._connection = conn
    eng._config = cfg

    state = {"err": None, "finished": False}
    eng.measurement_error.connect(lambda m: state.update(err=m))
    eng.measurement_finished.connect(lambda r: state.update(finished=True))

    tail = CaptureTail(cap_path, threshold)
    tail.start()

    if label.startswith(("run2", "run3")):
        print(">>> PULL the electrode a few seconds in and watch the run climb.\n")
    elif label.startswith("run4"):
        print(">>> Leave it connected — this run must NOT false-trip.\n")
    else:
        print(">>> Baseline: leave it connected. Expect run=0 throughout.\n")

    t0 = time.monotonic()
    eng._run_measurement()  # blocking; data flows on this thread
    elapsed = time.monotonic() - t0

    tail.stop()
    tail.join(timeout=2.0)

    print(f"\nRun ended after {elapsed:.1f}s")
    res = eng.result
    print(f"  points kept : {res.num_points if res else 0}")
    print(f"  engine error: {state['err']}")
    analyse(cap_path, threshold)

    conn.disconnect()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
