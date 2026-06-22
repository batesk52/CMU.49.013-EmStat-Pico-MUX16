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
import math
import os
import statistics
from datetime import datetime
from typing import Any, Optional

# Eager import per the blueprint constraint: native/third-party deps are
# imported at module top, before any asyncio loop exists.
from serial.tools import list_ports as _serial_list_ports

from src.agent.bridge import SignalTimeoutError, await_signal, run_on_gui
from src.data.exporters import PsSessionExporter
from src.data.models import TechniqueConfig
from src.techniques.scripts import (
    EIS_CURRENT_RANGES,
    next_larger_eis_range,
    supported_techniques,
    technique_params,
)

logger = logging.getLogger(__name__)

# Default directory for agent-initiated .pssession exports (relative to
# the process working directory, i.e. the repo root when launched the
# documented way).
_DEFAULT_EXPORT_DIR = "agent_exports"

__all__ = [
    "EngineAdapter",
    "build_technique_config",
    "cv_noise",
    "eis_quality",
]

# Techniques whose run summary carries an EIS data-quality assessment.
_EIS_TECHNIQUES = ("eis", "geis")

# A current-vs-time trace (CV/CA/…) is flagged noisy when its high-frequency
# ripple — a robust estimate of point-to-point oscillation, divided by the
# current span — exceeds this fraction. The dominant real-world cause is 50/60
# Hz mains pickup, fixable by lowering the measurement bandwidth (bw_hz). The
# default is a conservative starting point; calibrate it against a clean-vs-noisy
# pair from the actual rig (the auto-range/noise rehearsal).
_NOISE_RIPPLE_FLAG = 0.02

# Minimum current points needed for a meaningful ripple estimate.
_NOISE_MIN_POINTS = 8

# A channel is flagged under-ranged when at least this fraction of its
# impedance points carry a DEVICE-AUTHORITATIVE under-range signal (the
# overload status bit or a NaN reading). On a 50-point sweep this is ≥5
# points; combined with the absolute floor below, a single stray bad point
# never trips the flag.
_EIS_BAD_FRACTION_FLAG = 0.10

# Absolute floor (points), independent of sweep length: never flag a channel
# under-ranged on a single bad point. This also fixes the small-sweep edge
# (without it, 1 bad point on a ≤10-point sweep would reach the 10% fraction).
_EIS_MIN_BAD_POINTS = 2

# Negative real-Z is NOT treated as a primary under-range signal: the project's
# own EIS noise investigation attributes scattered negative-Z' points to mains
# (50/60 Hz) pickup on CORRECTLY-ranged sweeps, and stepping the range up cannot
# remove them — so flagging on them would chase a non-existent range fault. It
# only contributes when a LARGE fraction of the sweep is negative (an inverted
# Nyquist arc that mains cannot explain); the authoritative overload/NaN signals
# drive the normal case.
_EIS_NEG_ZREAL_FRACTION_FLAG = 0.5

# Fallback range when the used range is not on the mode-3 ladder (the agent
# passed an out-of-ladder value): re-range to a safe mid-ladder default.
_EIS_FALLBACK_CR = "100u"


def eis_quality(
    result: Any,
    channels_requested: list[int],
    used_cr: str,
) -> dict[str, Any]:
    """Assess EIS/GEIS data quality per channel for agent auto-ranging.

    A too-small (under-ranged) current range makes the EIS current rail the
    pinned range; the device then reports an overload status bit or emits NaN
    readings on the clipped points. Those two signals are device-authoritative
    and drive the verdict. Negative real-Z is a NOISY proxy — the project's own
    bench data attributes scattered negative-Z' to mains pickup on correctly-
    ranged sweeps — so it only contributes when a large fraction of the sweep is
    negative (an inverted arc). When a channel is flagged, the function suggests
    stepping one rung UP the mode-3 ladder. (Over-ranging is the opposite, softer
    problem — small-but-valid Z with poor resolution — and is left to the agent's
    judgement; only under-range signatures are auto-flagged here.)

    Args:
        result: The finished ``MeasurementResult``.
        channels_requested: Channels the run asked for (so a requested
            channel that returned no impedance points is reported as
            ``no_data`` rather than silently omitted).
        used_cr: The current range the sweep ran at (SI string, e.g.
            ``'1u'``), used to compute the suggested next range.

    Returns:
        A compact dict: ``quality_ok`` (bool), ``per_channel`` (dict keyed
        by channel string with point/overload/NaN/negative-Z counts, the
        bad fraction, and a per-channel verdict), ``suggested_cr`` (the
        recommended larger range, or None), ``rerange_exhausted`` (True when
        the channel is still bad at the largest range — stop re-ranging), and
        ``note`` (a one-line human summary for the model).
    """
    # Bucket impedance-bearing points by channel in a single pass (avoids an
    # O(channels × points) re-scan of the full point list per channel).
    by_channel: dict[int, list[Any]] = {}
    for dp in result.data_points:
        if "zreal" in dp.variables or "impedance" in dp.variables:
            by_channel.setdefault(dp.channel, []).append(dp)

    per_channel: dict[str, dict[str, Any]] = {}
    channels = sorted(
        set(int(c) for c in channels_requested) | set(by_channel)
    )
    for ch in channels:
        imp_pts = by_channel.get(ch, [])
        n = len(imp_pts)
        overload_n = nan_n = neg_n = auth_bad_n = 0
        for dp in imp_pts:
            zr = dp.variables.get("zreal")
            zi = dp.variables.get("zimag")
            zmag = dp.variables.get("impedance")
            is_nan = any(
                v is not None and math.isnan(v) for v in (zr, zi, zmag)
            )
            is_neg = (
                zr is not None and not math.isnan(zr) and zr < 0.0
            )
            is_overload = bool(getattr(dp, "overload", False))
            if is_overload:
                overload_n += 1
            if is_nan:
                nan_n += 1
            if is_neg:
                neg_n += 1
            # Authoritative under-range signals only (overload bit / NaN).
            if is_overload or is_nan:
                auth_bad_n += 1
        if n == 0:
            verdict = "no_data"
            bad_fraction = 1.0
        else:
            bad_fraction = auth_bad_n / n
            authoritative = (
                auth_bad_n >= _EIS_MIN_BAD_POINTS
                and bad_fraction >= _EIS_BAD_FRACTION_FLAG
            )
            # Inverted-arc backstop: a large negative-Z' fraction is a genuine
            # corruption signature that mains pickup (a few scattered points)
            # cannot produce.
            inverted_arc = (
                neg_n >= _EIS_MIN_BAD_POINTS
                and (neg_n / n) >= _EIS_NEG_ZREAL_FRACTION_FLAG
            )
            verdict = (
                "underranged" if (authoritative or inverted_arc) else "ok"
            )
        per_channel[str(ch)] = {
            "points": n,
            "overload_points": overload_n,
            "nan_points": nan_n,
            "neg_zreal_points": neg_n,
            "bad_fraction": round(bad_fraction, 3),
            "verdict": verdict,
        }

    flagged = [c for c, q in per_channel.items() if q["verdict"] != "ok"]
    quality_ok = not flagged
    suggested_cr: Optional[str] = None
    rerange_exhausted = False
    if quality_ok:
        note = "EIS data quality looks good on all measured channels."
    else:
        suggested_cr = next_larger_eis_range(used_cr)
        if suggested_cr is None:
            if str(used_cr) == EIS_CURRENT_RANGES[-1]:
                # Already at the top of the ladder and still bad: this is no
                # longer a range problem. Signal a hard stop so the agent does
                # not loop re-running the largest range.
                rerange_exhausted = True
                note = (
                    f"Channel(s) {flagged} still show overload / NaN at the "
                    f"largest current range ({used_cr}). This is no longer a "
                    "current-range problem — STOP re-ranging and check the "
                    "electrode contact, wiring, and cell, or whether the "
                    "channel is open/dead."
                )
            else:
                # Used range not on the mode-3 ladder: re-range to a safe
                # mid-ladder default rather than guessing a neighbour.
                suggested_cr = _EIS_FALLBACK_CR
                note = (
                    f"Channel(s) {flagged} returned overload / NaN or no data "
                    f"at current range {used_cr!r}, which is not a valid "
                    f"mode-3 EIS range. Re-run at a valid range such as "
                    f"{suggested_cr}."
                )
        else:
            note = (
                f"Channel(s) {flagged} look under-ranged at {used_cr} "
                "(the current railed the range → overload / NaN). Re-run "
                f"those channel(s) at a LARGER current range, e.g. "
                f"{suggested_cr} (jump several rungs if most points are bad), "
                "then continue."
            )

    return {
        "quality_ok": quality_ok,
        "per_channel": per_channel,
        "suggested_cr": suggested_cr,
        "rerange_exhausted": rerange_exhausted,
        "note": note,
    }


def cv_noise(
    result: Any,
    channels_requested: list[int],
) -> dict[str, Any]:
    """Assess per-channel high-frequency ripple on a current trace.

    Gives the agent a NUMBER for trace "noise" it cannot otherwise see (it only
    receives metrics, never the plot pixels), so it can scope settings the same
    way it auto-ranges EIS: run a quick (small-window) CV, read the ripple, lower
    the measurement bandwidth (``bw_hz``), and re-run until the ripple drops.

    The ripple estimate is the robust standard deviation of the current's
    discrete second difference, divided by the current span. The second
    difference cancels the smooth CV/CA trend so only point-to-point oscillation
    remains; the median-absolute-deviation makes it insensitive to the few sharp
    points at scan vertices / faradaic onsets. A smooth trace gives ≈0; 50/60 Hz
    mains pickup gives a clearly elevated value. It is a comparable indicator
    (drive it down across re-runs), not a calibrated noise amplitude.

    Args:
        result: The finished ``MeasurementResult``.
        channels_requested: Channels the run asked for (so a requested channel
            with too few current points is reported as ``insufficient_data``).

    Returns:
        ``{"noise_ok": bool, "per_channel": {ch: {points, ripple_ratio,
        verdict}}, "note": str}``. ``ripple_ratio`` is None when there are too
        few points; ``verdict`` is ``"clean"`` / ``"elevated"`` /
        ``"insufficient_data"``.
    """
    by_channel: dict[int, list[float]] = {}
    for dp in result.data_points:
        c = dp.variables.get("current")
        if c is None:
            continue
        c = float(c)
        if not math.isnan(c):
            by_channel.setdefault(dp.channel, []).append(c)

    per_channel: dict[str, dict[str, Any]] = {}
    channels = sorted(
        set(int(c) for c in channels_requested) | set(by_channel)
    )
    for ch in channels:
        cur = by_channel.get(ch, [])
        n = len(cur)
        if n < _NOISE_MIN_POINTS:
            per_channel[str(ch)] = {
                "points": n,
                "ripple_ratio": None,
                "verdict": "insufficient_data",
            }
            continue
        span = max(cur) - min(cur)
        # Robust high-frequency noise estimate from the second difference.
        # var(2nd diff) = 6·σ² for i.i.d. noise, so divide the robust σ of the
        # second difference by √6 to recover the per-point noise scale.
        d2 = [
            cur[i - 1] - 2.0 * cur[i] + cur[i + 1]
            for i in range(1, n - 1)
        ]
        med = statistics.median(d2)
        mad = statistics.median([abs(x - med) for x in d2])
        sigma_hf = 1.4826 * mad / math.sqrt(6.0)
        ripple_ratio = (sigma_hf / span) if span > 0 else 0.0
        verdict = (
            "elevated" if ripple_ratio >= _NOISE_RIPPLE_FLAG else "clean"
        )
        per_channel[str(ch)] = {
            "points": n,
            "ripple_ratio": round(ripple_ratio, 4),
            "verdict": verdict,
        }

    flagged = [c for c, q in per_channel.items() if q["verdict"] == "elevated"]
    noise_ok = not flagged
    if noise_ok:
        note = (
            "Traces look clean (low high-frequency ripple) on all measured "
            "channels."
        )
    else:
        note = (
            f"Channel(s) {flagged} show elevated high-frequency ripple — "
            "usually 50/60 Hz mains pickup. Lower the max bandwidth (bw_hz, "
            "e.g. 400 -> 40) and re-run; compare ripple_ratio to confirm it "
            "dropped."
        )
    return {"noise_ok": noise_ok, "per_channel": per_channel, "note": note}


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
            is_timeout = isinstance(exc, SignalTimeoutError)
            if is_timeout:
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
            err = self._error(technique, str(exc))
            # A too-small pinned EIS range can make the device REJECT the sweep
            # (e!xxxx) rather than complete with railed data, so the success-
            # path quality block never runs. Carry a re-range hint on the error
            # so the agent can still step the range up. Skipped for timeouts
            # (a slow low-frequency point, not a range fault).
            if technique.lower() in _EIS_TECHNIQUES and not is_timeout:
                used_cr = str(config.params.get("cr", _EIS_FALLBACK_CR))
                suggested = next_larger_eis_range(used_cr)
                if suggested is None and used_cr != EIS_CURRENT_RANGES[-1]:
                    suggested = _EIS_FALLBACK_CR
                if suggested is not None:
                    err["suggested_cr"] = suggested
                    err["hint"] = (
                        f"The device errored during EIS at current range "
                        f"{used_cr}. If you chose a small range it may be far "
                        f"too small — re-run at a larger range (e.g. "
                        f"{suggested}) before giving up."
                    )
            return err

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
        overload_points = 0
        for dp in result.data_points:
            key = str(dp.channel)
            points_per_channel[key] = points_per_channel.get(key, 0) + 1
            variables.update(dp.variables)
            if getattr(dp, "overload", False):
                overload_points += 1
        summary: dict[str, Any] = {
            "ok": True,
            "technique": result.technique,
            "num_points": result.num_points,
            "measured_channels": result.measured_channels,
            "points_per_channel": points_per_channel,
            "overload_points": overload_points,
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
        # EIS/GEIS: attach a per-channel data-quality block so the agent can
        # detect an under-ranged sweep (current railed the range → overload /
        # NaN / negative-Z') and re-range up the mode-3 ladder before moving
        # on. Other techniques carry only the generic ``overload_points``.
        if result.technique in _EIS_TECHNIQUES:
            used_cr = str(result.params.get("cr", _EIS_FALLBACK_CR))
            q = eis_quality(result, list(config.channels), used_cr)
            summary["quality_ok"] = q["quality_ok"]
            summary["quality"] = q["per_channel"]
            summary["quality_note"] = q["note"]
            # Always present so the model has an unambiguous stop signal: True
            # means the largest range is still bad (fault is the cell/wiring,
            # not the range) — do not re-range further.
            summary["rerange_exhausted"] = q["rerange_exhausted"]
            if q["suggested_cr"] is not None:
                summary["suggested_cr"] = q["suggested_cr"]
        # Current-vs-time techniques (CV, CA, …): attach a per-channel ripple
        # block so the agent can scope settings against mains/bandwidth noise
        # (run a quick windowed CV, read ripple_ratio, lower bw_hz, re-run).
        elif any("current" in dp.variables for dp in result.data_points):
            nz = cv_noise(result, list(config.channels))
            summary["noise_ok"] = nz["noise_ok"]
            summary["noise"] = nz["per_channel"]
            summary["noise_note"] = nz["note"]
        return summary
