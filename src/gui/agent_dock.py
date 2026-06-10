"""Agent dock panel: chat UI, tool-call cards and analysis figures.

``AgentDockPanel`` is the QWidget the MainWindow places inside the
"Agent" QDockWidget.  It owns the :class:`~src.agent.agent_worker.
AgentWorker` lifecycle:

* The worker QObject is constructed in ``__init__`` (via the injectable
  ``worker_factory`` testability seam) so its signals can be connected
  immediately, but its THREAD is started lazily on the first Send --
  the app pays no agent-thread cost until the panel is actually used.
* :meth:`shutdown` (called from ``MainWindow.closeEvent``) requests a
  stop and waits for the thread to finish; it is idempotent.

Threading discipline (architecture.md): every slot in this module runs
on the GUI thread.  Worker signals are connected with the DEFAULT
connection type, which Qt resolves to a queued connection because the
emitting agent thread differs from this widget's thread.  Figures
rendered by the analysis tools cross the thread boundary through
:class:`FigureSink` -- the agent-side callable is just the sink's
``figure_ready.emit``, and the queued slot turns the PNG bytes into a
pixmap on the GUI thread.  The agent thread never touches a widget.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.agent.agent_worker import DEFAULT_MODEL, AgentWorker
from src.agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

__all__ = ["AgentDockPanel", "FigureSink", "ToolCallCard"]

#: Models offered in the picker (default first).
AGENT_MODELS = [
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]

#: Longest tool input/result preview shown on a card.
_CARD_PREVIEW_CHARS = 160

#: Displayed width of analysis figures (pixels; aspect preserved).
_FIGURE_WIDTH = 420

# Oldest figures are dropped beyond this count so a long lab session
# cannot grow the figure strip (and its pixmaps) without bound.
_MAX_FIGURES = 12


class FigureSink(QObject):
    """Thread-safe funnel for analysis figures into the GUI.

    The analysis tool handlers run on the agent thread and call the
    ``figure_sink`` callable with ``{"title", "tool", "png"}`` dicts.
    That callable is this object's ``figure_ready.emit`` -- emitting a
    Qt signal is thread-safe, and the panel's queued slot renders the
    pixmap on the GUI thread.
    """

    figure_ready = pyqtSignal(object)  # {"title": str, "tool": str, "png": bytes}


class ToolCallCard(QFrame):
    """Small visual card for one tool call: name, input, status, result."""

    _STATUS_STYLE = {
        "running": "color: #c8a000; font-weight: bold;",
        "done": "color: #2e8b57; font-weight: bold;",
        "error": "color: #c0392b; font-weight: bold;",
    }

    def __init__(
        self,
        call_id: str,
        name: str,
        input_preview: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Build the card in the 'running' state.

        Args:
            call_id: The tool_use block id (card lookup key).
            name: Tool name.
            input_preview: Compact one-line input rendering.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self.call_id = call_id
        self.tool_name = name
        self.status = "running"

        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        header = QHBoxLayout()
        name_label = QLabel(name)
        name_label.setStyleSheet("font-weight: bold;")
        self._status_label = QLabel("running")
        self._status_label.setStyleSheet(self._STATUS_STYLE["running"])
        header.addWidget(name_label)
        header.addStretch()
        header.addWidget(self._status_label)
        layout.addLayout(header)

        self._input_label = QLabel(input_preview)
        self._input_label.setWordWrap(True)
        self._input_label.setStyleSheet(
            "color: #909090; font-size: 11px;"
        )
        layout.addWidget(self._input_label)

        self._result_label = QLabel("")
        self._result_label.setWordWrap(True)
        self._result_label.setStyleSheet("font-size: 11px;")
        self._result_label.setVisible(False)
        layout.addWidget(self._result_label)

    def finish(self, status: str, result_preview: str) -> None:
        """Move the card to its terminal state.

        Args:
            status: ``"done"`` or ``"error"``.
            result_preview: Compact result rendering.
        """
        self.status = status
        self._status_label.setText(status)
        self._status_label.setStyleSheet(
            self._STATUS_STYLE.get(status, "")
        )
        if result_preview:
            self._result_label.setText(result_preview)
            self._result_label.setVisible(True)


def _preview(value: Any) -> str:
    """One-line, length-capped rendering for card labels."""
    text = str(value)
    text = " ".join(text.split())
    if len(text) > _CARD_PREVIEW_CHARS:
        text = text[: _CARD_PREVIEW_CHARS - 3] + "..."
    return text


class AgentDockPanel(QWidget):
    """Chat panel for the embedded Claude agent (dock content).

    Layout, top to bottom:

    * Header row: API-key field (password echo, prefilled from
      ``ANTHROPIC_API_KEY``) and model picker; changes are pushed to
      the worker via ``set_api_key`` / ``set_model``.
    * Splitter with the streaming transcript, the tool-call card list,
      and the figure strip.
    * Input row: message field + Send (Enter sends) + Stop.  Input is
      disabled while a turn is in flight.

    The panel owns the AgentWorker: it is constructed here (through
    ``worker_factory`` for testability) and its thread starts lazily on
    the first send.  Call :meth:`shutdown` before the application
    closes.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        worker_factory: Optional[
            Callable[[ToolRegistry, Optional[str], str], AgentWorker]
        ] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Build the panel and its (not yet started) worker.

        Args:
            registry: Tool registry handed to the worker.  Analysis
                tools may still be registered into it after
                construction (the worker reads ``tool_defs`` per
                request).
            worker_factory: Testability seam called as
                ``worker_factory(registry, api_key, model)`` and
                returning an :class:`AgentWorker`-compatible object.
                Default builds the real worker.
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._registry = registry
        self._shutdown_done = False
        self._stream_open = False  # an "Agent:" block is being streamed

        self._figures: list[dict[str, Any]] = []
        self._figure_labels: list[QLabel] = []
        self._figure_captions: list[QLabel] = []
        self._tool_cards: dict[str, ToolCallCard] = {}

        self._sink = FigureSink(self)
        self._sink.figure_ready.connect(self._on_figure_ready)

        self._build_ui()

        api_key = self._api_key_edit.text().strip() or None
        model = self._model_combo.currentText()
        if worker_factory is None:
            self._worker: AgentWorker = AgentWorker(
                registry, api_key=api_key, model=model
            )
        else:
            self._worker = worker_factory(registry, api_key, model)
        self._connect_worker(self._worker)

    # ---- UI construction ------------------------------------------------------

    def _build_ui(self) -> None:
        """Create all child widgets (GUI thread, construction time)."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header: API key + model picker.
        header = QHBoxLayout()
        header.addWidget(QLabel("API key:"))
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("ANTHROPIC_API_KEY")
        self._api_key_edit.setText(
            os.environ.get("ANTHROPIC_API_KEY", "")
        )
        self._api_key_edit.editingFinished.connect(
            self._on_api_key_edited
        )
        header.addWidget(self._api_key_edit, stretch=1)
        header.addWidget(QLabel("Model:"))
        self._model_combo = QComboBox()
        self._model_combo.addItems(AGENT_MODELS)
        self._model_combo.setCurrentText(DEFAULT_MODEL)
        self._model_combo.currentTextChanged.connect(
            self._on_model_changed
        )
        header.addWidget(self._model_combo)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Transcript (streaming).
        self._transcript = QPlainTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setPlaceholderText(
            "Ask the agent to run measurements or analyze data..."
        )
        splitter.addWidget(self._transcript)

        # Tool-call cards.
        cards_container = QWidget()
        self._cards_layout = QVBoxLayout(cards_container)
        self._cards_layout.setContentsMargins(2, 2, 2, 2)
        self._cards_layout.setSpacing(3)
        self._cards_layout.addStretch()
        cards_scroll = QScrollArea()
        cards_scroll.setWidgetResizable(True)
        cards_scroll.setWidget(cards_container)
        splitter.addWidget(cards_scroll)

        # Figures.
        figures_container = QWidget()
        self._figures_layout = QVBoxLayout(figures_container)
        self._figures_layout.setContentsMargins(2, 2, 2, 2)
        self._figures_layout.setSpacing(4)
        self._figures_layout.addStretch()
        self._figures_scroll = QScrollArea()
        self._figures_scroll.setWidgetResizable(True)
        self._figures_scroll.setWidget(figures_container)
        splitter.addWidget(self._figures_scroll)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        layout.addWidget(splitter, stretch=1)

        # Input row.
        input_row = QHBoxLayout()
        self._input_edit = QLineEdit()
        self._input_edit.setPlaceholderText("Message the agent...")
        self._input_edit.returnPressed.connect(self._on_send_clicked)
        input_row.addWidget(self._input_edit, stretch=1)
        self._send_button = QPushButton("Send")
        self._send_button.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self._send_button)
        self._stop_button = QPushButton("Stop")
        self._stop_button.setToolTip(
            "Stop the agent worker thread (per-turn interruption is "
            "not supported; this disables the agent until restart)."
        )
        self._stop_button.clicked.connect(self._on_stop_clicked)
        input_row.addWidget(self._stop_button)
        layout.addLayout(input_row)

    def _connect_worker(self, worker: AgentWorker) -> None:
        """Connect worker signals (default = queued across threads)."""
        worker.agent_text_delta.connect(self._on_text_delta)
        worker.tool_call_started.connect(self._on_tool_call_started)
        worker.tool_call_finished.connect(self._on_tool_call_finished)
        worker.tool_call_error.connect(self._on_tool_call_error)
        worker.agent_turn_started.connect(self._on_turn_started)
        worker.agent_turn_done.connect(self._on_turn_done)
        worker.agent_error.connect(self._on_agent_error)

    # ---- Public surface (MainWindow / analysis tools / tests) ------------------

    @property
    def worker(self) -> AgentWorker:
        """The owned AgentWorker (e.g. for app-close request_stop)."""
        return self._worker

    @property
    def figure_sink(self) -> Callable[[dict[str, Any]], None]:
        """Agent-thread-safe callable for ``build_analysis_tools``.

        The analysis handlers build the ``{"title", "tool", "png"}``
        dict themselves; the payload is shallow-copied at this boundary
        so no dict is ever shared by reference across threads.
        """
        emit = self._sink.figure_ready.emit

        def _sink_callable(payload: dict[str, Any]) -> None:
            emit(dict(payload))

        return _sink_callable

    def shutdown(self, wait_ms: int = 5000) -> None:
        """Stop the worker thread and wait for it (idempotent).

        Called from ``MainWindow.closeEvent`` before the window goes
        away so the agent thread never outlives the GUI objects its
        tools marshal onto.

        Args:
            wait_ms: Maximum milliseconds to wait for the thread.
        """
        if self._shutdown_done:
            return
        self._shutdown_done = True
        worker = self._worker
        try:
            worker.request_stop()
            if worker.isRunning() and not worker.wait(wait_ms):
                logger.warning(
                    "Agent worker did not stop within %d ms.", wait_ms
                )
        except Exception:  # noqa: BLE001 - shutdown must not raise
            logger.exception("Agent worker shutdown failed.")

    # Introspection helpers used by the smoke tests (GUI thread only).

    def transcript_text(self) -> str:
        """Full transcript text (for tests)."""
        return self._transcript.toPlainText()

    def tool_card_states(self) -> list[dict[str, str]]:
        """Tool cards as ``{"id", "name", "status"}`` dicts (for tests)."""
        return [
            {
                "id": card.call_id,
                "name": card.tool_name,
                "status": card.status,
            }
            for card in self._tool_cards.values()
        ]

    def figure_count(self) -> int:
        """Number of figures rendered into the figure strip."""
        return len(self._figure_labels)

    def figure_label(self, index: int) -> QLabel:
        """The QLabel holding figure *index* (for tests)."""
        return self._figure_labels[index]

    # ---- GUI-thread slots -------------------------------------------------------

    @pyqtSlot()
    def _on_send_clicked(self) -> None:
        """Echo the user message and submit it to the worker."""
        if self._shutdown_done:
            return
        text = self._input_edit.text().strip()
        if not text or not self._send_button.isEnabled():
            return
        # Lazy thread start: the agent thread spins up on first use.
        if not self._worker.isRunning():
            self._worker.start()
        self._close_stream()
        self._append_line(f"You: {text}")
        self._input_edit.clear()
        self._worker.submit_user_message(text)

    @pyqtSlot()
    def _on_stop_clicked(self) -> None:
        """Stop the worker thread entirely (app-close style stop)."""
        if self._shutdown_done:
            return
        self._close_stream()
        self._append_line("[Agent stopped by user]")
        self._set_in_flight(False)
        self._send_button.setEnabled(False)
        self._input_edit.setEnabled(False)
        self._stop_button.setEnabled(False)
        self.shutdown()

    @pyqtSlot()
    def _on_api_key_edited(self) -> None:
        """Push the edited API key to the worker (next turn)."""
        if self._shutdown_done:
            return
        self._worker.set_api_key(
            self._api_key_edit.text().strip() or None
        )

    @pyqtSlot(str)
    def _on_model_changed(self, model: str) -> None:
        """Push the selected model to the worker (next turn)."""
        if self._shutdown_done:
            return
        self._worker.set_model(model)

    @pyqtSlot(str)
    def _on_text_delta(self, text: str) -> None:
        """Append one streamed assistant chunk to the transcript."""
        if not self._stream_open:
            self._append_text("Agent: ")
            self._stream_open = True
        self._append_text(text)

    @pyqtSlot(object)
    def _on_tool_call_started(self, payload: object) -> None:
        """Add a 'running' card for the announced tool call."""
        data = dict(payload) if isinstance(payload, dict) else {}
        call_id = str(data.get("id", ""))
        card = ToolCallCard(
            call_id,
            str(data.get("name", "?")),
            _preview(data.get("input", {})),
        )
        self._tool_cards[call_id] = card
        # Insert above the trailing stretch item.
        self._cards_layout.insertWidget(
            self._cards_layout.count() - 1, card
        )

    @pyqtSlot(object)
    def _on_tool_call_finished(self, payload: object) -> None:
        """Mark the matching card done."""
        self._finish_card(payload, "done")

    @pyqtSlot(object)
    def _on_tool_call_error(self, payload: object) -> None:
        """Mark the matching card errored."""
        self._finish_card(payload, "error")

    @pyqtSlot()
    def _on_turn_started(self) -> None:
        """Disable input while the turn is in flight."""
        self._set_in_flight(True)

    @pyqtSlot()
    def _on_turn_done(self) -> None:
        """Re-enable input and close the streamed block."""
        self._close_stream()
        self._set_in_flight(False)

    @pyqtSlot(str)
    def _on_agent_error(self, message: str) -> None:
        """Show an agent/API error distinctly in the transcript."""
        self._close_stream()
        self._append_line(f"[Agent error] {message}")

    @pyqtSlot(object)
    def _on_figure_ready(self, payload: object) -> None:
        """Render a PNG figure payload into the figure strip."""
        data = dict(payload) if isinstance(payload, dict) else {}
        png = data.get("png")
        pixmap = QPixmap()
        if not isinstance(png, bytes) or not pixmap.loadFromData(png):
            logger.warning(
                "Dropping undecodable figure payload from tool %r.",
                data.get("tool"),
            )
            return
        if pixmap.width() > _FIGURE_WIDTH:
            pixmap = pixmap.scaledToWidth(
                _FIGURE_WIDTH,
                Qt.TransformationMode.SmoothTransformation,
            )
        caption = QLabel(
            f"{data.get('title', 'Figure')}  ({data.get('tool', '?')})"
        )
        caption.setStyleSheet("font-size: 11px; color: #909090;")
        label = QLabel()
        label.setPixmap(pixmap)
        insert_at = self._figures_layout.count() - 1
        self._figures_layout.insertWidget(insert_at, caption)
        self._figures_layout.insertWidget(insert_at + 1, label)
        self._figures.append(data)
        self._figure_labels.append(label)
        self._figure_captions.append(caption)
        # Bound the strip: drop the oldest figure pair beyond the cap.
        while len(self._figure_labels) > _MAX_FIGURES:
            old_caption = self._figure_captions.pop(0)
            old_label = self._figure_labels.pop(0)
            self._figures.pop(0)
            self._figures_layout.removeWidget(old_caption)
            self._figures_layout.removeWidget(old_label)
            old_caption.deleteLater()
            old_label.deleteLater()

    # ---- Internals -----------------------------------------------------------------

    def _finish_card(self, payload: object, status: str) -> None:
        """Resolve a card to done/error from a worker payload."""
        data = dict(payload) if isinstance(payload, dict) else {}
        call_id = str(data.get("id", ""))
        card = self._tool_cards.get(call_id)
        if card is None:
            logger.warning(
                "Tool result for unknown call id %r.", call_id
            )
            return
        card.finish(status, _preview(data.get("result", "")))

    def _set_in_flight(self, in_flight: bool) -> None:
        """Toggle the input row for turn-in-flight state."""
        if self._shutdown_done:
            return
        self._send_button.setEnabled(not in_flight)
        self._input_edit.setEnabled(not in_flight)

    def _append_text(self, text: str) -> None:
        """Append raw text at the end of the transcript (no newline)."""
        cursor = self._transcript.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        self._transcript.setTextCursor(cursor)
        self._transcript.ensureCursorVisible()

    def _append_line(self, line: str) -> None:
        """Append *line* on its own transcript line."""
        if self._transcript.toPlainText():
            self._append_text("\n")
        self._append_text(line + "\n")

    def _close_stream(self) -> None:
        """Terminate the current streamed 'Agent:' block, if any."""
        if self._stream_open:
            self._append_text("\n")
            self._stream_open = False
