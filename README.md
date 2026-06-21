# EmStat Pico MUX16 Controller

Python GUI application for operating the PalmSens EmStat Pico MUX16 potentiostat via MethodSCRIPT over serial, with live multi-channel plotting and data export.

## Quick Start (Fresh PC)

**Prerequisites:** Python 3.10+, VS Code (optional)

### 1. Create a virtual environment
```
python -m venv C:\Users\KarlJ\envs\cmu.49.013
```

### 2. Activate it
```
C:\Users\KarlJ\envs\cmu.49.013\Scripts\activate.bat
```
You should see `(cmu.49.013)` in your prompt.

### 3. Install dependencies
```
cd C:\Users\KarlJ\Documents\_all_work\CMU.49.013-EmStat-Pico-MUX16
pip install -r requirements.txt
```

### 4. Run the app
```
python -m src.gui.main_window
```

### VS Code Integration
`Ctrl+Shift+P` → **Python: Select Interpreter** → pick `cmu.49.013`. New terminals will auto-activate the environment.

## Architecture Overview

### Folder Structure
```
CMU.49.013-EmStat-Pico-MUX16/
├── src/
│   ├── comms/              # Serial + MethodSCRIPT protocol + MUX addressing
│   ├── techniques/         # MethodSCRIPT generation per technique
│   ├── data/               # Models, CSV/.pssession export, presets, sequences, app settings
│   ├── engine/             # Background measurement thread + sequence runner
│   ├── gui/                # PyQt6 main window, live plots, control panels, agent dock
│   ├── agent/              # Embedded Claude agent (Qt<->asyncio bridge, tools, worker)
│   ├── vendor/             # Read-only vendored CMU.49.011 analysis code
│   └── mcp_server/         # Headless MCP stdio server (same tool defs for Claude Code)
├── tests/                  # pytest suite mirroring src/ (202 tests, headless via QT_QPA_PLATFORM=offscreen)
├── docs/                   # Protocol references, enclosure design, lessons
└── claude_test_files/      # Agent validation / smoke scripts (NOT the project test suite)
```
Presets/sequences live OUTSIDE the repo under `~/.emstat_pico_mux16/` (`*.mux16` / `*.mux16seq`); run exports go to a user-configurable folder (default per-run timestamped dirs), agent run exports default to a gitignored `./agent_exports/`.

### Key Components
- `src/comms/serial_connection.py` - Serial I/O to EmStat Pico (230400 baud, XON/XOFF)
- `src/comms/protocol.py` - MethodSCRIPT data packet decoder (hex → float; current range parsed as metadata, never multiplied into Z)
- `src/comms/mux.py` - MUX16 GPIO channel addressing (WE + shared RE/CE, channels 1-16)
- `src/techniques/scripts.py` - MethodSCRIPT generator for all techniques (EIS/GEIS run mode 3 with the current range PINNED; `_format_si` emits integer mantissa only)
- `src/engine/measurement_engine.py` - QThread-based measurement orchestrator (per-read EIS timeout scales to lowest swept frequency)
- `src/engine/sequence_runner.py` - Chains presets back-to-back via the existing engine (CMU.17.034)
- `src/data/models.py` - Dataclasses for results/configs/channels incl. `electrode_config_mode` + `re_ce_channels`
- `src/data/exporters.py` + `pssession_exporter.py` - CSV + .pssession export (PSTrace method-string fidelity)
- `src/data/presets.py` + `sequence.py` + `app_settings.py` - Externalized presets/sequences + last-used path pointers
- `src/gui/main_window.py` - Application window: docked Settings/Log/Sequence tabs, tabbed live-plot center, right-side agent dock
- `src/gui/plot_widget.py` / `eis_plot_container.py` / `bode_widget.py` - pyqtgraph live plots (per-channel curves, Nyquist + Bode)
- `src/gui/controls.py` + `parameter_form.py` - Control panels + widget factory (technique-aware current-range ladder)
- `src/gui/agent_dock.py` - In-app Claude chat: streaming text, inline tool chips, in-chat figure attachments
- `src/agent/` - Embedded agent stack: `bridge.py` (Qt<->asyncio), `engine_adapter.py`, `tools.py`, `agent_worker.py`, `vendor_analysis.py`
- `src/mcp_server/stdio_server.py` - Headless MCP server exposing the same run/analyze tools for Claude Code (mock engine when no hardware)

### Use Cases
1. Run cyclic voltammetry across 16 electrode channels with live overlay plots
2. Perform EIS on selected channels and export Nyquist data for analysis in CMU.49.011
3. Multi-channel chronoamperometry for biosensor calibration with real-time current monitoring
4. Run NO sensing preset with auto-save for crash-safe in-vivo experiments (DARPA IV&V)
5. Chain heterogeneous presets (CV → EIS → CA, per-step electrode mode) back-to-back via the Sequencer tab (CMU.17.034)
6. Drive runs and analysis conversationally through the embedded Claude agent dock, or headlessly via the MCP server (CMU.17.042)

## Implementation Blueprint

### Phase 1: Communication Foundation

#### src/comms/
- **serial_connection.py** - Serial connection manager for EmStat Pico (Req 1)
  * Connects/disconnects at 230400 baud, 8N1 with XON/XOFF flow control via pyserial
  * Sends raw commands (`t`, `i`, `v`, `e`, `l`, `Z`, `h`, `H`) and reads responses, stripping device echo
  * Loads MethodSCRIPT by sending lines terminated with `\n`; empty line signals end-of-script
  * Queries firmware version (`t`) and serial number (`i`) automatically on connect
  * Thread-safe write access via internal lock for concurrent abort/halt/resume from GUI thread
  * Context manager support for safe connect/disconnect lifecycle

- **protocol.py** - MethodSCRIPT data packet parser (Req 2)
  * Decodes hex data packets in `P<var1>;<var2>;...\n` format into `ParsedPacket` objects
  * Converts 28-bit hex values with SI prefix to float: `(hex - 2^27) * 10^(SI_exponent)`
  * Maps 2-char variable type codes to names (da=set_potential, ba=current, ab=potential, cc=zreal, cd=zimag, etc.)
  * Tracks measurement loop markers (M, *, L, +) with stateful depth and channel index
  * Parses optional metadata fields (status bits, current range) after comma separators
  * Provides `SI_PREFIXES` and `VAR_TYPES` constant dictionaries for external use

- **mux.py** - MUX16 channel address calculation and GPIO control (Req 3)
  * WE-only addressing (RE/CE=0, common reference/counter electrode)
  * Address format: bits[9:8]=enable (inverted), bits[7:4]=RE/CE (always 0), bits[3:0]=WE
  * Generates `set_gpio_cfg 0x3FFi 1i` for configuring all pins as outputs
  * 100 ms settle time (`wait 100m`) after each channel switch
  * Compact `loop i <= e` pattern with `add_var i 0b01` for consecutive channels (constant script size)
  * Sequential fallback for non-consecutive channel selections
  * Hardware-validated on EmStat Pico MUX16 v2 (firmware v1.6)

### Phase 2: Measurement Core

#### src/techniques/
- **scripts.py** - MethodSCRIPT generator for all electrochemical techniques (Req 4)
  * Dual `set_pgstat_chan` preamble (chan 1 off, chan 0 active) + `set_autoranging` for current range
  * Hardware-verified techniques: CV, CA, CA MUX-alternating, EIS (single + multi-channel)
  * `pck_add` uses MethodSCRIPT variable names (`p`, `c`, `h`, `r`, `j`) not type codes
  * SI prefix formatting handles zero values (`0m`) for firmware compatibility
  * Multi-channel runs use compact `loop i <= e` via `MuxController.scan_channels_script_with_body()`
  * Safety postamble (`on_finished: cell_off`) on every generated script

#### src/data/
- **models.py** - Data models for measurements and configuration (Req 2, 4)
  * `TechniqueConfig` dataclass: technique name (auto-lowercased), parameter dict, and channel list
  * `DataPoint` dataclass: timestamp, channel, and variable dict mapping names to float values with `get()` accessor
  * `MeasurementResult`: ordered list of DataPoints with metadata (technique, start_time, device_info, params, channels) and `channel_data()` filtered-view method
  * `ChannelData`: per-channel subset with `values(name)` and `timestamps()` convenience extractors

#### src/engine/
- **measurement_engine.py** - Background measurement thread (Req 2, 3, 4)
  * QThread subclass that accepts a PicoConnection and TechniqueConfig, then runs the full measurement lifecycle in a background thread
  * Generates complete MethodSCRIPT (preamble + technique body + MUX channel loop + safety postamble) via `scripts.generate()` and sends it to the device
  * Reads streaming response lines in real time, parsing data packets via `PacketParser` into `DataPoint` objects with elapsed timestamps and channel assignment
  * Emits Qt signals for GUI integration: `data_point_ready(DataPoint)`, `measurement_started(str)`, `measurement_finished(MeasurementResult)`, `measurement_error(str)`, `channel_changed(int)`
  * Supports abort (`Z` command), halt (`h`), and resume (`H`) from the GUI thread via PicoConnection's thread-safe write methods
  * Buffers all decoded DataPoints into a `MeasurementResult` with device metadata, technique parameters, and channel list for post-run export
  * Handles serial disconnection and device error codes gracefully, emitting `measurement_error` signal with descriptive messages

### Phase 3: GUI Application

#### src/gui/
- **plot_widget.py** - Live pyqtgraph plot with per-channel curves (Req 5)
  * ``LivePlotWidget(pg.PlotWidget)`` subclass with technique-aware axis labels for all 18 supported techniques
  * 16-color palette (CHANNEL_COLORS) assigns visually distinct colors to CH1-CH16
  * ``add_point(channel, x, y)`` streams real-time data; ``on_data_point(DataPoint)`` slot connects directly to engine signals
  * Technique presets via ``set_technique()``: I vs E (CV/LSV/DPV/SWV/NPV/ACV/FCV/LSP/PAD), I vs t (CA/FCA), E vs t (CP/OCP), -Z'' vs Z' (EIS/GEIS)
  * Auto-range enabled by default; defers to manual zoom/pan when user interacts, re-enabled on measurement completion
  * ``clear_plot()`` removes all curves between measurements; ``reset()`` also restores default axis labels

- **controls.py** - GUI control panels for connection, technique, channels (Req 6)
  * ``ConnectionPanel(QGroupBox)``: COM port combo with auto-detect via ``serial.tools.list_ports``, connect/disconnect buttons, firmware version label, status indicator with color-coded states (connected/disconnected/error)
  * ``TechniquePanel(QGroupBox)``: technique dropdown populated from ``supported_techniques()``, dynamic parameter fields (QDoubleSpinBox for floats, QSpinBox for ints, QComboBox for current range) rebuilt per technique from ``technique_params()`` defaults
  * ``ChannelPanel(QGroupBox)``: 4x4 grid of 16 channel checkboxes (CH1 checked by default), Select All / Select None batch buttons, emits ``channels_changed(list)`` signal
  * ``MeasurementControlPanel(QGroupBox)``: Start (green), Stop/abort (red), Halt, Resume buttons with state-driven enable/disable logic (idle/running/halted/disabled modes)

- **main_window.py** - Main application window (Req 6)
  * ``MainWindow(QMainWindow)`` with dock-based layout: left dock (ConnectionPanel, TechniquePanel, ChannelPanel, MeasurementControlPanel), centre (a ``QTabWidget`` of live-plot tabs — one ``EISPlotContainer`` per run; a sequence accumulates one labelled tab per step (``1·CV``, ``2·EIS``, …) while a single run uses one replaced tab), bottom dock (QPlainTextEdit log console with attached logging handler)
  * Wires all Qt signals: engine ``data_point_ready`` is routed (``_route_data_point``) to the active plot tab, engine ``measurement_started`` creates/labels that tab for the technique actually starting (``_on_measurement_started``), engine ``measurement_finished``/``measurement_error``/``channel_changed`` to status bar and panel state updates, control panel signals to engine ``start_measurement``/``abort``/``halt``/``resume``
  * Menu bar with File (Export Results Ctrl+E, Quit Ctrl+Q), Device (Connect, Disconnect), Help (About) actions
  * Status bar with three permanent labels: connection state, measurement progress, and current MUX channel
  * On measurement complete: prompts user to export; creates timestamped subdirectory with per-channel CSV files (delegates to ``CSVExporter`` when available, falls back to basic CSV writer)
  * Application entry point via ``main()`` function and ``if __name__ == "__main__"`` block; configures root logger and launches QApplication

### Phase 4: Data Export

#### src/data/
- **exporters.py** - CSV and .pssession file export (Req 7)
  * `CSVExporter` writes one CSV per channel with technique-aware column ordering (voltammetry: potential/current, EIS: set_frequency/impedance/zreal/zimag/phase, amperometry: current/potential)
  * `PsSessionExporter` writes UTF-16 LE encoded JSON matching PalmSens .pssession format for CMU.49.011 compatibility
  * Metadata header block (``#``-prefixed) includes technique, parameters, timestamp, device serial, and firmware version
  * `make_export_dir()` helper creates timestamped output directories (``YYYYMMDD_HHMMSS_technique``)
  * `export()` alias on CSVExporter provides backward compatibility with GUI fallback path

### Phase 5: Operational Features

#### src/data/
- **incremental_writer.py** - Auto-save CSV writer for crash safety (Req 8)
  * `IncrementalCSVWriter` writes CSV data incrementally at each MUX loop boundary
  * Per-channel file handles opened on first data point, appended on each flush
  * Calls `f.flush()` + `os.fsync()` after each write for crash safety in in-vivo experiments
  * Thread-safe via `threading.Lock` (finish may be called from GUI thread during abort)
  * CSV format identical to `CSVExporter` output for downstream compatibility

- **presets.py** - Measurement preset management (Req 9)
  * `Preset` dataclass: name, technique, params, channels, auto_save, description
  * `PresetManager` loads/saves/manages presets from `presets/presets.json`
  * Ships with built-in `no_sensing` preset: CA at 0.85V, channels 1-8, auto-save enabled
  * Built-in presets cannot be deleted; user presets can be added and removed

### Phase 6: Completion Fixes

#### src/gui/
- **controls.py + main_window.py** - Save Preset dialog (Req 9)
  * `TechniquePanel` emits `save_preset_requested` signal via Save button
  * `MainWindow._on_save_preset()` prompts with QInputDialog, gathers current technique/params/channels/auto-save state, calls `PresetManager.add_preset()`, and refreshes the preset combo

- **main_window.py** - .pssession export in GUI export flow (Req 7)
  * `_do_export()` calls `PsSessionExporter.export_pssession()` alongside per-channel CSV export
  * Output directory contains both per-channel CSVs and a single `.pssession` file per run

### Phase 7: Mode-2 Bandwidth Sweep (CMU.17.022)

**Goal:** Make `bw_hz` user-controllable to characterize the unexplored mode-2 BW < 400 Hz region. PSTrace benchmark runs at ~5 Hz on the same MUX architecture (36–55 nA std dev on 4-ch); current code is hardcoded to 400 Hz (40–70 nA). Theory predicts 3–5× noise reduction; mode 2's well-damped loop should stay stable below 400 Hz unlike mode 3 (which oscillates at 10 Hz).

**Branch:** `feature/bw-sweep-mode2` (cut from main, independent of PR #4)

#### src/techniques/
- **scripts.py** - `bw_hz` added to mode-2 technique defaults; `_preamble()` parameterised (closes BW hardcoding)
  * `"bw_hz": 400` added to `_DEFAULTS` for ca, ca_alt_mux, cv, lsv, dpv, swv, npv, acv, fca, pad, lsp, fcv, cp, ocp (14 techniques)
  * `_preamble()` now emits `f"set_max_bandwidth {_format_si(params.get('bw_hz', 400))}"`; default 400 Hz preserves legacy behaviour when `bw_hz` is absent
  * `_preamble_eis()` and `_preamble_galvano()` remain hardcoded at 200 kHz (mode-3 control-loop stability lock); EIS/GEIS defaults intentionally have no `bw_hz` key
  * The `bw_hz` key on `cp` / `ocp` defaults is inert (their preambles are `_preamble_galvano` / `_preamble_ocp`) but kept for uniform preset surface

#### src/gui/
- **controls.py** - `bw_hz` exposed as GUI combobox in technique panel (per-run BW selection without code edit)
  * `"bw_hz": ("Max Bandwidth", "Hz")` added to `_PARAM_LABELS`
  * `_create_param_widget()` renders `bw_hz` as a `QComboBox` populated from `_BANDWIDTH_HZ = [0.4, 4, 40, 400, 4000, 40000, 200000]`; numeric Hz value stored via `setItemData()` so `get_params()` returns float/int (not the visible label string) for downstream `_format_si()` consumption
  * Default selection tracks the technique's `bw_hz` default (400 Hz for mode-2 techniques)

#### presets/
- **presets.json** - `bw_hz: 400` declared on built-in `no_sensing` preset (explicit declaration, backwards-compat)
  * `no_sensing.params` extended with `"bw_hz": 400`; matching change in `src/data/presets.py::_BUILTIN_PRESETS` keeps the in-memory built-in consistent with the JSON

#### tests/techniques/
- **test_scripts.py** - NEW. Regression guard for preamble bandwidth handling
  * Parametrised over `(0.4 → '400m', 4 → '4', 40 → '40', 400 → '400', 4000 → '4k', 40000 → '40k', 200000 → '200k')`; each `_preamble({'cr':'2u','bw_hz':bw})` is asserted to contain the expected `set_max_bandwidth` line
  * Default-path test: `_preamble({'cr':'2u'})` still emits `set_max_bandwidth 400`
  * Mode-3 lock tests: `_preamble_eis()` and `_preamble_galvano()` emit `set_max_bandwidth 200k` regardless of any `bw_hz` passed in
  * `tests/techniques/__init__.py` added (empty marker)

#### Milestone task
- [ ] **CMU.17.022** - Mode-2 BW sweep protocol + analysis (tracks lab execution and results)
  * TRR: 4-ch ferricyanide CA, e_dc=0.2 V, cr=2u, t_run=120 s, t_interval=0.5 s, settle=200 ms, t_eq=60 s, BW ∈ {400, 40, 4, 0.4} Hz on same electrode set
  * Abort criterion: current swing > 10× cr → control-loop instability → log, step BW up
  * Use `/task` skill to scaffold; increments `_tasks/registry.yaml` `next_ids.CMU` 22→23
  * Validate: `/task list` shows CMU.17.022 with status=Planned

#### Hardware execution (Karl, lab)
- [ ] **Run sweep** - Execute CMU.17.022 protocol on real EmStat Pico MUX16
  * 4 sequential runs at each BW value; rinse electrode between
  * Output: 4 timestamped export folders in `exports/` named `*_ca_alt_mux_bw{N}Hz_*`
  * Validate: `ls exports/*_bw*` shows ≥4 folders from sweep date

#### docs/
- [ ] **multiplexer_limitations_and_lessons.md** - Append sweep results to comparison table; update recommended settings
  * Add 4 rows to table (lines 99-113) — one per BW setting with std dev, resolution, notes
  * Update §5.3 recommended settings with optimal BW
  * Add TRA to `_tasks/CMU.17.022.md`: results, conclusions, gap-to-MUX8 reassessment
  * Action log signoff with Completed / Left off / Next
  * Validate: TRA section populated, doc table has 4 new rows, action_log entry exists

#### Combined-config follow-up (after PR #4 merges)
- [ ] **Rebased sweep** - One additional sweep run at optimal BW × PR #4 burst pacing (production-baseline number)
  * Rebase `feature/bw-sweep-mode2` onto post-merge main
  * Run 4-ch CA at optimal BW found in sweep; document combined effect
  * Validate: TRA addendum in CMU.17.022.md with combined-config std dev

---

## Embedded Claude Agent (feature/e-cheMCP) - Implementation Blueprint

An in-app Claude agent dock panel: the agent's tools drive the SAME live pyqtgraph plots the user
sees by building TechniqueConfigs and calling the app's existing MeasurementEngine instance, and call
vendored CMU.49.011 analysis code to import sessions and characterize electrode responses. Full clean
rewrite of the tool/agent layer; existing measurement/plot/serial code is reused as-is.

See architecture.md "Embedded Claude Agent - Threading/Async Bridge" for the single mandatory
concurrency model every implementation follows.

### Constraints (apply to EVERY task)
- No emojis anywhere (source, logs, prints, markdown) - Windows cp1252.
- Eager-import native deps (numpy/scipy, matplotlib Agg backend, pandas) at module top, before any
  asyncio loop - avoids the Windows DLL-load deadlock against asyncio threads.
- Vendored 49.011 code is READ-ONLY. Fixes go upstream in CMU.49.011 then re-copy; never edit
  src/vendor/ in place.
- Agent-created test/validation scripts live in claude_test_files/, never src/ or tests/.
- Thinking is ADAPTIVE only on Fable 5: thinking={"type":"adaptive"}. Do NOT send budget_tokens,
  temperature, top_p, top_k. Do NOT send thinking={"type":"disabled"} (omit to disable).
- Default agent model id: claude-fable-5 (configurable in panel; API key from panel or
  ANTHROPIC_API_KEY). The agent module MUST import with no API key set.
- New deps: anthropic, mcp[cli]. Add pandas, scipy, matplotlib for vendored analysis.

### Validation Gate (EVERY batch - must pass with NO hardware and NO API key)
A headless smoke script under claude_test_files/ that imports the tool layer, builds the tool
registry, constructs the mock engine + mock connection, runs one mock CV through the engine adapter
to completion, and exits 0. The agent loop module must import without an API key.

### Batch 1 - src/agent/ foundation
- [x] **src/agent/bridge.py** - Qt<->asyncio marshaling primitives
  * run_on_gui(callable) queued onto a GUI-thread QObject; await-able from the asyncio loop
  * await_signal(...) -> concurrent.futures.Future resolved by one-shot GUI-thread slots, consumed
    via asyncio.wrap_future. No Qt widget imports; pure QtCore. Eager imports at module top.
  * Validate: `python -c "from src.agent.bridge import run_on_gui, await_signal"`
- [x] **src/agent/mock_engine.py** - MockMeasurementEngine + MockConnection (no-hardware path)
  * Same signals (data_point_ready, measurement_finished, measurement_error, channel_changed) and
    start_measurement(conn, cfg)/isRunning()/result surface as the real engine; emits synthetic
    DataPoints then measurement_finished via QTimer; MockConnection.is_connected=True
  * Validate: `python -c "from src.agent.mock_engine import MockMeasurementEngine, MockConnection; MockMeasurementEngine()"`
- [x] **src/agent/engine_adapter.py** - EngineAdapter: config builders + await-completion bridge + device tools
  * Builds TechniqueConfig for cv/ca/cp/eis/geis from src.techniques.scripts defaults, merging tool args
  * async run_cv/run_ca/run_eis/run_cp: marshal start_measurement onto GUI thread, await finish/error
    future, return compact summary; reject when isRunning(). Device tools: list ports, connect/
    disconnect/status; mock-aware. Engine + connection injected so the mock substitutes cleanly.
  * Validate: `python claude_test_files/smoke_engine_adapter.py` (mock CV to completion, exit 0)

### Batch 2 - tool surface + agent runtime
- [x] **src/agent/tools.py** - Anthropic tool definitions + dispatch over the EngineAdapter
  * JSON-schema tool defs (run_cv/run_ca/run_eis/run_cp, list_ports/connect/disconnect/device_status)
  * build_registry(adapter) -> name->async-handler map + tool spec list; analysis tools stubbed here,
    filled in Batch 3. Importable with no API key; no network at import.
  * Validate: `python claude_test_files/smoke_tools.py` (build registry over mock adapter, run one mock CV via dispatch, exit 0)
- [x] **src/agent/agent_worker.py** - AgentWorker(QThread) async streaming agentic loop
  * run() creates its own asyncio loop; AsyncAnthropic streaming manual tool-use loop (stream text
    deltas, execute tool handlers, feed tool_result back, loop to end_turn). Model configurable
    (default claude-fable-5); thinking={"type":"adaptive"}; client constructed lazily at start.
  * Emits agent_text_delta/tool_call_started/tool_call_finished/tool_call_error/agent_turn_done via a
    QObject bridge; importable with no API key.
  * Validate: `python -c "import src.agent.agent_worker"` AND `python claude_test_files/smoke_agent.py`

### Batch 3 - dock UI, analysis tools, vendoring, wiring
- [x] **src/gui/agent_dock.py** - AgentDockPanel chat UI + tool cards + figures
  * Chat transcript with streaming text; input box + send; API-key field + model picker; live tool-call
    cards (running/done/error); results/figure area (render matplotlib Agg figures). Slots update
    widgets on the GUI thread only; subscribes to AgentWorker signals.
  * Validate: `python claude_test_files/smoke_agent_dock.py` (offscreen QApplication, instantiate panel, feed a fake tool-card + text delta, exit 0)
- [x] **src/agent/vendor_analysis.py** - analysis tools over vendored 49.011
  * load_session, analyze_cv, analyze_ecsa, analyze_ca, analyze_eis, analyze_cic, analyze_cp calling
    src.vendor.electrochem_analysis.*; return summaries + matplotlib Agg figures handed to the panel;
    registered into tools.py. Eager-import numpy/scipy/matplotlib(Agg)/pandas at module top.
  * Validate: `python claude_test_files/smoke_analysis_tools.py` (analyze a bundled sample session headlessly, exit 0)
- [x] **src/vendor/electrochem_analysis/** - copy 49.011 src/analysis + src/dataloaders + src/utils, READ-ONLY, import-rewritten
  * Copy all modules; rewrite `from src.` / `import src.` -> `src.vendor.electrochem_analysis.`; add
    package __init__.py with a PROVENANCE note (source repo + commit) in its docstring. No behavioral edits.
  * Validate: `python -c "from src.vendor.electrochem_analysis.analysis import cv, ca, cp, eis, ecsa, cic"`
- [x] **src/gui/main_window.py** - EDIT: add right dock, wire AgentWorker + EngineAdapter to the EXISTING engine
  * addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, agent_dock); construct
    EngineAdapter(self._engine, self._connection) and AgentWorker; mock toggle when no hardware. No
    change to existing measurement/plot/serial code paths.
  * Validate: `python claude_test_files/smoke_main_window.py` (offscreen, construct MainWindow with mock, assert right dock present, exit 0)

### Batch 4 - headless MCP server (LOWEST priority)
- [x] **src/mcp_server/stdio_server.py** - thin MCP stdio server exposing the same tool defs for Claude Code
  * Reuses tools.py defs; headless (mock engine when no GUI); eager native imports; no emojis.
  * Validate: `python claude_test_files/smoke_mcp_server.py` (start server in-proc, list tools, call one mock-CV tool, exit 0)

### Follow-up - run -> characterize chain
- [x] **export_session tool** - saves the last finished run via the existing PsSessionExporter and
  returns the absolute .pssession path, closing the gap between run_* (live summaries) and
  analyze_* (which read .pssession files). Optional `path` arg (file or directory); defaults to
  ./agent_exports/ (gitignored). Registered in tools.py, so the dock agent and the MCP server both
  expose it (chain: run_cv -> export_session -> analyze_cv).
  * Validate: `python claude_test_files/smoke_export_session.py` (mock CV -> export -> vendored
    loader + CVAnalyzer round-trip, exit 0)

---

## CMU.17.034 — Preset Sequencer (IMPLEMENTED 2026-06-09)

A PSTrace-"Scripts"-equivalent for the MUX-16: a new sidebar tab where saved
presets are stacked as draggable blocks and run back-to-back. Two coupled
pieces — (1) externalize the preset store so preset files live outside the
repo and are imported via the preset dropdown, and (2) a sequence tab + runner.

**Design rationale and decisions:** see `architecture.md` → "Preset Sequencer
(CMU.17.034)". Key already-true fact: the `Preset` dataclass already carries
`channels`, `electrode_config_mode` (external=A / on_board=B / manual=C), and
`re_ce_channels` (the Mode-C pairing), so no schema redesign is needed — only
externalization of the file and a verification that the preset *save* path
captures the live electrode mode.

### Phase 1: Externalize the preset store

#### src/data/presets.py
- **presets.py** - Load/save-to-arbitrary-path + versioned file wrapper
  * On-disk wrapper schema: `{"format": "mux16-presets", "version": 1, "presets": {<name>: <Preset asdict>}}`; the loader detects the legacy bare `{name: preset}` map by the absence of a `"format"` key and reads it via a back-compat branch
  * `PresetManager.load_from_path(path)` / `save_to_path(path)` added; `load_from_path` re-seeds built-ins, merges the file on top, and switches the active path so later add/delete persist there. `_BUILTIN_PRESETS` stays in code as seed-only (always present in memory)
  * Default store moved OUT of the repo to a per-user data path (`~/.emstat_pico_mux16/presets.mux16`), not `presets/presets.json`
  * One-time migration: a default-store manager with no external file imports the legacy in-repo `presets/presets.json` once, then materializes the external file
  * Verified: `python -c "from src.data.presets import PresetManager; m=PresetManager(); m.save_to_path('x.mux16'); n=PresetManager(); n.load_from_path('x.mux16'); print(sorted(n.list_presets()))"` round-trips

- **app settings persistence** (`src/data/app_settings.py`) - Remembers the last-used preset file path
  * A tiny JSON file in the user-data dir (`~/.emstat_pico_mux16/app_settings.json`) holds `last_preset_file`; chosen over `QSettings` so `get_last_preset_file` / `set_last_preset_file` import and round-trip with no running `QApplication`. The settings path is overridable (`path=`) so tests never touch the real store
  * `MainWindow._load_last_preset_file` auto-loads the remembered file on startup when it still exists; a stale/missing pointer is ignored silently
  * Verified by `tests/data/test_app_settings.py`: set-then-get round-trip plus auto-load recovering presets from a saved file under a temp settings path

#### src/gui/controls.py (TechniquePanel preset dropdown)
- **controls.py** - "Import preset file..." is the last entry of the preset combobox
  * `TechniquePanel` carries the entry as a sentinel-keyed (`_IMPORT_PRESET_SENTINEL`) item appended by `refresh_presets`. Selecting it opens a `QFileDialog` (filter `MUX16 presets (*.mux16)`), loads the file via the injected `PresetManager.load_from_path`, repopulates the dropdown, persists the path via `set_last_preset_file`, and emits `presets_imported`. A cancelled dialog (or a missing manager / unreadable file) restores the prior selection and changes nothing
  * The manager is injected by `MainWindow._load_presets_into_ui` via `set_preset_manager` (with an optional settings-path override so tests never touch the real per-user store). This is the ONLY way a new file location is chosen — no hardcoded Drive/local default
  * Verified by `tests/gui/test_preset_import.py`: stubs `QFileDialog.getOpenFileName` to a temp `.mux16`, asserts the dropdown repopulates AND `last_preset_file` updates; a cancel case leaves state untouched

- **Preset SAVE captures electrode mode** - `MainWindow._on_save_preset` persists the live wiring policy
  * Reads `electrode_config_mode` from `ElectrodeConfigPanel`; in manual mode the WE + per-WE `re_ce_channels` pairing both come from `ManualChannelPanel.selected_pairs()`, while external/on_board take WE from the channel grid and leave `re_ce_channels` empty (re-defaulted at run time by `TechniqueConfig.__post_init__`). `_on_preset_selected` restores the mode + routes channels to the matching panel on load
  * Verified by `tests/gui/test_preset_save_mode.py`: a Mode-C config with explicit `re_ce_channels` survives a save/reload round-trip (`electrode_config_mode == "manual"`, pairing intact)

#### repo hygiene
- **.gitignore + untracked presets.json** - Presets are user data, not code
  * `.gitignore` now ignores the user-data preset/sequence globs (`*.mux16`, `*.mux16seq`) and `/presets/presets.json`; the legacy in-repo store was untracked via `git rm --cached presets/presets.json` (kept on disk as the one-time migration source). Built-in seeds remain in code
  * Verified: `presets/presets.json` no longer tracked yet still on disk; the default-store manager migrates the 12 legacy presets on first run, and a path-pointed manager with no external file loads cleanly (built-in seed set, currently empty by design)

### Phase 2: Sequence model + persistence

#### src/data/sequence.py (NEW)
- **sequence.py** - `SequenceStep` + `Sequence` dataclasses and a SEPARATE sequence file
  * `SequenceStep{preset_name: str, repeat: int = 1, delay_s: float = 0.0, channels_override: list[int] | None, mode_override: str | None}`
  * `Sequence{name: str, steps: list[SequenceStep]}` with `to_dict` / `from_dict` / `save_to_path` / `load_from_path`; on-disk wrapper `{"format": "mux16-sequence", "version": 1, ...}` in a sibling `*.mux16seq` file (separate from presets, per decision 2026-06-09)
  * `build_config(step, preset) -> TechniqueConfig` resolves a step against its preset (applying channel/mode overrides) and lets `TechniqueConfig.__post_init__` validate (Mode-C bounds etc.). When a channels override changes the count, the preset's explicit `re_ce_channels` is dropped so external/on_board steps repopulate from the mode default and a manual step with no usable pairing raises — the intended safety behaviour
  * Verified by `tests/data/test_sequence.py`: 3-step round-trip equality (incl. overrides) and `build_config` raising on a Mode-C step with empty `re_ce_channels`

### Phase 3: Sequencer tab (GUI)

#### src/gui/sequence_panel.py (NEW)
- **sequence_panel.py** - `SequencePanel(QWidget)` with reorderable blocks
  * `QListWidget` in `InternalMove` drag-drop mode; each row stores its `SequenceStep` as item data so the visual order IS the sequence order (read back via `build_sequence`). Each row shows preset name + repeat/delay suffix; the selected row's repeat + delay are edited via spin boxes that write back onto the step
  * Add-step (a `QInputDialog` picker over the injected `PresetManager.list_presets()`), remove-step, Run / Stop buttons, and a "Step i of N" progress label. Save/Load sequence (`*.mux16seq`) via `QFileDialog`
  * Owns no engine/connection: the `PresetManager`, engine, and a connection-provider callable are injected by `MainWindow`. Run builds a `SequenceRunner.from_sequence` against the current `Sequence` and starts it; the runner's `sequence_progress`/`sequence_finished`/`sequence_error` drive the progress label + Run/Stop enabled-state. Stop clears the runner and aborts the in-flight engine step. `sequence_started`/`sequence_stopped` signals bridge into the main window's export-suppression state
  * Verified by `tests/gui/test_sequence_panel.py` (offscreen): 3 blocks add in order, a model-level `takeItem`+`insertItem` reorder changes `Sequence.steps` order, and a save-to-`*.mux16seq`/reload round-trips equal (with an independent ground-truth check on the persisted file)

#### src/gui/main_window.py
- **main_window.py** - Register the new dock tab
  * `_build_sequence_dock()` mirrors `_build_log_dock`: a movable/floatable `QDockWidget` titled "Sequence" hosting a `SequencePanel`. `tabifyDockWidget(self._control_dock, self._sequence_dock)` tabs it beside Settings/Log; the panel is injected with the shared `PresetManager`, engine, and a `_sequence_connection` accessor (returns the single `PicoConnection` when connected, else `None`)
  * Verified by `tests/gui/test_sequence_dock.py` (offscreen): a dock titled "Sequence" is present after `MainWindow()` construction

### Phase 4: Sequence runner

#### src/engine/sequence_runner.py (NEW)
- **sequence_runner.py** - `SequenceRunner(QObject)` drives steps via the existing engine
  * Holds a resolved queue of `_QueueEntry(config, delay_s)`; `start()` launches step 0 via `engine.start_measurement`. `from_sequence(engine, connection, sequence, preset_manager)` resolves each step with `build_config` (validated eagerly so a Mode-C step with no usable pairing raises before step 0) and expands `SequenceStep.repeat` into that many entries, applying `delay_s` only on the final repeat
  * On `measurement_finished` → emits `sequence_progress`, then schedules the next entry after `delay_s` via `QTimer.singleShot`; `_advance` re-checks `engine.isRunning()` (re-arming a short timer rather than launching) so the single-run guard is never violated. On `measurement_error` → clears the running flag, halts the queue, re-emits `sequence_error`
  * `sequence_mode` property lets the main window suppress the interactive export prompt. `from_sequence(..., base_export_dir=..., auto_save_all=...)` controls per-step auto-save: auto-saving entries (all of them when `auto_save_all` — the GUI toggle — else just the provenance-forced EIS/GEIS steps) each get an `AutoSaveConfig(exact_dir=True)` pointing at their own `<base>/<stamp>_sequence/stepNN_<technique>/` dir — unique per queue entry including repeats, so two same-second runs of one technique can never collide — matching the end-of-run save prompt's layout exactly (shared `make_sequence_dir`/`sequence_step_dirname` helpers in `exporters.py`). No base dir → steps don't auto-save at all
  * Signals: `sequence_progress(int, int)`, `sequence_finished()`, `sequence_error(str)`
  * Verified by `tests/engine/test_sequence_runner.py` with a mock engine (QObject exposing the two lifecycle signals + a `start_measurement` recorder + `isRunning()`): step N+1 starts only after step N's finished signal, the queue completes to `sequence_finished` with monotonic progress, an error halts the queue + emits `sequence_error`, and `repeat` expands into extra runs

#### src/gui/main_window.py (wiring)
- **main_window.py** - Suppress export prompt in sequence mode; disable single-run controls while a sequence runs
  * A `_sequence_active` flag is set on `sequence_started` (which also disables the measurement panel) and cleared on `sequence_stopped` (which restores Start when a device is still connected). In `_on_measurement_finished`, sequence mode returns early BEFORE the `set_idle()` / export-prompt branch — so each step's completion never re-enables Start and never raises the interactive `_prompt_export` / auto-save modal (the `SequenceRunner` drives the next step and per-step auto-save handles persistence)
  * Verified by `tests/gui/test_sequence_dock.py` (offscreen): a 2-step mock sequence raises NO `QMessageBox` (every entry point monkeypatched to fail the test) and keeps Start disabled mid-sequence, re-enabling it after `sequence_finished`

### Phase 5: Tests + docs
- **tests/** - `test_sequence.py`, `test_sequence_runner.py`, `test_sequence_panel.py` + `test_sequence_dock.py` (headless), preset round-trip + import-dialog tests
  * `test_sequence_runner.py` also covers the carry-forwards: `start()` refuses when the engine is already running (emits `sequence_error`), and a late `measurement_finished` arriving after `stop()` adds no phantom step (no extra launch / progress / finish)
  * Full suite green: `QT_QPA_PLATFORM=offscreen pytest -q` -> 100 passed
- **README transform** - Checkboxes converted to past-tense docs; decisions recorded in `architecture.md`

#### Build-time resolutions (closed)
- File extensions shipped as `.mux16` (presets) / `.mux16seq` (sequences)
- Per-step channel/mode overrides are persisted in the `SequenceStep` model but the v1 panel UI edits only preset + repeat + delay; overrides round-trip through save/load and `build_config` honours them
- The remembered `last_preset_file` pointer lives in `~/.emstat_pico_mux16/app_settings.json`; the import dialog is the only way to point at a preset file (no hardcoded location)
