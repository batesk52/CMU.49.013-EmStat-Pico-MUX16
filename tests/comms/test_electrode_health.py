"""Tests for the disconnected RE/CE guard (ElectrodeHealthMonitor)."""

from __future__ import annotations

from src.comms.electrode_health import (
    DEFAULT_DISCONNECT_RUN,
    ElectrodeDisconnectError,
    ElectrodeHealthMonitor,
)
from src.comms.protocol import (
    STATUS_OVERLOAD,
    PacketParser,
    ParsedPacket,
    ParsedVariable,
)


def _current_packet(
    value: float = 1.0e-6,
    status: int | None = None,
) -> ParsedPacket:
    """Build a packet carrying a single WE-current variable."""
    return ParsedPacket(
        variables=[
            ParsedVariable(
                var_type="ba",
                name="current",
                value=value,
                status=status,
            )
        ]
    )


def _overload_packet() -> ParsedPacket:
    return _current_packet(status=STATUS_OVERLOAD)


def _nan_current_packet() -> ParsedPacket:
    return _current_packet(value=float("nan"))


def _healthy_packet() -> ParsedPacket:
    return _current_packet(value=2.5e-6, status=0)


def _potential_only_packet() -> ParsedPacket:
    """No current variable -> neutral, must not move the run counter."""
    return ParsedPacket(
        variables=[
            ParsedVariable(
                var_type="da", name="set_potential", value=0.1, status=None
            )
        ]
    )


# ---------------------------------------------------------------------------
# Tripping behaviour
# ---------------------------------------------------------------------------


def test_does_not_trip_below_threshold() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=10)
    for _ in range(9):
        mon.observe(_overload_packet())
    assert not mon.tripped
    assert mon.consecutive == 9


def test_trips_exactly_at_threshold() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=10)
    for _ in range(9):
        mon.observe(_overload_packet())
        assert not mon.tripped
    mon.observe(_overload_packet())
    assert mon.tripped


def test_nan_current_counts_as_overload() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=3)
    for _ in range(3):
        mon.observe(_nan_current_packet())
    assert mon.tripped


def test_healthy_reading_resets_the_run() -> None:
    """A single recovered point clears the run — transients don't trip."""
    mon = ElectrodeHealthMonitor(consecutive_threshold=5)
    for _ in range(4):
        mon.observe(_overload_packet())
    assert mon.consecutive == 4
    mon.observe(_healthy_packet())
    assert mon.consecutive == 0
    assert not mon.tripped
    # Must take a fresh full run to trip after recovery.
    for _ in range(4):
        mon.observe(_overload_packet())
    assert not mon.tripped


def test_neutral_packet_does_not_reset_run() -> None:
    """A potential-only packet carries no current info; leave the run."""
    mon = ElectrodeHealthMonitor(consecutive_threshold=3)
    mon.observe(_overload_packet())
    mon.observe(_overload_packet())
    mon.observe(_potential_only_packet())  # neutral
    assert mon.consecutive == 2
    mon.observe(_overload_packet())
    assert mon.tripped


def test_run_accumulates_across_channels() -> None:
    """Shared RE/CE disconnect overloads every channel; the run continues
    across interleaved channels because no channel reads healthy."""
    mon = ElectrodeHealthMonitor(consecutive_threshold=6)
    # Two channels' worth of overloads, none healthy.
    for _ in range(6):
        mon.observe(_overload_packet())
    assert mon.tripped


def test_one_dead_we_among_healthy_channels_does_not_trip() -> None:
    """One bad WE is broken up by the other channels' healthy reads."""
    mon = ElectrodeHealthMonitor(consecutive_threshold=4)
    for _ in range(20):
        mon.observe(_overload_packet())  # the dead WE
        mon.observe(_healthy_packet())  # a good neighbour channel
    assert not mon.tripped


def test_threshold_zero_disables_guard() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=0)
    for _ in range(100):
        mon.observe(_overload_packet())
    assert not mon.tripped


def test_reset_clears_counters() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=3)
    for _ in range(3):
        mon.observe(_overload_packet())
    assert mon.tripped
    mon.reset()
    assert not mon.tripped
    assert mon.consecutive == 0


# ---------------------------------------------------------------------------
# Integration with the real packet parser
# ---------------------------------------------------------------------------


def test_real_overload_packet_from_parser_trips() -> None:
    """End-to-end: device lines with the overload status bit trip the guard.

    ``ba8000000 ,10002`` is a current variable (value 0) carrying the
    metadata field ``1<hex>`` = status 0x0002 = STATUS_OVERLOAD.
    """
    parser = PacketParser()
    mon = ElectrodeHealthMonitor(consecutive_threshold=5)
    for _ in range(5):
        packet = parser.parse_line("Pba8000000 ,10002")
        assert isinstance(packet, ParsedPacket)
        mon.observe(packet)
    assert mon.tripped


def test_real_nan_current_packet_from_parser_trips() -> None:
    parser = PacketParser()
    mon = ElectrodeHealthMonitor(consecutive_threshold=3)
    for _ in range(3):
        packet = parser.parse_line("Pba     nan")
        assert isinstance(packet, ParsedPacket)
        mon.observe(packet)
    assert mon.tripped


def test_real_healthy_current_does_not_trip() -> None:
    parser = PacketParser()
    mon = ElectrodeHealthMonitor(consecutive_threshold=3)
    for _ in range(50):
        packet = parser.parse_line("Pba8000ABCu")
        assert isinstance(packet, ParsedPacket)
        mon.observe(packet)
    assert not mon.tripped


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_default_run_constant_is_positive() -> None:
    assert DEFAULT_DISCONNECT_RUN > 0


def test_disconnect_error_is_runtime_error_not_connection_error() -> None:
    """The MCP reconnect path catches PicoConnectionError; the electrode
    fault must NOT look like one, so it subclasses RuntimeError."""
    assert issubclass(ElectrodeDisconnectError, RuntimeError)


def test_reason_mentions_re_ce() -> None:
    mon = ElectrodeHealthMonitor(consecutive_threshold=1)
    mon.observe(_overload_packet())
    assert mon.tripped
    assert "RE/CE" in mon.reason
