"""Background measurement thread for the EmStat Pico MUX16.

Orchestrates electrochemical measurements in a QThread, keeping all
serial I/O off the GUI thread.  The engine builds a MethodSCRIPT from
the requested technique, parameters, and MUX channels, sends it to the
device, then reads the streaming response line-by-line, decoding data
packets in real time.  Decoded data points are emitted as Qt signals
for live plotting, and buffered into a MeasurementResult for post-run
export.

Abort, halt, and resume commands are forwarded to the device via the
thread-safe PicoConnection write methods and can be called from any
thread (typically the GUI thread).

Typical usage from the GUI layer::

    engine = MeasurementEngine()
    engine.data_point_ready.connect(plot_widget.on_data_point)
    engine.measurement_finished.connect(on_done)
    engine.start_measurement(connection, config)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from src.comms.protocol import LoopMarker, PacketParser, ParsedPacket
from src.comms.serial_connection import (
    MEASUREMENT_TIMEOUT,
    PicoConnection,
    PicoConnectionError,
)
from src.data.incremental_writer import IncrementalCSVWriter
from src.data.models import DataPoint, MeasurementResult, TechniqueConfig
from src.techniques.scripts import generate

logger = logging.getLogger(__name__)

# Response line prefixes that signal the end of a measurement
_END_MARKERS = frozenset({"*", "+"})

# Device error line prefix (MethodSCRIPT errors start with '!')
_ERROR_PREFIX = "!"

# Empty-line sentinel returned when the device has nothing more to send
_EMPTY_LINE = ""


class MeasurementEngine(QThread):
    """Background QThread that executes electrochemical measurements.

    Accepts a ``PicoConnection``, a ``TechniqueConfig``, and runs the
    full measurement lifecycle: script generation, transmission, real-
    time response parsing, and result buffering.  Data flows to the GUI
    exclusively through Qt signals.

    Signals:
        data_point_ready(DataPoint): Emitted for every decoded data
            packet.  Connect to a plot widget for live updates.
        measurement_started(str): Emitted once the script is sent to
            the device.  Payload is the technique name.
        measurement_finished(MeasurementResult): Emitted on normal
            completion with the full buffered result.
        measurement_error(str): Emitted when a fatal error occurs
            (serial disconnect, device error code, script error).
        channel_changed(int): Emitted when the MUX switches to a new
            channel (1-indexed).

    Attributes:
        result: The ``MeasurementResult`` being built during the run.
            Only valid after ``measurement_finished`` is emitted.
    """

    # ---- Qt signals (class-level) ----------------------------------------
    data_point_ready = pyqtSignal(object)  # DataPoint
    measurement_started = pyqtSignal(str)  # technique name
    measurement_finished = pyqtSignal(object)  # MeasurementResult
    measurement_error = pyqtSignal(str)  # error message
    channel_changed = pyqtSignal(int)  # 1-indexed channel
    auto_save_completed = pyqtSignal(str)  # output dir path

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._connection: Optional[PicoConnection] = None
        self._config: Optional[TechniqueConfig] = None
        self._abort_requested: bool = False
        self._halted: bool = False
        self.result: Optional[MeasurementResult] = None
        self._writer: Optional[IncrementalCSVWriter] = None
        self._last_flush_index: int = 0

    # ---- Public API (called from GUI thread) -----------------------------

    def start_measurement(
        self,
        connection: PicoConnection,
        config: TechniqueConfig,
    ) -> None:
        """Configure and launch the measurement thread.

        This must be called from the GUI thread.  It stores the
        connection and config, then calls ``QThread.start()`` which
        invokes ``run()`` in the background thread.

        Args:
            connection: An already-connected ``PicoConnection``.
            config: Technique configuration (technique name, parameters,
                and channel list).

        Raises:
            RuntimeError: If the engine is already running.
        """
        if self.isRunning():
            raise RuntimeError(
                "MeasurementEngine is already running. "
                "Abort or wait for completion before starting again."
            )
        self._connection = connection
        self._config = config
        self._abort_requested = False
        self._halted = False
        self.result = None
        self.start()

    def abort(self) -> None:
        """Request measurement abort.

        Sends the 'Z' command to the device via the thread-safe
        ``PicoConnection.abort()`` method.  The run loop will detect
        the abort flag and exit cleanly.

        Safe to call from any thread (typically the GUI thread).
        """
        self._abort_requested = True
        if self._connection is not None and self._connection.is_connected:
            try:
                self._connection.abort()
                logger.info("Abort requested by user.")
            except PicoConnectionError as exc:
                logger.warning("Error sending abort: %s", exc)

    def halt(self) -> None:
        """Pause the running measurement.

        Sends the 'h' command.  The device pauses data output until
        ``resume()`` is called.

        Safe to call from any thread.
        """
        self._halted = True
        if self._connection is not None and self._connection.is_connected:
            try:
                self._connection.halt()
                logger.info("Halt requested by user.")
            except PicoConnectionError as exc:
                logger.warning("Error sending halt: %s", exc)

    def resume(self) -> None:
        """Resume a halted measurement.

        Sends the 'H' command.

        Safe to call from any thread.
        """
        self._halted = False
        if self._connection is not None and self._connection.is_connected:
            try:
                self._connection.resume()
                logger.info("Resume requested by user.")
            except PicoConnectionError as exc:
                logger.warning("Error sending resume: %s", exc)

    # ---- QThread entry point (runs in background thread) -----------------

    def run(self) -> None:
        """Execute the measurement lifecycle.

        This method is invoked by ``QThread.start()`` and runs entirely
        in the background thread.  It should **never** be called
        directly.

        Lifecycle:
            1. Validate inputs
            2. Generate MethodSCRIPT
            3. Send script to device
            4. Read response lines in a loop, parsing data packets
            5. Emit signals for each data point and loop markers
            6. On completion or error, emit the appropriate signal
        """
        try:
            self._run_measurement()
        except PicoConnectionError as exc:
            msg = f"Serial communication error: {exc}"
            logger.error(msg)
            self.measurement_error.emit(msg)
        except Exception as exc:
            msg = f"Unexpected error: {exc}"
            logger.error(msg, exc_info=True)
            self.measurement_error.emit(msg)

    # ---- Internal implementation -----------------------------------------

    def _run_measurement(self) -> None:
        """Core measurement loop (runs in background thread)."""
        connection = self._connection
        config = self._config

        # -- Validate inputs -----------------------------------------------
        if connection is None or not connection.is_connected:
            self.measurement_error.emit(
                "No active connection. Connect to the device first."
            )
            return

        if config is None:
            self.measurement_error.emit(
                "No technique configuration provided."
            )
            return

        if not config.channels:
            self.measurement_error.emit(
                "No channels selected. Select at least one channel."
            )
            return

        # Validate channel numbers (1-16)
        for ch in config.channels:
            if not isinstance(ch, int) or ch < 1 or ch > 16:
                self.measurement_error.emit(
                    f"Invalid channel number: {ch}. "
                    "Channels must be integers between 1 and 16."
                )
                return

        technique = config.technique
        params = config.params
        channels = config.channels

        # -- Generate MethodSCRIPT -----------------------------------------
        try:
            script_lines = generate(technique, params, channels)
        except (ValueError, Exception) as exc:
            self.measurement_error.emit(
                f"Script generation failed: {exc}"
            )
            return

        logger.info(
            "Generated %d-line MethodSCRIPT for %s on channels %s.",
            len(script_lines),
            technique,
            channels,
        )

        # -- Initialise result buffer --------------------------------------
        self.result = MeasurementResult(
            technique=technique,
            start_time=datetime.now(),
            device_info={
                "firmware": connection.firmware_version or "",
                "serial": connection.serial_number or "",
            },
            params=dict(params),
            channels=list(channels),
        )

        # -- Initialise auto-save writer if enabled -------------------------
        self._writer = None
        self._last_flush_index = 0
        if (
            config.auto_save is not None
            and config.auto_save.enabled
            and config.auto_save.output_dir
        ):
            self._writer = IncrementalCSVWriter()
            auto_dir = self._writer.start(
                technique=technique,
                params=params,
                device_info=self.result.device_info,
                channels=channels,
                output_dir=config.auto_save.output_dir,
            )
            logger.info("Auto-save enabled: %s", auto_dir)

        # -- Send script to device -----------------------------------------
        try:
            connection.send_script(script_lines)
        except (PicoConnectionError, ValueError) as exc:
            self._finish_writer()
            self.measurement_error.emit(
                f"Failed to send script: {exc}"
            )
            return

        self.measurement_started.emit(technique)
        logger.info("Measurement started: %s", technique)

        # -- Stream and parse response -------------------------------------
        parser = PacketParser()
        measurement_start_time = time.monotonic()

        # Track the current channel: start at the first selected channel
        current_channel_idx = 0
        current_channel = channels[current_channel_idx]
        self.channel_changed.emit(current_channel)

        # Count of empty reads in a row (detect end of measurement)
        consecutive_empty = 0
        max_consecutive_empty = 3

        while not self._abort_requested:
            # Read one line from the device
            try:
                line = connection.read_response(
                    timeout=MEASUREMENT_TIMEOUT
                )
            except PicoConnectionError as exc:
                self.measurement_error.emit(
                    f"Serial read error: {exc}"
                )
                return

            # Handle empty lines (timeout or end of data)
            if line == _EMPTY_LINE:
                consecutive_empty += 1
                if consecutive_empty >= max_consecutive_empty:
                    logger.debug(
                        "Received %d consecutive empty reads; "
                        "assuming measurement complete.",
                        consecutive_empty,
                    )
                    break
                continue
            consecutive_empty = 0

            # Check for device error lines
            if line.startswith(_ERROR_PREFIX):
                error_msg = f"Device error: {line}"
                logger.error(error_msg)
                self.measurement_error.emit(error_msg)
                return

            # Parse the line
            result = parser.parse_line(line)

            if result is None:
                # Unrecognised line -- log and continue
                logger.debug("Skipping unrecognised line: %r", line)
                continue

            if isinstance(result, ParsedPacket):
                # Decode packet into a DataPoint
                elapsed = time.monotonic() - measurement_start_time
                data_point = DataPoint(
                    timestamp=elapsed,
                    channel=current_channel,
                    variables=dict(result.values),
                )
                self.result.add_point(data_point)
                self.data_point_ready.emit(data_point)

            elif isinstance(result, LoopMarker):
                if result == LoopMarker.SUB_BEGIN:
                    # Sub-loop marker: advance to next channel
                    if current_channel_idx + 1 < len(channels):
                        current_channel_idx += 1
                        current_channel = channels[
                            current_channel_idx
                        ]
                        self.channel_changed.emit(current_channel)
                        logger.debug(
                            "Channel changed to %d", current_channel
                        )

                elif result == LoopMarker.END_LOOP:
                    # End of loop iteration -- reset channel index
                    # for next pass through the channel list
                    current_channel_idx = 0
                    current_channel = channels[current_channel_idx]
                    self.channel_changed.emit(current_channel)
                    logger.debug(
                        "Loop reset — channel back to %d",
                        current_channel,
                    )
                    # Auto-save: flush points from this loop
                    self._flush_auto_save()

                elif result == LoopMarker.END_MEAS:
                    # Measurement complete
                    logger.info("Received end-of-measurement marker.")
                    break

                elif result == LoopMarker.BEGIN:
                    # Start of measurement loop -- no action needed
                    pass

        # -- Flush remaining auto-save data and finish writer ----------------
        self._flush_auto_save()
        self._finish_writer()

        # -- Emit completion -----------------------------------------------
        if self._abort_requested:
            logger.info("Measurement aborted by user.")
            self.measurement_error.emit("Measurement aborted by user.")
        else:
            logger.info(
                "Measurement complete: %d data points collected.",
                self.result.num_points,
            )
            self.measurement_finished.emit(self.result)

    def _flush_auto_save(self) -> None:
        """Flush new data points to the incremental writer."""
        if self._writer is None or self.result is None:
            return
        new_points = self.result.data_points[
            self._last_flush_index :
        ]
        if new_points:
            count = self._writer.flush_points(new_points)
            self._last_flush_index = len(self.result.data_points)
            logger.debug(
                "Auto-saved %d points at loop boundary.", count
            )

    def _finish_writer(self) -> None:
        """Close the incremental writer and emit completion signal."""
        if self._writer is not None and self._writer.is_active:
            paths = self._writer.finish()
            if paths:
                self.auto_save_completed.emit(
                    self._writer.output_dir
                )
            self._writer = None
