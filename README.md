# EmStat Pico MUX16 Controller

Python GUI application for operating the PalmSens EmStat Pico MUX16 potentiostat via MethodSCRIPT over serial, with live multi-channel plotting and data export.

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
  * Maps 2-char variable type codes to names (da=set_potential, ba=current, ab=measured_potential, etc.)
  * Tracks measurement loop markers (M, *, L, +) with stateful depth and channel index
  * Parses optional metadata fields (status bits, current range) after comma separators
  * Provides `SI_PREFIXES` and `VAR_TYPES` constant dictionaries for external use

- **mux.py** - MUX16 channel address calculation and GPIO control (Req 3)
  * Calculates 10-bit GPIO addresses for channels 1-16 (MUX16 mode: WE and RE/CE switched together)
  * Address format: bits[9:8]=enable (inverted), bits[7:4]=RE/CE, bits[3:0]=WE
  * Generates `set_gpio_cfg 0x3FFi 1` initialisation script for configuring all pins as outputs
  * Generates `set_gpio <addr>i` channel selection commands for individual channel switching
  * Generates multi-channel scan loops with `meas_loop_for` and `add_var` stepping
  * Validates channel numbers (1-16) with descriptive `MuxError` exceptions
  * Supports both bare scan loops and scan-with-measurement-body composition

### Phase 2: Measurement Core

#### src/techniques/
- **scripts.py** - MethodSCRIPT generator for all electrochemical techniques (Req 4)
  * Template-based generation: preamble (pgstat config, cell_on) followed by technique measurement loop and postamble (on_finished: cell_off)
  * Supports 15 standard techniques (LSV, DPV, SWV, NPV, ACV, CV, CA, FCA, CP, OCP, EIS, GEIS, PAD, LSP, FCV) and 3 MUX-alternating variants (ca_alt_mux, cp_alt_mux, ocp_alt_mux)
  * Parameterised via `generate(technique, params, channels)` with defaults for all parameters (potential range, scan rate, frequency range, step size, amplitude, current range)
  * Formats all values with MethodSCRIPT SI prefix notation (e.g., `500m` for 0.5 V) via internal `_format_si()` helper
  * Includes pck_start/pck_add/pck_end blocks configured per technique type (voltammetry, amperometry, potentiometry, EIS)
  * Multi-channel runs wrap the technique body in a MUX scan loop via `MuxController.scan_channels_script_with_body()`

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
  * `CSVExporter` writes one CSV per channel with technique-aware column ordering (voltammetry: potential/current, EIS: frequency/impedance/phase, amperometry: current/potential/charge)
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
  * Ships with built-in `no_sensing` preset: CA_alt_mux at 0.85V, channels 1-8, auto-save enabled
  * Built-in presets cannot be deleted; user presets can be added and removed
