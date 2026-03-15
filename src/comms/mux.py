"""MUX16 channel address calculation and GPIO control script generation.

The PalmSens MUX16 multiplexer switches 16 electrode channels via a
10-bit GPIO address. In MUX16 mode, Working Electrode (WE) and
Reference/Counter Electrode (RE/CE) are switched together.

Address bit layout (10 bits)::

    Bits [9:8]  — Enable (inverted: 0 = enabled, 1 = disabled)
    Bits [7:4]  — RE/CE channel select (0-15)
    Bits [3:0]  — WE channel select (0-15)

Hardware channels are 1-indexed (CH1–CH16), while GPIO addresses use
0-indexed channel values internally.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Number of channels supported by the MUX16 module
MAX_CHANNELS = 16

# GPIO configuration mask: all 10 bits as outputs (0x3FF = 1023)
GPIO_CONFIG_MASK = 0x3FF

# Enable bits mask: bits 9 and 8
_ENABLE_MASK = 0x300  # 0b11_0000_0000

# When enabled, enable bits are 0 (inverted logic)
_ENABLED = 0x000
_DISABLED = _ENABLE_MASK  # 0x300


class MuxError(Exception):
    """Raised for invalid MUX channel operations."""


class MuxController:
    """Manages MUX16 channel addressing and MethodSCRIPT GPIO commands.

    Provides methods to calculate 10-bit GPIO addresses for channels
    1–16 and to generate MethodSCRIPT script fragments for GPIO
    initialisation, channel selection, and multi-channel scanning.

    In MUX16 mode, WE and RE/CE are always switched together, so
    ``bits[7:4]`` (RE/CE) and ``bits[3:0]`` (WE) are set to the same
    channel index.

    Example::

        mux = MuxController()
        addr = mux.channel_address(1)   # 0x000
        addr = mux.channel_address(16)  # 0x0FF
        script = mux.select_channel_script(5)
    """

    def channel_address(self, channel: int) -> int:
        """Calculate the 10-bit GPIO address for a MUX16 channel.

        Args:
            channel: 1-indexed channel number (1–16).

        Returns:
            10-bit integer address with enable bits cleared (enabled)
            and WE/RE-CE set to the corresponding 0-indexed channel.

        Raises:
            MuxError: If channel is outside the valid range 1–16.
        """
        self._validate_channel(channel)
        idx = channel - 1  # Convert to 0-indexed
        # WE = bits[3:0], RE/CE = bits[7:4], enable = bits[9:8] = 0
        address = (idx << 4) | idx
        return address

    def channel_address_disabled(self) -> int:
        """Return the GPIO address with MUX outputs disabled.

        Enable bits (9:8) set to 1 (disabled in inverted logic).
        Channel bits are zeroed.

        Returns:
            10-bit address with enable bits set (disabled).
        """
        return _DISABLED

    def gpio_config_script(self) -> list[str]:
        """Generate MethodSCRIPT lines to configure GPIO for MUX16.

        Configures all 10 GPIO pins as outputs using
        ``set_gpio_cfg 0x3FFi 1``.

        Returns:
            List of MethodSCRIPT lines (without trailing newlines).
        """
        return [f"set_gpio_cfg 0x{GPIO_CONFIG_MASK:03X}i 1"]

    def select_channel_script(self, channel: int) -> list[str]:
        """Generate MethodSCRIPT lines to switch to a specific channel.

        Args:
            channel: 1-indexed channel number (1–16).

        Returns:
            List of MethodSCRIPT lines to set the GPIO address.

        Raises:
            MuxError: If channel is outside the valid range.
        """
        addr = self.channel_address(channel)
        return [f"set_gpio 0x{addr:03X}i"]

    def disable_script(self) -> list[str]:
        """Generate MethodSCRIPT lines to disable MUX outputs.

        Returns:
            List of MethodSCRIPT lines to set enable bits high (disabled).
        """
        addr = self.channel_address_disabled()
        return [f"set_gpio 0x{addr:03X}i"]

    def scan_channels_script(self, channels: list[int]) -> list[str]:
        """Generate a MethodSCRIPT loop that iterates over channels.

        Uses ``meas_loop_for`` with ``add_var`` to step through the
        selected channels. The loop variable can be used inside the
        measurement to index the current channel.

        For each channel, the script:
        1. Sets the GPIO to the channel address
        2. Provides a placeholder comment for the measurement body

        The caller is responsible for inserting the actual measurement
        commands inside the loop body.

        Args:
            channels: List of 1-indexed channel numbers to scan.

        Returns:
            List of MethodSCRIPT lines for the channel scan loop.

        Raises:
            MuxError: If any channel is outside the valid range.
            ValueError: If the channel list is empty.
        """
        if not channels:
            raise ValueError("Channel list must not be empty.")
        for ch in channels:
            self._validate_channel(ch)

        lines: list[str] = []

        # Configure GPIO
        lines.extend(self.gpio_config_script())

        # Build a loop that steps through each channel
        n_channels = len(channels)
        lines.append(
            f"meas_loop_for p c {n_channels}i"
        )

        for i, ch in enumerate(channels):
            addr = self.channel_address(ch)
            if i == 0:
                # First channel — set GPIO at loop start
                lines.append(f"  set_gpio 0x{addr:03X}i")
            else:
                # Subsequent channels — use add_var to step
                lines.append(f"  add_var p 1i 0i")
                lines.append(f"  set_gpio 0x{addr:03X}i")

        lines.append("endloop")

        return lines

    def scan_channels_script_with_body(
        self,
        channels: list[int],
        body_lines: list[str],
    ) -> list[str]:
        """Generate a multi-channel scan loop with measurement body.

        For each selected channel, the generated script:
        1. Sets the MUX GPIO to the channel address
        2. Executes the provided measurement body lines

        Args:
            channels: 1-indexed channel numbers (1–16).
            body_lines: MethodSCRIPT lines to execute per channel.
                These are indented inside the loop.

        Returns:
            Complete MethodSCRIPT lines for the multi-channel scan.

        Raises:
            MuxError: If any channel is outside the valid range.
            ValueError: If channels or body_lines are empty.
        """
        if not channels:
            raise ValueError("Channel list must not be empty.")
        if not body_lines:
            raise ValueError("Measurement body must not be empty.")
        for ch in channels:
            self._validate_channel(ch)

        lines: list[str] = []

        # Configure GPIO pins as outputs
        lines.extend(self.gpio_config_script())

        # Outer loop over channels using add_var stepping
        n_channels = len(channels)
        lines.append(f"meas_loop_for p c {n_channels}i")

        for i, ch in enumerate(channels):
            addr = self.channel_address(ch)
            if i > 0:
                lines.append(f"  add_var p 1i 0i")
            lines.append(f"  set_gpio 0x{addr:03X}i")

            # Insert measurement body (indented)
            for body_line in body_lines:
                lines.append(f"  {body_line}")

        lines.append("endloop")

        return lines

    @staticmethod
    def _validate_channel(channel: int) -> None:
        """Raise MuxError if channel is outside [1, 16].

        Args:
            channel: Channel number to validate.

        Raises:
            MuxError: If channel < 1 or channel > 16.
        """
        if not isinstance(channel, int) or channel < 1 or channel > MAX_CHANNELS:
            raise MuxError(
                f"Channel must be an integer between 1 and {MAX_CHANNELS}, "
                f"got {channel!r}."
            )
