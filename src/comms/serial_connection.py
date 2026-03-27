"""Serial connection manager for the PalmSens EmStat Pico.

Handles UART communication at 230400 baud (no software flow control).
Provides methods for sending commands, loading MethodSCRIPT scripts,
and reading device responses.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import serial

logger = logging.getLogger(__name__)

# Default serial settings per EmStat Pico communication protocol
BAUDRATE = 230400
BYTESIZE = serial.EIGHTBITS
PARITY = serial.PARITY_NONE
STOPBITS = serial.STOPBITS_ONE
COMMAND_TIMEOUT = 5.0  # seconds for idle commands
MEASUREMENT_TIMEOUT = 120.0  # seconds during measurements

# Device response markers
RESPONSE_OK = "*\n"
RESPONSE_END = "\n"
PROMPT_IDLE = "\n"


class PicoConnectionError(Exception):
    """Raised when a serial communication error occurs."""


class PicoConnection:
    """Manages the serial connection to an EmStat Pico device.

    Provides connect/disconnect lifecycle, raw command sending, script
    loading, and firmware/serial-number queries. Thread-safe for
    concurrent write access (e.g., abort from GUI thread while engine
    thread reads).

    Attributes:
        port: The COM/serial port path (e.g., 'COM3' or '/dev/ttyUSB0').
        firmware_version: Firmware version string, populated on connect.
        serial_number: Device serial number, populated on connect.
        is_connected: Whether the serial port is currently open.
    """

    def __init__(self, port: Optional[str] = None) -> None:
        """Initialize PicoConnection.

        Args:
            port: Serial port path. Can also be set later via connect().
        """
        self.port: Optional[str] = port
        self.firmware_version: Optional[str] = None
        self.serial_number: Optional[str] = None
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Return True if the serial port is open and ready."""
        return self._serial is not None and self._serial.is_open

    def connect(self, port: Optional[str] = None) -> None:
        """Open the serial connection and query device identity.

        Configures the port for 230400/8N1 (no software flow control),
        then queries firmware version and serial number.

        Args:
            port: Serial port path. Overrides port set in constructor.

        Raises:
            PicoConnectionError: If the port cannot be opened or the
                device does not respond to identity queries.
        """
        if port is not None:
            self.port = port
        if self.port is None:
            raise PicoConnectionError("No serial port specified.")
        if self.is_connected:
            logger.warning("Already connected to %s, disconnecting first.", self.port)
            self.disconnect()

        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=BAUDRATE,
                bytesize=BYTESIZE,
                parity=PARITY,
                stopbits=STOPBITS,
                timeout=COMMAND_TIMEOUT,
                xonxoff=True,
                rtscts=False,
                dsrdtr=False,
                write_timeout=2,
            )
            # Increase receive buffer to prevent data loss on long
            # multi-scan sequences (Windows default is only 4096).
            self._serial.set_buffer_size(rx_size=65536, tx_size=4096)
            logger.info("Opened serial port %s at %d baud.", self.port, BAUDRATE)
        except serial.SerialException as exc:
            self._serial = None
            raise PicoConnectionError(
                f"Failed to open port {self.port}: {exc}"
            ) from exc

        # Flush any stale data left in the serial buffer (e.g. from a
        # previous aborted measurement) before querying device identity.
        self._serial.reset_input_buffer()

        # Query device identity
        try:
            self.firmware_version = self.get_firmware_version()
            self.serial_number = self.get_serial_number()
            logger.info(
                "Connected — firmware: %s, serial: %s",
                self.firmware_version,
                self.serial_number,
            )
        except PicoConnectionError:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        """Close the serial connection and reset device info.

        Safe to call even if not connected.
        """
        if self._serial is not None:
            try:
                if self._serial.is_open:
                    self._serial.close()
                    logger.info("Disconnected from %s.", self.port)
            except serial.SerialException as exc:
                logger.warning("Error closing port: %s", exc)
            finally:
                self._serial = None
                self.firmware_version = None
                self.serial_number = None

    def send_command(self, cmd: str) -> str:
        """Send a single-character command and return the response.

        The device echoes the first character of any command. This echo
        is stripped from the returned response.

        Args:
            cmd: A single command character (e.g., 't', 'i', 'v').

        Returns:
            The device response with the echo character stripped.

        Raises:
            PicoConnectionError: If not connected or communication fails.
        """
        self._ensure_connected()
        with self._lock:
            try:
                # Commands are terminated with LF only
                self._serial.write(f"{cmd}\n".encode("ascii"))
                self._serial.flush()
                logger.debug("Sent command: %r", cmd)

                response = self._read_until_prompt()
                # Strip leading echo character
                if response and response[0] == cmd[0]:
                    response = response[1:]
                return response.strip()
            except serial.SerialException as exc:
                raise PicoConnectionError(
                    f"Communication error sending '{cmd}': {exc}"
                ) from exc

    def send_script(self, lines: list[str]) -> None:
        """Load a MethodSCRIPT onto the device for execution.

        Sends the 'e' command to enter script mode, then sends each
        script line terminated with LF. An empty line signals end of
        script and starts execution.

        No empty lines are allowed within the script body (the device
        interprets them as end-of-script).

        Args:
            lines: MethodSCRIPT lines to send. Must not contain empty
                strings (use the returned lines from script generators).

        Raises:
            PicoConnectionError: If not connected or communication fails.
            ValueError: If any line in the script is empty.
        """
        self._ensure_connected()
        # Validate: no empty lines in script body
        for i, line in enumerate(lines):
            if not line.strip():
                raise ValueError(
                    f"Empty line at index {i} — empty lines terminate "
                    "MethodSCRIPT; remove or fix the script generator."
                )

        with self._lock:
            try:
                # Abort any running script, then clear stale data
                self._serial.write(b"Z\n")
                self._serial.flush()
                time.sleep(0.3)
                self._serial.reset_input_buffer()

                # Enter script-loading mode
                self._serial.write(b"e\n")
                self._serial.flush()
                # Give device time to transition to script-loading
                # mode before sending lines — without this, early
                # lines can arrive before the device is ready,
                # causing e!4001 "unknown command" errors.
                time.sleep(0.05)
                logger.debug("Entered script-loading mode ('e' command).")

                # Send each script line with small inter-line
                # delay to avoid USB serial buffer overrun
                for line in lines:
                    self._serial.write(f"{line}\n".encode("ascii"))
                    self._serial.flush()
                    time.sleep(0.002)

                # Empty line terminates script and starts execution
                self._serial.write(b"\n")
                self._serial.flush()
                logger.info(
                    "Script loaded and execution started (%d lines).",
                    len(lines),
                )
            except serial.SerialException as exc:
                raise PicoConnectionError(
                    f"Error loading script: {exc}"
                ) from exc

    def read_response(self, timeout: Optional[float] = None) -> str:
        """Read a single line from the device.

        Args:
            timeout: Read timeout in seconds. Defaults to the serial
                port's current timeout setting.

        Returns:
            A single response line with trailing newline stripped.

        Raises:
            PicoConnectionError: If not connected or a read error occurs.
        """
        self._ensure_connected()
        original_timeout = self._serial.timeout
        if timeout is not None:
            self._serial.timeout = timeout
        try:
            raw = self._serial.readline()
            if not raw:
                return ""
            return raw.decode("ascii", errors="replace").rstrip("\r\n")
        except serial.SerialException as exc:
            raise PicoConnectionError(
                f"Error reading response: {exc}"
            ) from exc
        finally:
            if timeout is not None:
                self._serial.timeout = original_timeout

    def read_responses(
        self, timeout: Optional[float] = None
    ) -> list[str]:
        """Read all available response lines until an empty read.

        Useful for collecting streaming data during measurements. Call
        repeatedly or use read_response() for line-by-line processing.

        Args:
            timeout: Per-line read timeout in seconds.

        Returns:
            List of response lines.
        """
        responses: list[str] = []
        while True:
            line = self.read_response(timeout=timeout)
            if not line:
                break
            responses.append(line)
        return responses

    def get_firmware_version(self) -> str:
        """Query the device firmware version.

        Sends the 't' command (version query).

        Returns:
            Firmware version string (e.g., 'espico 1.6 lr202307').

        Raises:
            PicoConnectionError: If the query fails.
        """
        response = self.send_command("t")
        if not response:
            raise PicoConnectionError(
                "No firmware version response from device."
            )
        # Response may be multi-line; first line is version
        return response.splitlines()[0] if response else ""

    def get_serial_number(self) -> str:
        """Query the device serial number.

        Sends the 'i' command (device info/serial number).

        Returns:
            Device serial number string.

        Raises:
            PicoConnectionError: If the query fails.
        """
        response = self.send_command("i")
        if not response:
            raise PicoConnectionError(
                "No serial number response from device."
            )
        return response.splitlines()[0] if response else ""

    def abort(self) -> None:
        """Abort the currently running measurement.

        Sends the 'Z' command. Thread-safe — can be called from the GUI
        thread while the engine thread reads measurement data.
        """
        self._ensure_connected()
        with self._lock:
            try:
                self._serial.write(b"Z\n")
                self._serial.flush()
                logger.info("Abort command sent.")
            except serial.SerialException as exc:
                raise PicoConnectionError(
                    f"Error sending abort: {exc}"
                ) from exc

    def halt(self) -> None:
        """Halt (pause) the currently running measurement.

        Sends the 'h' command. The measurement can be resumed with
        resume().
        """
        self._ensure_connected()
        with self._lock:
            try:
                self._serial.write(b"h\n")
                self._serial.flush()
                logger.info("Halt command sent.")
            except serial.SerialException as exc:
                raise PicoConnectionError(
                    f"Error sending halt: {exc}"
                ) from exc

    def resume(self) -> None:
        """Resume a halted measurement.

        Sends the 'H' command.
        """
        self._ensure_connected()
        with self._lock:
            try:
                self._serial.write(b"H\n")
                self._serial.flush()
                logger.info("Resume command sent.")
            except serial.SerialException as exc:
                raise PicoConnectionError(
                    f"Error sending resume: {exc}"
                ) from exc

    def set_timeout(self, timeout: float) -> None:
        """Update the serial read timeout.

        Args:
            timeout: New timeout in seconds.
        """
        self._ensure_connected()
        self._serial.timeout = timeout

    def _ensure_connected(self) -> None:
        """Raise if the serial port is not open."""
        if not self.is_connected:
            raise PicoConnectionError(
                "Not connected. Call connect() first."
            )

    def wait_until_idle(self, timeout: float = 5.0) -> bool:
        """Block until the device is idle and ready for a new script.

        Polls the device with the version command ('t') until it
        responds, confirming it has finished any running script and
        returned to command mode.

        Args:
            timeout: Maximum seconds to wait before giving up.

        Returns:
            True if the device is idle, False if timed out.
        """
        self._ensure_connected()
        deadline = time.monotonic() + timeout
        with self._lock:
            while time.monotonic() < deadline:
                try:
                    # Flush any residual data
                    self._serial.reset_input_buffer()
                    # Send version query — device only responds in
                    # command mode (idle), not during script execution
                    self._serial.write(b"t\n")
                    self._serial.flush()
                    old_timeout = self._serial.timeout
                    self._serial.timeout = 1.0
                    response = self._serial.readline()
                    self._serial.timeout = old_timeout
                    if response and response.strip():
                        # Drain any remaining response lines
                        self._serial.timeout = 0.1
                        while self._serial.readline():
                            pass
                        self._serial.timeout = old_timeout
                        logger.debug("Device idle confirmed.")
                        return True
                except serial.SerialException:
                    pass
                time.sleep(0.1)
        logger.warning("Timed out waiting for device idle.")
        return False

    def _read_until_prompt(self) -> str:
        """Read lines from the device until an empty line or timeout.

        Returns:
            All response text concatenated.
        """
        lines: list[str] = []
        while True:
            raw = self._serial.readline()
            if not raw:
                break  # Timeout
            decoded = raw.decode("ascii", errors="replace").rstrip("\r\n")
            if decoded == "*":
                break  # Response-complete marker
            if not decoded and lines:
                break  # Empty line signals end of response
            if decoded:
                lines.append(decoded)
        return "\n".join(lines)

    def __enter__(self) -> "PicoConnection":
        """Context manager entry — connect if port is set."""
        if self.port and not self.is_connected:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit — disconnect."""
        self.disconnect()

    def __repr__(self) -> str:
        state = "connected" if self.is_connected else "disconnected"
        return f"PicoConnection(port={self.port!r}, {state})"
