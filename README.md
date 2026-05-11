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
- [ ] **scripts.py** - Add `bw_hz` to mode-2 technique defaults; parameterize `_preamble()` (closes BW hardcoding)
  * Add `"bw_hz": 400` to `_DEFAULTS["ca"]`, `_DEFAULTS["ca_alt_mux"]`, plus cv, lsv, dpv, swv, npv, acv, fca, pad, lsp, fcv, cp, ocp
  * Modify `_preamble()` (line 265) to use `_format_si(params.get("bw_hz", 400))` for `set_max_bandwidth` (line 281)
  * Leave `_preamble_eis()` and `_preamble_galvano()` hardcoded at 200 kHz (mode-3 stability lock)
  * Validate: `python -c "from src.techniques.scripts import _preamble; assert 'set_max_bandwidth 4' in '\n'.join(_preamble({'cr':'2u','bw_hz':4}))"`

#### src/gui/
- [ ] **controls.py** - Expose `bw_hz` as GUI combobox in technique panel (per-run BW selection without code edit)
  * Add `"bw_hz": ("Max Bandwidth", "Hz")` to `_PARAM_LABELS` (line 69)
  * In `_create_param_widget()` (line 493), render `bw_hz` as `QComboBox` with values `[0.4, 4, 40, 400, 4000, 40000, 200000]` Hz (model on existing `cr` combobox at lines 102-105)
  * Validate: `python -m src.gui.main_window` — CA panel shows Max Bandwidth dropdown defaulting to 400

#### presets/
- [ ] **presets.json** - Add `bw_hz: 400` to built-in `no_sensing` preset (explicit declaration, backwards-compat)
  * Edit `no_sensing.params` to include `"bw_hz": 400`
  * Validate: `python -c "from src.data.presets import PresetManager; assert PresetManager().get('no_sensing').params['bw_hz']==400"`

#### tests/techniques/
- [ ] **test_scripts.py** - NEW. Assert preamble respects `bw_hz`; regression guard
  * Parametrize: `_preamble({'cr':'2u','bw_hz':bw})` emits `set_max_bandwidth {expected}` for bw ∈ [0.4, 4, 40, 400, 4000]
  * Default (no `bw_hz`) still emits `set_max_bandwidth 400`
  * `_preamble_eis()` and `_preamble_galvano()` emit `set_max_bandwidth 200k` regardless of params
  * Validate: `pytest tests/techniques/test_scripts.py -v`

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
