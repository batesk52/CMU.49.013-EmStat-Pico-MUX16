"""Detect a disconnected reference (RE) or counter (CE) electrode.

A potentiostat holds the working electrode at the requested potential by
driving current through the counter electrode while sensing the cell
voltage at the reference electrode.  If either RE or CE goes open the
control loop can no longer establish the cell: the control amplifier rails
to its compliance voltage and the current ADC saturates.  The EmStat Pico
reports that saturation two ways on every affected data packet:

* the per-variable status field carries the ``STATUS_OVERLOAD`` (0x0002)
  bit (see ``protocol.py``), and
* the current reading is emitted as ``nan`` (decoded here as ``float('nan')``).

A *single* overloaded point is normal — autoranging, capacitive switching
spikes in CV, and the first settling point of a fast technique can all
overload transiently and recover.  What distinguishes a disconnected
electrode is that the cell *never* recovers: every subsequent point stays
overloaded.  This monitor therefore trips only on a sustained run of
consecutive unhealthy points, and resets the run the moment a healthy
current reading arrives.

Because the MUX16 shares one RE/CE pair across all working-electrode
channels, a disconnected RE/CE overloads *every* channel, so the run of
unhealthy points accumulates across channel switches.  A single dead WE
channel, by contrast, is interrupted by the healthy readings of the other
channels and will not trip the monitor — which is the desired behaviour:
this guard is for RE/CE, not for one bad WE.

The monitor never raises; it exposes ``tripped`` and ``reason`` so each
caller can react in its own idiom (a Qt error signal in the GUI engine,
an exception in the MCP service).  ``ElectrodeDisconnectError`` is provided
for callers that prefer to raise.
"""

from __future__ import annotations

import math

from src.comms.protocol import STATUS_OVERLOAD, ParsedPacket

# Default number of *consecutive* overloaded/NaN-current points that
# constitutes a disconnected RE/CE.  Tuned to clear the few-point
# transients seen at technique start and during autoranging while still
# tripping well under a second on a fast technique (CA/CV emit sub-second
# packets).  Configurable per call site; pass <= 0 to disable the guard.
DEFAULT_DISCONNECT_RUN: int = 10

# Variable names (from ``protocol.VAR_TYPES``) that carry a working-electrode
# current.  A NaN on any of these is treated as an overload sentinel.
_CURRENT_VAR_NAMES: frozenset[str] = frozenset(
    {
        "current",
        "generic_current_1",
        "generic_current_2",
        "generic_current_3",
        "generic_current_4",
        "eis_i_ac",
        "eis_i_dc",
        "eis_i_tdd",
    }
)


class ElectrodeDisconnectError(RuntimeError):
    """Raised when a sustained overload indicates an open RE/CE.

    Subclasses ``RuntimeError`` (not a connection error) so callers that
    distinguish a lost serial link from a lost cell — e.g. the MCP
    service's reconnect-and-retry path — do not mistake an electrode
    fault for a comms fault and pointlessly retry the run.
    """


def _classify(packet: ParsedPacket) -> str:
    """Classify a packet's cell health.

    Returns one of:
        ``"unhealthy"`` — overload status set, or a current reading is NaN.
        ``"healthy"``   — a finite current reading with no overload flag.
        ``"neutral"``   — no current information in this packet (e.g. a
            potential-only or marker-adjacent packet); leaves the run
            counter unchanged rather than falsely resetting it.
    """
    overload = any(
        v.status is not None and (v.status & STATUS_OVERLOAD)
        for v in packet.variables
    )

    current_vars = [
        v for v in packet.variables if v.var_type and v.name in _CURRENT_VAR_NAMES
    ]
    nan_current = any(math.isnan(v.value) for v in current_vars)

    if overload or nan_current:
        return "unhealthy"
    if current_vars:
        return "healthy"
    return "neutral"


class ElectrodeHealthMonitor:
    """Stateful guard that trips on a sustained overload run.

    Feed every decoded ``ParsedPacket`` to :meth:`observe` as it streams
    in.  After each call check :attr:`tripped`; once true it stays true
    until :meth:`reset`.  The monitor is cheap and side-effect free.

    Args:
        consecutive_threshold: Number of consecutive unhealthy points that
            trips the monitor.  Values <= 0 disable the guard entirely
            (``tripped`` never becomes true).
    """

    def __init__(
        self, consecutive_threshold: int = DEFAULT_DISCONNECT_RUN
    ) -> None:
        self._threshold = int(consecutive_threshold)
        self._consecutive = 0
        self._max_consecutive = 0
        self._total = 0
        self._unhealthy_total = 0

    def reset(self) -> None:
        """Reset all counters (e.g. between rounds of a continuous run)."""
        self._consecutive = 0
        self._max_consecutive = 0
        self._total = 0
        self._unhealthy_total = 0

    def observe(self, packet: ParsedPacket) -> None:
        """Update the overload run from one decoded data packet."""
        verdict = _classify(packet)
        if verdict == "neutral":
            return
        self._total += 1
        if verdict == "unhealthy":
            self._unhealthy_total += 1
            self._consecutive += 1
            if self._consecutive > self._max_consecutive:
                self._max_consecutive = self._consecutive
        else:  # healthy
            self._consecutive = 0

    @property
    def tripped(self) -> bool:
        """True once the consecutive overload run reaches the threshold."""
        if self._threshold <= 0:
            return False
        return self._consecutive >= self._threshold

    @property
    def consecutive(self) -> int:
        """Length of the current unbroken overload run."""
        return self._consecutive

    @property
    def reason(self) -> str:
        """Human-readable explanation for the trip (or current state)."""
        return (
            f"Possible disconnected RE/CE: {self._consecutive} consecutive "
            f"overloaded/NaN current readings (threshold {self._threshold}). "
            "The potentiostat could not maintain cell control. Check the "
            "reference and counter electrode connections, then re-run."
        )
