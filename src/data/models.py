"""Data models for measurements and configuration.

Provides dataclasses for technique configuration, individual data points,
full measurement results with metadata, and per-channel filtered views.
These models form the data layer shared between the engine, GUI, and
export modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class AutoSaveConfig:
    """Configuration for incremental auto-save during measurement.

    When enabled, CSV data is flushed to disk at each MUX loop
    boundary so that data is preserved even if the application
    crashes or the user aborts mid-experiment.

    Attributes:
        enabled: Whether auto-save is active.
        output_dir: Base directory for auto-saved files. A timestamped
            subdirectory is created automatically.
    """

    enabled: bool = False
    output_dir: str = ""


@dataclass
class TechniqueConfig:
    """Configuration for an electrochemical technique.

    Bundles the technique name, its parameters, and the list of MUX
    channels to measure on.

    Attributes:
        technique: Lowercase technique identifier (e.g., 'cv', 'dpv',
            'eis'). Must match a key in the technique script registry.
        params: Technique-specific parameter dictionary. Keys and
            expected types vary by technique (e.g., ``e_begin``,
            ``scan_rate``, ``freq_start``).
        channels: 1-indexed MUX channel numbers to include in the
            measurement (e.g., ``[1, 2, 5]``).
        auto_save: Optional auto-save configuration. When provided and
            enabled, measurement data is written incrementally to CSV.
    """

    technique: str
    params: dict[str, Any]
    channels: list[int]
    auto_save: Optional[AutoSaveConfig] = None
    continuous: bool = False

    def __post_init__(self) -> None:
        """Normalise technique name to lowercase."""
        self.technique = self.technique.lower()


@dataclass
class DataPoint:
    """A single decoded measurement sample.

    Represents one data packet from the device, containing one or more
    named measurement values (potential, current, impedance, etc.).

    Attributes:
        timestamp: Time of acquisition in seconds relative to
            measurement start. ``None`` if not yet assigned.
        channel: 1-indexed MUX channel that produced this sample.
        variables: Mapping of variable names to float values
            (e.g., ``{'set_potential': 0.5, 'current': 1.2e-6}``).
    """

    timestamp: Optional[float]
    channel: int
    variables: dict[str, float] = field(default_factory=dict)

    def get(self, name: str, default: float = 0.0) -> float:
        """Return the value of a named variable.

        Args:
            name: Variable name (e.g., 'current', 'set_potential').
            default: Value to return if the variable is not present.

        Returns:
            The float value, or *default* if not found.
        """
        return self.variables.get(name, default)


@dataclass
class MeasurementResult:
    """Complete result of a measurement run.

    Collects all data points from a single measurement execution along
    with metadata describing the run.

    Attributes:
        data_points: Ordered list of all decoded data points.
        technique: Lowercase technique identifier used for this run.
        start_time: Wall-clock time when the measurement started.
        device_info: Optional dict with device metadata (e.g.,
            ``{'firmware': '...', 'serial': '...'}``).
        params: Copy of the technique parameters used.
        channels: List of channels that were measured.
    """

    data_points: list[DataPoint] = field(default_factory=list)
    technique: str = ""
    start_time: Optional[datetime] = None
    device_info: dict[str, str] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    channels: list[int] = field(default_factory=list)

    def add_point(self, point: DataPoint) -> None:
        """Append a data point to the result.

        Args:
            point: The decoded data point to add.
        """
        self.data_points.append(point)

    def channel_data(self, channel: int) -> ChannelData:
        """Return a filtered view for a single channel.

        Args:
            channel: 1-indexed channel number.

        Returns:
            A ``ChannelData`` instance containing only the data points
            from the specified channel.
        """
        filtered = [
            dp for dp in self.data_points if dp.channel == channel
        ]
        return ChannelData(
            channel=channel,
            data_points=filtered,
            technique=self.technique,
            params=self.params,
        )

    @property
    def num_points(self) -> int:
        """Return the total number of data points."""
        return len(self.data_points)

    @property
    def measured_channels(self) -> list[int]:
        """Return sorted list of channels that have data points."""
        return sorted({dp.channel for dp in self.data_points})


@dataclass
class ChannelData:
    """Filtered view of measurement data for a single channel.

    Provides convenient access to the data points belonging to one
    MUX channel within a ``MeasurementResult``.

    Attributes:
        channel: 1-indexed channel number.
        data_points: Data points for this channel only.
        technique: Technique identifier.
        params: Technique parameters.
    """

    channel: int
    data_points: list[DataPoint] = field(default_factory=list)
    technique: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def num_points(self) -> int:
        """Return the number of data points for this channel."""
        return len(self.data_points)

    def values(self, name: str) -> list[float]:
        """Extract a list of values for a named variable.

        Args:
            name: Variable name (e.g., 'current').

        Returns:
            List of float values in acquisition order. Points that
            lack the variable are skipped.
        """
        return [
            dp.variables[name]
            for dp in self.data_points
            if name in dp.variables
        ]

    def timestamps(self) -> list[float]:
        """Return timestamps for all data points in this channel.

        Returns:
            List of timestamp floats. Points with ``None`` timestamps
            are excluded.
        """
        return [
            dp.timestamp
            for dp in self.data_points
            if dp.timestamp is not None
        ]
