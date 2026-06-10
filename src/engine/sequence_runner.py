"""Sequential preset runner for the EmStat Pico MUX16 (CMU.17.034).

Drives a queue of resolved :class:`~src.data.models.TechniqueConfig`
steps back-to-back through the existing :class:`MeasurementEngine`, a
PSTrace-"Scripts" equivalent.  The runner never opens a second execution
path: it chains ``engine.start_measurement`` calls, advancing on the
engine's ``measurement_finished`` signal and gating on ``isRunning()`` so
the engine's single-run guard is honoured.

Design (see ``architecture.md`` -> "Preset Sequencer (CMU.17.034)"):

* The whole queue is resolved (and therefore validated by
  ``TechniqueConfig.__post_init__``) up front in :meth:`from_sequence`, so
  a Mode-C step with no usable RE/CE pairing fails before step 0 launches
  rather than mid-run.
* A step's ``repeat`` is expanded into that many queue entries; the
  step's ``delay_s`` is applied only AFTER the last repeat, inserted via
  ``QTimer.singleShot`` so the GUI event loop keeps spinning.
* ``sequence_mode`` is exposed so the main window can suppress the
  interactive export prompt. When a ``base_export_dir`` is supplied (only
  when the user has enabled auto-save), every step auto-saves into one
  shared ``<base>/<stamp>_sequence/`` parent; the engine's incremental
  writer adds a ``<ts>_<technique>_autosave/`` leaf per step, so the run
  looks like a normal export folder whose subfolders are each an ordinary
  per-step export. Without a base dir the sequence runs without writing.

Signals:
    sequence_progress(int, int): Emitted as ``(completed, total)`` each
        time a step finishes (1-indexed completed count).
    sequence_finished(): Emitted once when every step has completed.
    sequence_error(str): Emitted when a step errors; the queue halts.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from src.data.models import AutoSaveConfig, TechniqueConfig
from src.data.presets import PresetManager
from src.data.sequence import Sequence, build_config

logger = logging.getLogger(__name__)

# Re-poll cadence (ms) while waiting for the engine thread to settle
# between steps, plus a bounded number of re-polls before giving up.  At
# 20 ms a 200-poll cap gives ~4 s of grace for the engine's
# ``measurement_finished`` signal to be followed by ``isRunning()`` going
# False; past that the engine is wedged, so the sequence errors out rather
# than re-arming the timer forever.
_ADVANCE_REPOLL_MS = 20
_ADVANCE_MAX_REPOLLS = 200


@dataclass
class _QueueEntry:
    """One resolved step in the run queue.

    Attributes:
        config: The validated technique configuration to run.
        delay_s: Idle delay inserted AFTER this entry before the next
            one starts.  Non-zero only on the final repeat of a step.
    """

    config: TechniqueConfig
    delay_s: float = 0.0


class SequenceRunner(QObject):
    """Run a queue of technique configs back-to-back via the engine.

    The runner is a thin orchestrator: it owns no serial I/O and reuses
    the engine for every step.  It connects to the engine's
    ``measurement_finished`` / ``measurement_error`` signals and advances
    the queue from there, never starting a new step while the engine is
    still running.

    Args:
        engine: The shared measurement engine.  Must expose
            ``start_measurement(connection, config)``,
            ``measurement_finished``, ``measurement_error``, and
            ``isRunning()``.
        connection: The connected ``PicoConnection`` passed through to
            each ``start_measurement`` call.  May be ``None`` for the
            mock-engine unit tests, which ignore it.
        queue: Resolved queue entries to run in order.
        parent: Optional Qt parent.
    """

    sequence_progress = pyqtSignal(int, int)  # (completed, total)
    sequence_finished = pyqtSignal()
    sequence_error = pyqtSignal(str)

    def __init__(
        self,
        engine: object,
        connection: object,
        queue: list[_QueueEntry],
        base_export_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._engine = engine
        self._connection = connection
        self._queue: list[_QueueEntry] = list(queue)
        self._index: int = 0
        self._running: bool = False
        # Re-poll counter for the inter-step settle wait; reset before
        # each advance and capped so a wedged engine surfaces an error.
        self._advance_repolls: int = 0
        # Timestamped parent dir for per-step auto-save; fixed once at
        # construction so every step in the run shares one folder.
        self._run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # When an export base dir is supplied, every step auto-saves into
        # one shared <base>/<stamp>_sequence/ parent so a sequence run
        # looks like a normal export with per-step subfolders. The engine's
        # incremental writer adds the <ts>_<technique>_autosave leaf, so
        # each step subfolder is identical to a standalone auto-save export.
        self._sequence_dir: Optional[str] = None
        if base_export_dir:
            self._attach_step_auto_save(base_export_dir)

        # Chain on the engine's lifecycle signals.  Done once here so the
        # connections survive across all steps.
        self._engine.measurement_finished.connect(self._on_step_finished)
        self._engine.measurement_error.connect(self._on_step_error)

    # -- construction helpers ---------------------------------------------

    @classmethod
    def from_sequence(
        cls,
        engine: object,
        connection: object,
        sequence: Sequence,
        preset_manager: PresetManager,
        base_export_dir: Optional[str] = None,
        parent: Optional[QObject] = None,
    ) -> "SequenceRunner":
        """Build a runner by resolving a sequence against a preset store.

        Each step is resolved with :func:`build_config` (which validates
        via ``TechniqueConfig.__post_init__``) and expanded by its
        ``repeat`` count.  The step's ``delay_s`` lands only on the last
        repeat.  Resolution happens eagerly so an invalid step raises
        here, before any measurement starts.

        Args:
            engine: Shared measurement engine.
            connection: Connected ``PicoConnection`` (or ``None``).
            sequence: The sequence of preset steps to run.
            preset_manager: Store the step preset names resolve against.
            parent: Optional Qt parent.

        Returns:
            A ready-to-:meth:`start` ``SequenceRunner``.

        Raises:
            KeyError: If a step references an unknown preset name.
            ValueError: If a resolved step fails ``TechniqueConfig``
                validation (e.g. Mode-C with no RE/CE pairing).
        """
        queue: list[_QueueEntry] = []
        for step in sequence.steps:
            # Embedded steps carry their own config; only legacy
            # reference steps still need the preset store.
            preset = None
            if not step.is_embedded:
                preset = preset_manager.get_preset(step.preset_name)
                if preset is None:
                    raise KeyError(
                        f"Sequence step references unknown preset: "
                        f"{step.preset_name!r}"
                    )
            config = build_config(step, preset)
            repeat = max(1, int(step.repeat))
            for rep in range(repeat):
                # Delay only after the final repeat of the step.
                delay = step.delay_s if rep == repeat - 1 else 0.0
                queue.append(_QueueEntry(config=config, delay_s=delay))
        return cls(
            engine,
            connection,
            queue,
            base_export_dir=base_export_dir,
            parent=parent,
        )

    # -- public API --------------------------------------------------------

    @property
    def sequence_mode(self) -> bool:
        """Whether a sequence is currently driving the engine.

        The main window reads this to suppress the interactive export
        prompt and auto-save each step instead.
        """
        return self._running

    @property
    def total_steps(self) -> int:
        """Total number of queued step runs (repeats expanded)."""
        return len(self._queue)

    @property
    def sequence_dir(self) -> Optional[str]:
        """Shared parent dir all steps auto-save into (None if disabled)."""
        return self._sequence_dir

    def _attach_step_auto_save(self, base_export_dir: str) -> None:
        """Enable per-step auto-save into one shared sequence folder.

        Every step's config is pointed at ``<base>/<stamp>_sequence/``.
        The engine's incremental writer then creates a
        ``<ts>_<technique>_autosave/`` leaf per step, so the run lands as
        a normal-looking export folder whose subfolders are each an
        ordinary per-step export::

            <base>/<stamp>_sequence/
                <ts>_<technique>_autosave/   <- step 1
                <ts>_<technique>_autosave/   <- step 2

        Args:
            base_export_dir: Root export directory the user configured.
        """
        self._sequence_dir = os.path.join(
            base_export_dir, f"{self._run_stamp}_sequence"
        )
        for entry in self._queue:
            entry.config.auto_save = AutoSaveConfig(
                enabled=True, output_dir=self._sequence_dir
            )

    def start(self) -> None:
        """Launch the first step.

        Does nothing for an empty queue beyond emitting
        ``sequence_finished``.  Refuses to start while the engine is
        already running so the single-run guard is never violated.
        """
        if self._running:
            logger.warning(
                "SequenceRunner.start() while already running."
            )
            return
        if not self._queue:
            logger.info("SequenceRunner started with an empty queue.")
            self.sequence_finished.emit()
            return
        if self._engine.isRunning():
            self.sequence_error.emit(
                "Engine is busy; cannot start a sequence."
            )
            return
        self._running = True
        self._index = 0
        self._launch_current()

    def stop(self) -> None:
        """Halt the sequence after the current step.

        Clears the running flag so a finished/queued step does not start
        the next one.  An in-flight engine run is left to the caller to
        abort (the runner owns no serial I/O).
        """
        self._running = False

    # -- internal ----------------------------------------------------------

    def _launch_current(self) -> None:
        """Start the engine on the current queue entry."""
        entry = self._queue[self._index]
        try:
            self._engine.start_measurement(
                self._connection, entry.config
            )
        except RuntimeError as exc:
            # Engine reported busy despite our guard -- surface it and
            # halt rather than silently dropping the step.
            self._running = False
            self.sequence_error.emit(str(exc))
            return
        logger.info(
            "Sequence step %d/%d started (%s).",
            self._index + 1,
            len(self._queue),
            entry.config.technique,
        )

    @pyqtSlot(object)
    def _on_step_finished(self, _result: object) -> None:
        """Advance the queue when the engine finishes a step.

        Ignored when no sequence is active so a stray standalone-run
        completion never drives the queue.
        """
        if not self._running:
            return
        completed = self._index + 1
        self.sequence_progress.emit(completed, len(self._queue))

        if completed >= len(self._queue):
            self._running = False
            logger.info("Sequence complete: %d steps.", completed)
            self.sequence_finished.emit()
            return

        delay_s = self._queue[self._index].delay_s
        self._index += 1
        delay_ms = max(0, int(delay_s * 1000))
        # Defer the next step so the engine thread fully finishes (the
        # finished signal can fire while isRunning() is briefly still
        # True) and the inter-step delay is honoured.
        self._advance_repolls = 0
        QTimer.singleShot(delay_ms, self._advance)

    def _advance(self) -> None:
        """Launch the next step once the engine has settled.

        While the engine is still winding down its previous run, this
        re-arms a short timer instead of launching (never violating the
        single-run guard).  The re-poll count is bounded: a wedged engine
        that never goes idle halts the queue with ``sequence_error``
        rather than re-polling forever.
        """
        if not self._running:
            return
        if self._engine.isRunning():
            self._advance_repolls += 1
            if self._advance_repolls > _ADVANCE_MAX_REPOLLS:
                # Engine never went idle within the grace window -- treat
                # it as wedged and surface an error instead of spinning.
                self._running = False
                msg = (
                    "Engine did not become idle between steps "
                    f"(step {self._index + 1}/{len(self._queue)}); "
                    "sequence aborted."
                )
                logger.error(msg)
                self.sequence_error.emit(msg)
                return
            # Engine not yet idle -- re-check shortly without launching.
            QTimer.singleShot(_ADVANCE_REPOLL_MS, self._advance)
            return
        self._launch_current()

    @pyqtSlot(str)
    def _on_step_error(self, message: str) -> None:
        """Halt the queue and re-emit when a step errors."""
        if not self._running:
            return
        self._running = False
        logger.error(
            "Sequence halted at step %d/%d: %s",
            self._index + 1,
            len(self._queue),
            message,
        )
        self.sequence_error.emit(message)
