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
│   ├── data/               # Data models + CSV/.pssession export
│   ├── engine/             # Background measurement thread
│   └── gui/                # PyQt6 main window, live plot, control panels
├── presets/                # Measurement preset configurations (JSON)
├── tests/                  # Test suite
├── exports/                # Measurement output files
└── docs/                   # Protocol references
```

### Key Components
- `src/comms/serial_connection.py` - Serial I/O to EmStat Pico (230400 baud, XON/XOFF)
- `src/comms/protocol.py` - MethodSCRIPT data packet decoder (hex → float)
- `src/comms/mux.py` - MUX16 GPIO channel addressing (channels 1-16)
- `src/techniques/scripts.py` - MethodSCRIPT generator for all techniques
- `src/engine/measurement_engine.py` - QThread-based measurement orchestrator
- `src/gui/main_window.py` - Application window assembling all panels
- `src/gui/plot_widget.py` - pyqtgraph live plot with per-channel curves
- `src/gui/controls.py` - Connection, technique, channel, and measurement panels
- `src/data/models.py` - Dataclasses for results, configs, channels
- `src/data/exporters.py` - CSV + .pssession export
- `src/data/incremental_writer.py` - Auto-save CSV writer (per MUX loop)
- `src/data/presets.py` - Measurement preset manager with built-in NO Sensing

### Use Cases
1. Run cyclic voltammetry across 16 electrode channels with live overlay plots
2. Perform EIS on selected channels and export Nyquist data for analysis in CMU.49.011
3. Multi-channel chronoamperometry for biosensor calibration with real-time current monitoring
4. Run NO sensing preset with auto-save for crash-safe in-vivo experiments (DARPA IV&V)

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
  * ``MainWindow(QMainWindow)`` with dock-based layout: left dock (ConnectionPanel, TechniquePanel, ChannelPanel, MeasurementControlPanel), centre (LivePlotWidget), bottom dock (QPlainTextEdit log console with attached logging handler)
  * Wires all Qt signals: engine ``data_point_ready`` to plot ``on_data_point``, engine ``measurement_finished``/``measurement_error``/``channel_changed``/``measurement_started`` to status bar and panel state updates, control panel signals to engine ``start_measurement``/``abort``/``halt``/``resume``
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
