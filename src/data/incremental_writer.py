"""Incremental CSV writer for auto-saving during measurement.

Writes CSV data per channel incrementally at each MUX loop boundary,
ensuring data is preserved even if the application crashes or the
user aborts mid-experiment.  File format is identical to
:class:`CSVExporter` output for downstream compatibility.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

from src.data.exporters import _ordered_columns
from src.data.models import DataPoint

logger = logging.getLogger(__name__)


class IncrementalCSVWriter:
    """Writes CSV data incrementally during a measurement run.

    Lifecycle::

        writer = IncrementalCSVWriter()
        writer.start(technique, params, device_info, channels, output_dir)
        # ... at each MUX loop boundary:
        writer.flush_points(new_points)
        # ... on completion or abort:
        paths = writer.finish()

    Thread safety: :meth:`flush_points` is called from the engine
    QThread while :meth:`finish` may be called from the GUI thread
    during abort.  A lock guards both methods.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handles: dict[int, io.TextIOWrapper] = {}
        self._writers: dict[int, csv.writer] = {}
        self._columns: dict[int, list[str]] = {}
        self._header_written: set[int] = set()
        self._output_dir: str = ""
        self._technique: str = ""
        self._params: dict[str, Any] = {}
        self._device_info: dict[str, str] = {}
        self._start_time: Optional[datetime] = None
        self._started = False

    def start(
        self,
        technique: str,
        params: dict[str, Any],
        device_info: dict[str, str],
        channels: list[int],
        output_dir: str,
    ) -> str:
        """Initialise the writer and create output directory.

        Args:
            technique: Lowercase technique identifier.
            params: Technique parameter dict.
            device_info: Device metadata (serial, firmware).
            channels: List of channels that will produce data.
            output_dir: Base directory; a timestamped subdirectory
                is created automatically.

        Returns:
            Absolute path to the created output directory.
        """
        self._technique = technique
        self._params = params
        self._device_info = device_info
        self._start_time = datetime.now()

        timestamp = self._start_time.strftime("%Y%m%d_%H%M%S")
        dirname = f"{timestamp}_{technique}_autosave"
        self._output_dir = os.path.join(output_dir, dirname)
        os.makedirs(self._output_dir, exist_ok=True)

        self._started = True
        logger.info(
            "Incremental writer started: %s", self._output_dir
        )
        return os.path.abspath(self._output_dir)

    def flush_points(self, points: list[DataPoint]) -> int:
        """Append new data points to per-channel CSV files.

        Each point is routed to the appropriate channel file.
        Headers are written on first write per channel.  Files are
        flushed and synced to disk for crash safety.

        Args:
            points: New data points since the last flush.

        Returns:
            Number of points written.
        """
        if not self._started or not points:
            return 0

        with self._lock:
            written = 0
            # Group points by channel
            by_channel: dict[int, list[DataPoint]] = {}
            for dp in points:
                by_channel.setdefault(dp.channel, []).append(dp)

            for ch, ch_points in by_channel.items():
                self._ensure_channel_file(ch, ch_points)
                writer = self._writers[ch]
                columns = self._columns[ch]

                for dp in ch_points:
                    row: list[Any] = [dp.timestamp]
                    for col in columns:
                        row.append(dp.variables.get(col, ""))
                    writer.writerow(row)
                    written += 1

                # Flush to OS and sync to disk
                handle = self._handles[ch]
                handle.flush()
                os.fsync(handle.fileno())

            return written

    def finish(self) -> list[str]:
        """Close all file handles and return written file paths.

        Safe to call multiple times; subsequent calls return an
        empty list.

        Returns:
            List of absolute file paths written.
        """
        with self._lock:
            if not self._started:
                return []

            paths: list[str] = []
            for ch in sorted(self._handles.keys()):
                handle = self._handles[ch]
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                    paths.append(
                        os.path.abspath(handle.name)
                    )
                    handle.close()
                except OSError:
                    logger.warning(
                        "Error closing file for channel %d", ch
                    )

            self._handles.clear()
            self._writers.clear()
            self._columns.clear()
            self._header_written.clear()
            self._started = False

            logger.info(
                "Incremental writer finished: %d files",
                len(paths),
            )
            return paths

    # -- Internal -----------------------------------------------------------

    def _ensure_channel_file(
        self, channel: int, points: list[DataPoint]
    ) -> None:
        """Open file and write header for a channel if not yet done."""
        if channel in self._header_written:
            return

        # Determine columns from the first batch of points
        all_vars: set[str] = set()
        for dp in points:
            all_vars.update(dp.variables.keys())
        columns = _ordered_columns(self._technique, all_vars)
        self._columns[channel] = columns

        filepath = os.path.join(
            self._output_dir, f"ch{channel:02d}.csv"
        )
        handle = open(  # noqa: SIM115
            filepath, "w", newline="", encoding="utf-8"
        )
        self._handles[channel] = handle
        self._writers[channel] = csv.writer(handle)

        # Metadata header (matches CSVExporter format)
        handle.write(f"# Technique: {self._technique}\n")
        handle.write(f"# Channel: {channel}\n")
        if self._start_time is not None:
            handle.write(
                f"# Timestamp: {self._start_time.isoformat()}\n"
            )
        serial = self._device_info.get("serial", "")
        firmware = self._device_info.get("firmware", "")
        if serial:
            handle.write(f"# Device Serial: {serial}\n")
        if firmware:
            handle.write(f"# Firmware Version: {firmware}\n")
        if self._params:
            params_str = ", ".join(
                f"{k}={v}" for k, v in self._params.items()
            )
            handle.write(f"# Parameters: {params_str}\n")
        handle.write("#\n")

        # Column header row
        self._writers[channel].writerow(
            ["timestamp"] + columns
        )
        self._header_written.add(channel)

    @property
    def output_dir(self) -> str:
        """Return the output directory path."""
        return self._output_dir

    @property
    def is_active(self) -> bool:
        """Return whether the writer is currently active."""
        return self._started
