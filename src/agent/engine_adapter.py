"""EngineAdapter: agent-facing measurement and device operations.

Wraps an injected engine + connection pair (the real
``MeasurementEngine``/``PicoConnection`` or the mocks from
:mod:`src.agent.mock_engine` -- the adapter never constructs hardware
objects itself) and exposes the async tool surface the agent uses:

* ``run_cv`` / ``run_ca`` / ``run_cp`` / ``run_eis`` / ``run_geis`` --
  build a validated ``TechniqueConfig`` from technique defaults plus
  user args, marshal ``engine.start_measurement`` onto the GUI thread
  via :func:`src.agent.bridge.run_on_gui` (the engine must be started
  from the GUI thread), and await the race between
  ``measurement_finished`` and ``measurement_error`` through a
  thread-safe future connected BEFORE the start call (no missed-signal
  window).  On success a COMPACT summary dict is returned -- never the
  raw data arrays.
* Device tools: ``list_ports`` (pyserial enumeration, imported eagerly
  at module top), ``connect_device`` / ``disconnect_device`` (marshaled
  onto the GUI thread, which owns the connection), ``device_status``,
  and ``abort_measurement``.

Every operation returns a structured dict with an ``ok`` flag instead
of raising, so the tool-dispatch layer can hand results straight back
to the model.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

# Eager import per the blueprint constraint: native/third-party deps are
# imported at module top, before any asyncio loop exists.
from serial.tools import list_ports as _serial_list_ports

from src.agent.bridge import SignalTimeoutError, await_signal, run_on_gui
from src.data.exporters import PsSessionExporter
from src.data.models import TechniqueConfig
from src.techniques.scripts import supported_techniques, technique_params

logger = logging.getLogger(__name__)

# Default directory for agent-initiated .pssession exports (relative to
# the process working directory, i.e. the repo root when launched the
# documented way).
_DEFAULT_EXPORT_DIR = "agent_exports"

__all__ = ["EngineAdapter", "build_technique_config"]


def build_technique_config(
    technique: str, args: Optional[dict[str, Any]] = None
) -> TechniqueConfig:
    """Build a validated ``TechniqueConfig`` from defaults plus args.

    Starts from ``technique_params(technique)`` and overlays the user
    args.  Unknown parameter keys are rejected with a message listing
    the allowed keys.  Wiring validation (electrode mode, RE/CE list,
    channel ranges) is delegated to ``TechniqueConfig.__post_init__``;
    its ``ValueError`` is re-raised with a clean message.

    Args:
        technique: Technique identifier (case-insensitive, e.g.
            ``"cv"``).
        args: Optional dict of technique parameters plus the config
            keys ``channels`` (default ``[1]``),
            ``electrode_config_mode`` (default ``"external"``),
            ``re_ce_channels`` (required for ``"manual"`` mode), and
            ``continuous``.

    Returns:
        A fully validated ``TechniqueConfig``.

    Raises:
        ValueError: For unknown techniques, unknown parameter keys,
            malformed channel lists, or wiring-rule violations.
    """
    merged_args = dict(args or {})
    channels = merged_args.pop("channels", None)
    mode = str(merged_args.pop("electrode_config_mode", "external")).lower()
    re_ce_channels = merged_args.pop("re_ce_channels", None)
    continuous = bool(merged_args.pop("continuous", False))

    try:
        defaults = technique_params(technique)
    except ValueError:
        raise ValueError(
            f"Unknown technique {technique!r}. "
            f"Hardware-verified techniques: {supported_techniques()}"
        ) from None

    unknown = sorted(set(merged_args) - set(defaults))
    if unknown:
        raise ValueError(
            f"Unknown parameter(s) for technique {technique!r}: "
            f"{unknown}. Allowed parameters: {sorted(defaults)}"
        )
    params = {**defaults, **merged_args}

    if channels is None:
        channels = [1]
    if (
        not isinstance(channels, (list, tuple))
        or not channels
        or any(
            not isinstance(ch, int) or isinstance(ch, bool)
            for ch in channels
        )
    ):
        raise ValueError(
            "channels must be a non-empty list of integers "
            f"(1-16), got {channels!r}"
        )

    if mode == "manual" and not re_ce_channels:
        raise ValueError(
            "electrode_config_mode 'manual' requires an explicit "
            "re_ce_channels list (one RE/CE position per channel)."
        )

    try:
        return TechniqueConfig(
            technique=technique.lower(),
            params=params,
            channels=list(channels),
            continuous=continuous,
            re_ce_channels=(
                list(re_ce_channels) if re_ce_channels else []
            ),
            electrode_config_mode=mode,
        )
    except ValueError as exc:
        raise ValueError(f"Invalid technique configuration: {exc}") from None


class EngineAdapter:
    """Async adapter between the agent tool layer and the engine.

    The engine and connection are injected so the real hardware pair
    and the mocks substitute cleanly; this class never imports the real
    engine or opens serial ports itself.

    Required engine surface (real ``MeasurementEngine`` and
    ``MockMeasurementEngine`` both provide it): signals
    ``measurement_finished(object)`` / ``measurement_error(str)``,
    methods ``start_measurement(connection, config)`` / ``abort()`` /
    ``isRunning()``.

    Required connection surface: ``connect(port)`` / ``disconnect()`` /
    ``is_connected`` / ``port`` / ``firmware_version`` /
    ``serial_number``.

    All ``run_*`` coroutines follow the mandated sequence: connect
    one-shot completion slots first, then marshal the start call onto
    the GUI thread, then suspend on the wrapped future.  They must be
    awaited from the agent thread's asyncio loop.
    """

    def __init__(self, engine: Any, connection: Any) -> None:
        """Initialize the adapter.

        Args:
            engine: A ``MeasurementEngine``-compatible object (real or
                mock).  Must live on the GUI thread.
            connection: A ``PicoConnection``-compatible object (real or
                mock).
        """
        self._engine = engine
        self._connection = connection
        # True while the run currently in (or just out of) the engine
        # was started by the agent. GUI-thread-confined: set inside the
        # run_on_gui start closure, consumed by the main window's
        # finished/error handlers (also GUI thread), so the window can
        # suppress its modal prompts for agent-driven runs without any
        # cross-thread race.
        self._agent_run_active = False

    def consume_agent_run(self) -> bool:
        """Return whether the finishing run was agent-started, and clear.

        Call from the GUI thread (the engine's finished/error handler).
        Consume-once semantics: every engine termination emits exactly
        one of finished/error, so whichever handler runs takes the flag
        and the next user-started run can never be misattributed.
        """
        was = self._agent_run_active
        self._agent_run_active = False
        return was

    def _start_agent_run(self, config: TechniqueConfig) -> None:
        """GUI-thread helper: mark the run agent-initiated, then start.

        Marking and starting happen in one GUI-thread closure so the
        flag is always set before any engine signal can be delivered to
        the GUI handlers; a start failure unwinds the mark.
        """
        self._agent_run_active = True
        try:
            self._engine.start_measurement(self._connection, config)
        except Exception:
            self._agent_run_active = False
            raise

    # ---- Measurement tools -------------------------------------------------

    async def run_technique(
        self,
        technique: str,
        params: Optional[dict[str, Any]] = None,
        *,
        channels: Optional[list[int]] = None,
        electrode_config_mode: Optional[str] = None,
        re_ce_channels: Optional[list[int]] = None,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Run one measurement to completion and return a summary.

        Sequence (architecture.md mandated): reject if busy; build the
        config; connect the finished/error race future BEFORE starting;
        marshal ``start_measurement`` onto the GUI thread; await the
        future via ``asyncio.wrap_future``.

        Args:
            technique: Technique identifier (e.g. ``"cv"``).
            params: Technique parameter overrides (may also carry the
                config keys; explicit keyword arguments below win).
            channels: 1-indexed MUX channels (default ``[1]``).
            electrode_config_mode: ``"external"`` / ``"on_board"`` /
                ``"manual"``.
            re_ce_channels: Per-channel RE/CE positions (manual mode).
            timeout: Optional seconds to wait for completion before
                failing with a timeout error result.

        Returns:
            On success a compact summary dict (``ok=True``, technique,
            point counts, channels, variable names, params echo).  On
            any failure a structured error dict (``ok=False``,
            ``error`` message).  Raw data arrays are never included.
        """
        if self._engine.isRunning():
            return self._error(
                technique,
                "Engine is busy: a measurement is already running. "
                "Abort it or wait for it to finish before starting "
                "another.",
            )

        merged = dict(params or {})
        if channels is not None:
            merged["channels"] = channels
        if electrode_config_mode is not None:
            merged["electrode_config_mode"] = electrode_config_mode
        if re_ce_channels is not None:
            merged["re_ce_channels"] = re_ce_channels
        try:
            config = build_technique_config(technique, merged)
        except ValueError as exc:
            return self._error(technique, str(exc))

        # Connect the one-shot finished/error race BEFORE starting so a
        # fast (or synchronous) completion can never be missed.
        future = await_signal(
            self._engine.measurement_finished,
            self._engine.measurement_error,
            timeout=timeout,
        )
        try:
            await run_on_gui(self._start_agent_run, config)
        except Exception as exc:
            # E.g. RuntimeError from a busy engine that won the race
            # against our isRunning() pre-check. Cancelling detaches
            # the one-shot slots immediately (done-callback cleanup).
            future.cancel()
            logger.error("start_measurement failed: %s", exc)
            return self._error(
                technique, f"Failed to start measurement: {exc}"
            )

        try:
            result = await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            # The agent turn was cancelled mid-measurement (user pressed
            # Stop). CancelledError is a BaseException, so it is NOT caught
            # by the `except Exception` below -- without this the LLM turn
            # unwinds but the engine keeps sweeping the remaining channels
            # (and, for banded EIS, the remaining bands). Abort the in-flight
            # run so the device actually stops, then re-raise so the turn
            # unwinds normally. engine.abort() is thread-safe (same call the
            # abort_measurement tool uses).
            try:
                self._engine.abort()
                logger.info(
                    "Measurement aborted: agent turn cancelled (Stop)."
                )
            except Exception as abort_exc:  # noqa: BLE001 - best-effort stop
                logger.warning("Abort on turn-cancel failed: %s", abort_exc)
            raise
        except Exception as exc:
            # measurement_error payload, or SignalTimeoutError.
            if isinstance(exc, SignalTimeoutError):
                # A timed-out await must not leave the cell energized
                # and the engine busy: best-effort abort, mirroring the
                # engine's own error-path cell-off policy.
                try:
                    self._engine.abort()
                except Exception as abort_exc:
                    logger.warning(
                        "Abort after await timeout failed: %s", abort_exc
                    )
            logger.error("Measurement failed: %s", exc)
            return self._error(technique, str(exc))

        summary = self._summarize(result, config)
        logger.info(
            "Measurement summary: %s, %d points on channels %s.",
            summary["technique"],
            summary["num_points"],
            summary["measured_channels"],
        )
        return summary

    async def run_cv(
        self, params: Optional[dict[str, Any]] = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a cyclic voltammetry measurement (see ``run_technique``)."""
        return await self.run_technique("cv", params, **kwargs)

    async def run_ca(
        self, params: Optional[dict[str, Any]] = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a chronoamperometry measurement (see ``run_technique``)."""
        return await self.run_technique("ca", params, **kwargs)

    async def run_cp(
        self, params: Optional[dict[str, Any]] = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a chronopotentiometry measurement (see ``run_technique``)."""
        return await self.run_technique("cp", params, **kwargs)

    async def run_eis(
        self, params: Optional[dict[str, Any]] = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a potentiostatic EIS measurement (see ``run_technique``)."""
        return await self.run_technique("eis", params, **kwargs)

    async def run_geis(
        self, params: Optional[dict[str, Any]] = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Run a galvanostatic EIS measurement (see ``run_technique``)."""
        return await self.run_technique("geis", params, **kwargs)

    def abort_measurement(self) -> dict[str, Any]:
        """Request abort of the running measurement.

        ``engine.abort()`` is documented thread-safe on both the real
        and mock engines, so no GUI-thread marshaling is required.

        Returns:
            ``{"ok": True, ...}`` when an abort was requested,
            ``{"ok": False, ...}`` when nothing was running.
        """
        if not self._engine.isRunning():
            return {"ok": False, "error": "No measurement is running."}
        self._engine.abort()
        logger.info("Abort requested via agent adapter.")
        return {
            "ok": True,
            "message": (
                "Abort requested. The run will end with the error "
                "'Measurement aborted by user.'"
            ),
        }

    def export_session(
        self, path: Optional[str] = None
    ) -> dict[str, Any]:
        """Export the last finished measurement to a .pssession file.

        Bridges the run -> characterize gap: the analysis tools only
        read .pssession files, so the agent chains run_* ->
        export_session -> analyze_*. Uses the app's existing
        ``PsSessionExporter`` (PSTrace-fidelity validated), reading
        ``engine.result`` after the engine is idle -- pure data-to-file
        work, so no GUI-thread marshaling is required.

        Args:
            path: Optional output file or directory. A directory (or a
                trailing slash) gets an auto-generated
                ``<technique>_<timestamp>.pssession`` name; a file path
                without the .pssession suffix has it appended; omitted
                entirely, the file goes to ``agent_exports/``.

        Returns:
            ``{"ok": True, "path": ..., "technique": ...,
            "num_points": ..., "channels": [...]}`` on success,
            ``{"ok": False, "error": ...}`` otherwise.
        """
        if self._engine.isRunning():
            return {
                "ok": False,
                "error": (
                    "A measurement is still running; wait for it to "
                    "finish (or abort_measurement) before exporting."
                ),
            }
        result = getattr(self._engine, "result", None)
        if result is None or result.num_points == 0:
            return {
                "ok": False,
                "error": (
                    "No finished measurement to export. Run a "
                    "measurement first."
                ),
            }

        if path:
            out = str(path)
            if out.endswith(("/", "\\")) or os.path.isdir(out):
                out = os.path.join(out, self._export_name(result))
            elif not out.lower().endswith(".pssession"):
                out = out + ".pssession"
        else:
            out = os.path.join(
                _DEFAULT_EXPORT_DIR, self._export_name(result)
            )

        try:
            abs_path = PsSessionExporter().export_pssession(result, out)
        except Exception as exc:  # noqa: BLE001 - surfaced to the model
            logger.exception("export_session failed")
            return {
                "ok": False,
                "error": (
                    f"Export failed: {type(exc).__name__}: {exc}"
                ),
            }
        logger.info("Agent exported session: %s", abs_path)
        return {
            "ok": True,
            "path": abs_path,
            "technique": result.technique,
            "num_points": result.num_points,
            "channels": result.measured_channels,
        }

    @staticmethod
    def _export_name(result: Any) -> str:
        """Build ``<technique>_<timestamp>.pssession`` for *result*."""
        started = getattr(result, "start_time", None) or datetime.now()
        stamp = started.strftime("%Y%m%d_%H%M%S")
        technique = getattr(result, "technique", "") or "session"
        return f"{technique}_{stamp}.pssession"

    # ---- Device tools --------------------------------------------------------

    def list_ports(self) -> dict[str, Any]:
        """Enumerate available serial ports.

        Returns:
            ``{"ok": True, "ports": [{"device", "description",
            "hwid"}, ...]}``.
        """
        ports = [
            {
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            }
            for p in _serial_list_ports.comports()
        ]
        return {"ok": True, "ports": ports}

    async def connect_device(self, port: str) -> dict[str, Any]:
        """Connect the injected connection to *port* on the GUI thread.

        Args:
            port: Serial port name (e.g. ``"COM6"``); any string is
                accepted by ``MockConnection``.

        Returns:
            Status dict with ``ok`` flag; on success it includes the
            full ``device_status()`` payload.
        """
        if self._engine.isRunning():
            return {
                "ok": False,
                "error": (
                    "Cannot (re)connect while a measurement is "
                    "running. Abort it first."
                ),
            }
        try:
            await run_on_gui(self._connection.connect, port)
        except Exception as exc:
            logger.error("connect_device(%s) failed: %s", port, exc)
            return {
                "ok": False,
                "error": f"Failed to connect to {port}: {exc}",
            }
        status = self.device_status()
        status["ok"] = True
        return status

    async def disconnect_device(self) -> dict[str, Any]:
        """Disconnect the injected connection on the GUI thread.

        Returns:
            Status dict with ``ok`` flag.
        """
        if self._engine.isRunning():
            return {
                "ok": False,
                "error": (
                    "Cannot disconnect while a measurement is "
                    "running. Abort it first."
                ),
            }
        try:
            await run_on_gui(self._connection.disconnect)
        except Exception as exc:
            logger.error("disconnect_device failed: %s", exc)
            return {"ok": False, "error": f"Failed to disconnect: {exc}"}
        status = self.device_status()
        status["ok"] = True
        return status

    def device_status(self) -> dict[str, Any]:
        """Return connection and engine state.

        Returns:
            Dict with ``ok``, ``connected``, ``port``, ``firmware``,
            ``serial`` and ``engine_running``.
        """
        connection = self._connection
        return {
            "ok": True,
            "connected": bool(connection.is_connected),
            "port": getattr(connection, "port", None),
            "firmware": getattr(connection, "firmware_version", None),
            "serial": getattr(connection, "serial_number", None),
            "engine_running": bool(self._engine.isRunning()),
        }

    # ---- Internal helpers ------------------------------------------------------

    @staticmethod
    def _error(technique: str, message: str) -> dict[str, Any]:
        """Build a structured error result."""
        return {"ok": False, "technique": technique.lower(),
                "error": message}

    @staticmethod
    def _summarize(result: Any, config: TechniqueConfig) -> dict[str, Any]:
        """Build the compact success summary from a MeasurementResult.

        Never includes raw data arrays -- only counts, channel lists,
        variable names, and the parameter echo.
        """
        points_per_channel: dict[str, int] = {}
        variables: set[str] = set()
        for dp in result.data_points:
            key = str(dp.channel)
            points_per_channel[key] = points_per_channel.get(key, 0) + 1
            variables.update(dp.variables)
        return {
            "ok": True,
            "technique": result.technique,
            "num_points": result.num_points,
            "measured_channels": result.measured_channels,
            "points_per_channel": points_per_channel,
            "variables": sorted(variables),
            "channels_requested": list(config.channels),
            "re_ce_channels": list(config.re_ce_channels),
            "electrode_config_mode": config.electrode_config_mode,
            "params": dict(result.params),
            "start_time": (
                result.start_time.isoformat()
                if result.start_time is not None
                else None
            ),
        }
