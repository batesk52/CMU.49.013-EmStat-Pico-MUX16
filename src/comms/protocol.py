"""MethodSCRIPT data packet parser for the EmStat Pico.

Decodes hex-encoded data packets from the device's measurement output
stream. Packets follow the format ``P<var1>;<var2>;...\\n`` where each
variable is a 2-char type code + 7-char hex value + 1-char SI prefix.

The 28-bit hex values are decoded as:
    ``(hex_to_uint(value) - 2^27) * 10^(SI_exponent)``

This module also parses measurement loop markers (M, *, L, +) used for
technique and channel tracking in multiplexed measurements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SI prefix characters mapped to their base-10 exponents.
# The space character (' ') represents unity (10^0).
SI_PREFIXES: dict[str, int] = {
    "a": -18,  # atto
    "f": -15,  # femto
    "p": -12,  # pico
    "n": -9,   # nano
    "u": -6,   # micro
    "m": -3,   # milli
    " ": 0,    # unity
    "k": 3,    # kilo
    "M": 6,    # mega
    "G": 9,    # giga
    "T": 12,   # tera
    "P": 15,   # peta
    "E": 18,   # exa
}

# 2-character variable type codes → human-readable measurement names.
# Derived from MethodSCRIPT V1.6 specification Appendix.
VAR_TYPES: dict[str, str] = {
    "aa": "unknown",
    "ab": "measured_potential",
    "ac": "applied_potential",
    "ad": "cell_current",      # alternate current label
    "ae": "cell_potential",     # alternate potential label
    "ba": "current",
    "bb": "phase",             # alternate phase label
    "ca": "time",
    "cb": "impedance_real",
    "cc": "frequency",
    "cd": "charge",
    "da": "set_potential",
    "db": "impedance_imaginary",
    "dc": "impedance",
    "dd": "phase",
    "eb": "potential_ce",
    "ec": "potential_we_vs_ce",
    "jb": "misc_generic_2",
    "ja": "misc_generic_1",
}

# Status bit masks (metadata after comma in variable field)
STATUS_OK = 0x0000
STATUS_OVERLOAD = 0x0002
STATUS_UNDERLOAD = 0x0004
STATUS_OVERLOAD_WARNING = 0x0008

# Offset for signed 28-bit decoding
_OFFSET_28BIT = 2**27  # 134217728


class LoopMarker(Enum):
    """Measurement loop markers in the MethodSCRIPT response stream."""

    BEGIN = "M"       # Start of measurement loop
    END_LOOP = "*"    # End of loop iteration (more to come)
    END_MEAS = "+"    # End of measurement
    SUB_BEGIN = "L"   # Start of sub-loop (e.g., inner MUX channel loop)


@dataclass
class ParsedVariable:
    """A single decoded variable from a data packet.

    Attributes:
        var_type: 2-character type code (e.g., 'ba').
        name: Human-readable name (e.g., 'current').
        value: Decoded float value.
        status: Optional status/metadata value.
        current_range: Optional current range index.
    """

    var_type: str
    name: str
    value: float
    status: Optional[int] = None
    current_range: Optional[int] = None


@dataclass
class ParsedPacket:
    """A fully decoded data packet from a ``P...`` line.

    Attributes:
        variables: List of decoded variables in packet order.
        values: Convenience dict mapping variable names to float values.
    """

    variables: list[ParsedVariable] = field(default_factory=list)

    @property
    def values(self) -> dict[str, float]:
        """Return a dict of variable name → decoded value."""
        return {v.name: v.value for v in self.variables}


# ---------------------------------------------------------------------------
# PacketParser
# ---------------------------------------------------------------------------


class PacketParser:
    """Stateful parser for MethodSCRIPT response lines.

    Tracks measurement loop state (current technique, channel index)
    across multiple response lines. Each ``P...`` line is decoded into
    a ``ParsedPacket`` containing one or more ``ParsedVariable`` entries.

    Typical usage::

        parser = PacketParser()
        for line in response_lines:
            result = parser.parse_line(line)
            if isinstance(result, ParsedPacket):
                print(result.values)

    Attributes:
        loop_depth: Current nesting depth of measurement loops.
        channel_index: Current MUX channel index (0-based) within a
            multi-channel scan, incremented by sub-loop markers.
    """

    def __init__(self) -> None:
        self.loop_depth: int = 0
        self.channel_index: int = 0

    def reset(self) -> None:
        """Reset parser state for a new measurement."""
        self.loop_depth = 0
        self.channel_index = 0

    # -- Public API ---------------------------------------------------------

    def parse_line(
        self, line: str
    ) -> Optional[ParsedPacket | LoopMarker]:
        """Parse a single response line from the device.

        Args:
            line: A stripped response line (no trailing newline).

        Returns:
            A ``ParsedPacket`` for data lines (starting with 'P'),
            a ``LoopMarker`` for loop-control lines (M, *, +, L),
            or ``None`` for unrecognised/empty lines.
        """
        if not line:
            return None

        first = line[0]

        if first == "P":
            return self.parse_packet(line)
        if first in ("M", "*", "+", "L"):
            return self._handle_loop_marker(first)

        # Unrecognised line (could be echo, error code, etc.)
        logger.debug("Unrecognised response line: %r", line)
        return None

    def parse_packet(self, line: str) -> ParsedPacket:
        """Decode a ``P<var1>;<var2>;...`` data packet line.

        Args:
            line: The full packet line including the leading 'P'.

        Returns:
            A ``ParsedPacket`` with all decoded variables.
        """
        # Strip leading 'P'
        body = line[1:] if line.startswith("P") else line
        packet = ParsedPacket()

        # Variables are separated by ';'
        for var_str in body.split(";"):
            var_str = var_str.strip()
            if not var_str:
                continue
            parsed = self._parse_variable(var_str)
            if parsed is not None:
                packet.variables.append(parsed)

        return packet

    def decode_value(self, hex_str: str, prefix: str) -> float:
        """Decode a 7-char hex string with SI prefix to a float.

        The 28-bit unsigned hex value is offset-decoded:
            ``(value - 2^27) * 10^(SI_exponent)``

        Args:
            hex_str: 7-character hexadecimal string (e.g., '8000800').
            prefix: Single SI prefix character (e.g., 'u' for micro).

        Returns:
            The decoded float value.

        Raises:
            ValueError: If the prefix is not recognised or hex is invalid.
        """
        if prefix not in SI_PREFIXES:
            raise ValueError(
                f"Unknown SI prefix {prefix!r}. "
                f"Valid prefixes: {list(SI_PREFIXES.keys())}"
            )
        raw = int(hex_str, 16)
        exponent = SI_PREFIXES[prefix]
        return (raw - _OFFSET_28BIT) * (10**exponent)

    @staticmethod
    def parse_var_type(code: str) -> str:
        """Map a 2-character variable type code to a human-readable name.

        Args:
            code: Two-character code (e.g., 'ba', 'da').

        Returns:
            Human-readable name (e.g., 'current', 'set_potential').
            Returns ``'unknown_<code>'`` for unrecognised codes.
        """
        return VAR_TYPES.get(code, f"unknown_{code}")

    # -- Internal -----------------------------------------------------------

    def _parse_variable(self, var_str: str) -> Optional[ParsedVariable]:
        """Parse a single variable field from a packet.

        A variable field has the structure:
            ``<type:2><hex:7><prefix:1>[,<metadata>...]``

        Metadata fields (after commas) may include status bits and
        current range information.
        """
        # Split off metadata (comma-separated fields after main value)
        parts = var_str.split(",")
        main = parts[0]

        if len(main) < 10:
            logger.warning("Variable field too short: %r", var_str)
            return None

        var_type = main[:2]
        hex_str = main[2:9]
        prefix = main[9]

        name = self.parse_var_type(var_type)
        value = self.decode_value(hex_str, prefix)

        # Parse optional metadata fields
        status = None
        current_range = None
        for meta in parts[1:]:
            meta = meta.strip()
            if not meta:
                continue
            # Status field: 4-char hex
            if len(meta) == 4:
                try:
                    status = int(meta, 16)
                except ValueError:
                    pass
            # Current range: typically 2-char hex
            elif len(meta) <= 3:
                try:
                    current_range = int(meta, 16)
                except ValueError:
                    pass

        return ParsedVariable(
            var_type=var_type,
            name=name,
            value=value,
            status=status,
            current_range=current_range,
        )

    def _handle_loop_marker(self, char: str) -> LoopMarker:
        """Process a loop marker character and update parser state."""
        if char == "M":
            self.loop_depth += 1
            logger.debug("Loop begin (depth=%d)", self.loop_depth)
            return LoopMarker.BEGIN
        elif char == "*":
            self.channel_index = 0
            logger.debug("Loop end iteration (depth=%d)", self.loop_depth)
            return LoopMarker.END_LOOP
        elif char == "+":
            self.loop_depth = max(0, self.loop_depth - 1)
            logger.debug("Measurement end (depth=%d)", self.loop_depth)
            return LoopMarker.END_MEAS
        elif char == "L":
            self.channel_index += 1
            logger.debug(
                "Sub-loop begin (channel_index=%d)", self.channel_index
            )
            return LoopMarker.SUB_BEGIN
        # Should not reach here
        return LoopMarker.BEGIN  # pragma: no cover
