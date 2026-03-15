"""Data models for measurements and configuration.

Provides dataclasses that flow through the measurement pipeline:
``TechniqueConfig`` is built from GUI inputs, ``DataPoint`` instances
are emitted by the engine during acquisition, and ``MeasurementResult``
collects them for export. ``ChannelData`` offers a filtered view of
results for a single MUX channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TechniqueConfig:
    """Configuration for an electrochemical technique.

    Bundles the technique name with its parameter dictionary and the
    list of MUX channels to measure on.

    Attributes:
        technique: Technique identifier (e.g., 'cv', 'dpv', 'eis').
        params: Technique-specific parameters keyed by name
            (e.g., ``{'e_begin': -0.5, 'scan_rate': 0.1}``).
        channels: 1-indexed MUX channel numbers to scan (1-16).
    """

    technique: str
    params: dict
    channels: list[int]

    def __post_init__(self) -> None:
        """Normalize technique name to lowercase."""
        self.technique = self.technique.lower()


@dataclass
class DataPoint:
    """A single measurement data point from the device.

    Each data point carries decoded variable values from one packet
    line, tagged with the MUX channel and a timestamp.

    Attributes:
        timestamp: Time of acquisition (seconds from measurement start,
            or absolute datetime depending on context).
        channel: 1-indexed MUX channel that produced this data point.
        variables: Mapping of variable name to decoded float value
            (e.g., ``{'set_potential': 0.5, 'current': 1.2e-6}``).
    """

    timestamp: float
    channel: int
    variables: dict[str, float]

    def get(self, name: str, default: float = 0.0) -> float:
        """Return the value for a variable name, or *default*.

        Args:
            name: Variable name (e.g., 'current', 'measured_potential').
            default: Fallback value if *name* is not present.

        Returns:
            The variable value or *default*.
        """
        return self.variables.get(name, default)


@dataclass
class MeasurementResult:
    """Collected data points and metadata for a complete measurement run.

    Populated incrementally by the measurement engine during acquisition
    and consumed by exporters after the run completes.

    Attributes:
        data_points: All data points in acquisition order.
        technique: Technique identifier used for this run.
        start_time: Wall-clock time when the measurement started.
        device_info: Optional dict with device metadata (firmware
            version, serial number, port, etc.).
    """

    data_points: list[DataPoint] = field(default_factory=list)
    technique: str = ""
    start_time: Optional[datetime] = None
    device_info: Optional[dict[str, str]] = None

    def add_point(self, point: DataPoint) -> None:
        """Append a data point to the result buffer.

        Args:
            point: The decoded data point to add.
        """
        self.data_points.append(point)

    @property
    def channels(self) -> list[int]:
        """Return sorted list of unique channel numbers present."""
        return sorted({dp.channel for dp in self.data_points})

    def for_channel(self, channel: int) -> "ChannelData":
        """Return a ``ChannelData`` view filtered to one channel.

        Args:
            channel: 1-indexed MUX channel number.

        Returns:
            A ``ChannelData`` containing only points for *channel*.
        """
        filtered = [
            dp for dp in self.data_points if dp.channel == channel
        ]
        return ChannelData(
            channel=channel,
            data_points=filtered,
            technique=self.technique,
            start_time=self.start_time,
            device_info=self.device_info,
        )

    def __len__(self) -> int:
        return len(self.data_points)


@dataclass
class ChannelData:
    """Filtered view of a ``MeasurementResult`` for a single channel.

    Provides convenience accessors for extracting arrays of specific
    variables, useful for plotting and export.

    Attributes:
        channel: 1-indexed MUX channel number.
        data_points: Data points for this channel only.
        technique: Technique identifier from the parent result.
        start_time: Measurement start time from the parent result.
        device_info: Device metadata from the parent result.
    """

    channel: int
    data_points: list[DataPoint] = field(default_factory=list)
    technique: str = ""
    start_time: Optional[datetime] = None
    device_info: Optional[dict[str, str]] = None

    def values(self, name: str) -> list[float]:
        """Extract a list of values for a single variable name.

        Args:
            name: Variable name (e.g., 'current').

        Returns:
            List of float values in acquisition order. Points that
            do not contain *name* are skipped.
        """
        return [
            dp.variables[name]
            for dp in self.data_points
            if name in dp.variables
        ]

    def timestamps(self) -> list[float]:
        """Return timestamps for all data points in this channel."""
        return [dp.timestamp for dp in self.data_points]

    @property
    def variable_names(self) -> set[str]:
        """Return the set of variable names present across all points."""
        names: set[str] = set()
        for dp in self.data_points:
            names.update(dp.variables.keys())
        return names

    def __len__(self) -> int:
        return len(self.data_points)
