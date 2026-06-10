# System Architecture

## Overview

Four-layer architecture: Communication → Engine → Data → GUI. The communication layer handles raw serial I/O and MethodSCRIPT protocol. The engine orchestrates measurements and MUX switching. The data layer models results and handles export. The GUI provides real-time visualization and user controls via PyQt6 + pyqtgraph.

## Design Principles

1. **Thread separation** - Serial I/O and measurement execution run in a QThread; GUI never blocks on hardware
2. **Signal-driven updates** - Measurement data flows to GUI via Qt signals (thread-safe, decoupled)
3. **Script generation, not hardcoding** - Techniques are parameterized MethodSCRIPT templates, not raw command strings
4. **Safety-first** - Every generated script includes `on_finished: cell_off`; abort always available

## System Components

### Communication Layer (`src/comms/`)
```
Serial connection management and MethodSCRIPT protocol handling.
- Interface: UART 230400/8N1 via pyserial with XON/XOFF
- Protocol: MethodSCRIPT command/response with hex data packet decoding
- MUX: GPIO-based channel addressing for 16-channel multiplexer
```

### Engine Layer (`src/engine/`)
```
Measurement orchestration running in background thread.
- Builds MethodSCRIPT from technique + parameters + channel list
- Sends script to device, streams parsed data points via Qt signals
- Handles halt/resume/abort commands during execution
```

### Data Layer (`src/data/`)
```
Data models, file export, incremental auto-save, and measurement presets.
- Dataclasses for measurement results, technique configs, channel data
- CSV export with per-channel files
- .pssession JSON export compatible with CMU.49.011
- Incremental CSV writer for crash-safe auto-save at MUX loop boundaries
- Measurement presets loaded from JSON (ships with NO Sensing preset)
```

### GUI Layer (`src/gui/`)
```
PyQt6 application with pyqtgraph live plotting.
- Main window with docked panels (connection, technique, channels, plot)
- Real-time plot updates via signal/slot from engine thread
- Technique-aware axis labels and plot types
```

## Module Structure

### Module: `comms`

#### `serial_connection.py`
**Purpose:** Manage physical serial connection to EmStat Pico
**Key Classes:**
- `PicoConnection` - Connect/disconnect, send commands, read responses
**Interfaces:**
- `connect(port: str) -> None`
- `disconnect() -> None`
- `send_command(cmd: str) -> str`
- `send_script(lines: list[str]) -> None`
- `read_response() -> str`
- `get_firmware_version() -> str`

#### `protocol.py`
**Purpose:** MethodSCRIPT data packet parsing and variable type mapping
**Key Classes:**
- `PacketParser` - Decode `P...` data lines into measurement values
**Key Functions:**
- `decode_value(hex_str: str, prefix: str) -> float` - 28-bit hex + SI prefix to float
- `parse_packet(line: str) -> dict[str, float]` - Full packet line to named values
- `parse_var_type(code: str) -> str` - 2-char code to variable name
**Constants:**
- `SI_PREFIXES` - Mapping of prefix chars to exponents
- `VAR_TYPES` - Mapping of 2-char codes to measurement quantities

#### `mux.py`
**Purpose:** MUX16 channel address calculation and GPIO script generation
**Key Classes:**
- `MuxController` - Channel addressing and MethodSCRIPT snippets for MUX
**Interfaces:**
- `channel_address(channel: int) -> int` - Channel 1-16 to 10-bit GPIO address
- `gpio_config_script() -> list[str]` - MethodSCRIPT lines to configure GPIO
- `select_channel_script(channel: int) -> list[str]` - MethodSCRIPT lines to switch channel
- `scan_channels_script(channels: list[int]) -> list[str]` - Loop over selected channels

### Module: `techniques`

#### `scripts.py`
**Purpose:** Generate MethodSCRIPT for each electrochemical technique
**Key Classes:**
- `TechniqueScript` - Base with shared preamble/postamble (cell_on, on_finished, etc.)
- Technique-specific subclasses or factory method for each technique
**Interfaces:**
- `generate(technique: str, params: dict, channels: list[int]) -> list[str]` - Full script
- `supported_techniques() -> list[str]` - List all available techniques
- `technique_params(technique: str) -> dict` - Default parameters for a technique

### Module: `data`

#### `models.py`
**Purpose:** Dataclasses for measurements, channels, and technique configuration
**Key Classes:**
- `TechniqueConfig` - Technique name + parameter dict
- `DataPoint` - Single measurement (timestamp, channel, variable_name, value)
- `MeasurementResult` - Collection of DataPoints for one run, with metadata
- `ChannelData` - Per-channel subset of a MeasurementResult

#### `exporters.py`
**Purpose:** Export measurement data to CSV and .pssession formats
**Key Classes:**
- `CSVExporter` - Per-channel CSV files with headers
- `PsSessionExporter` - UTF-16 JSON matching PalmSens .pssession format
**Interfaces:**
- `export_csv(result: MeasurementResult, output_dir: str) -> list[str]`
- `export_pssession(result: MeasurementResult, output_path: str) -> str`

#### `incremental_writer.py`
**Purpose:** Crash-safe incremental CSV writing during measurement
**Key Classes:**
- `IncrementalCSVWriter` - Append-mode CSV writer with fsync
**Interfaces:**
- `start(technique, params, device_info, channels, output_dir) -> str`
- `flush_points(points: list[DataPoint]) -> int`
- `finish() -> list[str]`

#### `presets.py`
**Purpose:** Measurement preset management with JSON persistence
**Key Classes:**
- `Preset` - Named measurement configuration dataclass
- `PresetManager` - Load/save/add/delete presets from JSON
**Interfaces:**
- `list_presets() -> list[str]`
- `get_preset(key: str) -> Optional[Preset]`
- `add_preset(key: str, preset: Preset) -> None`

### Module: `engine`

#### `measurement_engine.py`
**Purpose:** Orchestrate measurements in background thread
**Key Classes:**
- `MeasurementEngine(QThread)` - Runs measurement, emits data via signals
**Qt Signals:**
- `data_point_ready(DataPoint)` - Emitted for each decoded data point
- `measurement_started(str)` - Technique name
- `measurement_finished()` - Normal completion
- `measurement_error(str)` - Error message
- `channel_changed(int)` - MUX channel switch
**Interfaces:**
- `start_measurement(connection, technique_config, channels) -> None`
- `abort() -> None`
- `halt() -> None`
- `resume() -> None`

### Module: `gui`

#### `main_window.py`
**Purpose:** Top-level application window, layout, and menu bar
**Key Classes:**
- `MainWindow(QMainWindow)` - Assembles all panels, wires signals/slots

#### `plot_widget.py`
**Purpose:** Live pyqtgraph plot with per-channel curves
**Key Classes:**
- `LivePlotWidget(pg.PlotWidget)` - Real-time plot with channel color coding
**Interfaces:**
- `add_point(channel: int, x: float, y: float) -> None`
- `clear() -> None`
- `set_axes(x_label: str, y_label: str) -> None`
- `set_technique(technique: str) -> None` - Configure axes for technique type

#### `controls.py`
**Purpose:** Control panels — connection, technique parameters, channel selector
**Key Classes:**
- `ConnectionPanel(QWidget)` - COM port dropdown, connect/disconnect buttons, status LED
- `TechniquePanel(QWidget)` - Technique combo box + dynamic parameter fields
- `ChannelPanel(QWidget)` - 16 checkboxes + select all/none, channel labels
- `MeasurementControlPanel(QWidget)` - Start, Stop, Halt, Resume buttons

## Data Flow

```
User selects technique + params + channels in GUI
     ↓
MeasurementEngine.start_measurement()
     ↓
TechniqueScript.generate() → MethodSCRIPT lines
     ↓
PicoConnection.send_script() → Serial UART
     ↓
Device streams data packets (P...\n lines)
     ↓
PacketParser.parse_packet() → DataPoint
     ↓
Qt Signal: data_point_ready(DataPoint)
     ↓
LivePlotWidget.add_point() + MeasurementResult buffer
     ↓
On END_LOOP → IncrementalCSVWriter.flush_points() [if auto-save enabled]
     ↓
On completion → Exporters write CSV / .pssession (or skip if auto-saved)
```

## Design Patterns

### Pattern: Signal-driven data pipeline
**Where used:** Engine → GUI communication
**Why:** Qt signals are thread-safe and decouple the serial I/O thread from the GUI thread. The engine never touches GUI objects directly.

### Pattern: Script template composition
**Where used:** Technique script generation
**Why:** MethodSCRIPT requires specific command ordering (GPIO config → channel select → cell_on → measurement loop → on_finished). Templates ensure correct structure while allowing parameterization.

### Preset Sequencer (CMU.17.034) — planned design (2026-06-09)
**Status:** Plan only; Low priority. Blueprint checklist lives in `README.md`.

**Goal:** A PSTrace-"Scripts" equivalent — stack saved presets as draggable
blocks on a new sidebar tab and run them back-to-back on the MUX-16.

**Decisions:**
- **Preset schema unchanged.** `Preset` already carries `channels`,
  `electrode_config_mode` (external=Mode A / on_board=Mode B / manual=Mode C),
  and `re_ce_channels` (the Mode-C pairing). No redesign — only externalization
  and a verification that the *save* path persists the live electrode mode.
- **Presets externalized, imported via the dropdown.** Preset files live OUTSIDE
  the repo. The only way to point at a file is an "Import preset file…" entry at
  the bottom of the preset combobox (file dialog); the chosen path is remembered
  as last-used and auto-loaded next launch. This avoids any hardcoded
  Drive-vs-local default and matches how a user naturally re-selects a file on a
  new run. On-disk format is JSON under a custom extension with a versioned
  wrapper (`format`/`version`) so the loader can evolve.
- **Sequences in a separate file** (`*.mux16seq`), not co-stored with presets.
  Steps reference presets by name; the sequence file is portable on its own.
- **Runner reuses the engine, not a new execution path.** `SequenceRunner`
  chains `engine.start_measurement` calls, advancing on the existing
  `measurement_finished` signal and gating on `isRunning()` so the single-run
  guard is honoured. The interactive export prompt is suppressed in "sequence
  mode"; auto-save is opt-in (the same GUI toggle as single runs), with EIS/GEIS
  steps provenance-forced per step (`forces_auto_save` in `models.py`) so their
  `_script.mscr` always lands. Auto-saving entries each write into their own
  `<export_dir>/<stamp>_sequence/stepNN_<technique>/` dir (`exact_dir` writer
  mode — unique per entry, repeats included, so same-second runs can't collide),
  and the finished handler adds each auto-saved step's `.pssession`. Steps that
  did NOT auto-save are retained and offered for save from the single terminal
  hook (`sequence_stopped` — fired on finish, stop, AND error), producing the
  identical `stepNN_<technique>` layout via shared helpers in `exporters.py`.

**Why not a device-side multi-method script?** The Pico's MethodSCRIPT can loop a
single technique but not chain heterogeneous techniques with per-step electrode
modes; orchestrating at the GUI/engine layer (one script per step) keeps each
step a normal, fully-validated `TechniqueConfig` run and reuses all existing
parsing, export, and the new RE/CE disconnect guard (CMU.17.035).

**Risks:** engine single-run guard (must gate on completion + thread finish);
Mode-C validation throws in `TechniqueConfig.__post_init__`, so the whole queue
must be validated before the first step launches.

### Error Handling Strategy
- Serial errors: catch in engine thread, emit `measurement_error` signal, auto-disconnect
- Script errors: device returns error codes — parse and display in GUI status bar
- MUX errors: validate channel numbers (1-16) before script generation
- Safety: `on_finished: cell_off` in every generated script prevents cell damage on abort

## Technology Stack

### Core Technologies
- **Language:** Python 3.10+
- **GUI:** PyQt6 + pyqtgraph
- **Serial:** pyserial
- **Data:** numpy, dataclasses
- **Testing:** pytest

### Key Dependencies
```
pyserial>=3.5
PyQt6>=6.5
pyqtgraph>=0.13
numpy>=1.21
```

---

## Embedded Claude Agent - Threading/Async Bridge (feature/e-cheMCP)

Three independent execution contexts must interact through exactly one discipline.

**Contexts**
- GUI thread - owns Qt widgets, the MeasurementEngine object, the PicoConnection, and all pyqtgraph
  plots. NEVER block it. NEVER touch serial here.
- Engine thread - the existing MeasurementEngine(QThread). Owns serial I/O. Unchanged. Communicates
  outward ONLY via its existing Qt signals.
- Agent thread - AgentWorker(QThread) that, in its run(), creates and runs its OWN asyncio event loop
  (asyncio.new_event_loop() / loop.run_until_complete(...)). The AsyncAnthropic streaming agentic loop
  lives here. NEVER touches a Qt widget, the engine, or the connection directly.

**The single rule:** the agent thread touches the engine/GUI only by marshaling onto the GUI thread,
and awaits engine completion via a thread-safe future resolved by a one-shot GUI-thread signal
connection.

Every tool that drives hardware follows this exact sequence (implemented once in bridge.py +
engine_adapter.py, reused by all tools):
1. Tool handler runs INSIDE the agent thread's asyncio loop (it is an async def).
2. It builds a TechniqueConfig (pure data - safe on any thread).
3. To start the measurement it calls `await run_on_gui(fn)` where fn is a zero-arg callable executed
   on the GUI thread, marshaled via a GUI-thread QObject plus QMetaObject.invokeMethod(...,
   Qt.QueuedConnection) (or a queued-signal trampoline). The GUI-thread fn:
   a. checks engine.isRunning() - if busy, signal back "engine busy" (tool returns an error result, no raise);
   b. connects ONE-SHOT slots to measurement_finished, measurement_error (and optionally
      channel_changed/data_point_ready for progress) that resolve a concurrent.futures.Future;
   c. calls engine.start_measurement(connection, config) - the SAME engine instance the plots are
      already wired to, so plots animate automatically with NO separate render path;
   d. returns immediately (does not wait).
4. Back in the asyncio loop, the tool does `result = await asyncio.wrap_future(future)` - non-blocking
   for the GUI; the agent thread's loop simply suspends this coroutine until the signal fires.
5. The one-shot slots (GUI thread) set the future's result (MeasurementResult / error string) and
   disconnect themselves so they never fire for the next run.
6. Tool returns a compact text/JSON summary (channels, point counts, key metrics) to the agent; large
   arrays are NOT dumped into the model context.

**Streaming out:** the agent loop emits Qt signals (agent_text_delta(str), tool_call_started(dict),
tool_call_finished(dict), tool_call_error(dict), agent_turn_done()) via a QObject bridge; the dock
panel slots run on the GUI thread and update the chat/tool-cards/figures. The agent thread NEVER calls
a widget method.

**Why this and not alternatives:** running the Anthropic async client in the GUI thread's event loop
would require qasync and risks blocking on serial-bound awaits; running tools that call engine.wait()
on the engine thread would deadlock signal delivery. One asyncio loop in a worker QThread +
future-bridged completion signals keeps the GUI responsive, reuses the existing engine/plot wiring
untouched, and is identically implementable by every coder.

**Mock parity:** MockMeasurementEngine exposes the identical signal set and start_measurement(conn,
cfg); it emits a short synthetic DataPoint stream then measurement_finished on a QTimer, so the
bridge, tools, plots, and validation gate exercise the real code path with zero hardware.
