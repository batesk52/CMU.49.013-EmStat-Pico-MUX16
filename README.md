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

### Use Cases
1. Run cyclic voltammetry across 16 electrode channels with live overlay plots
2. Perform EIS on selected channels and export Nyquist data for analysis in CMU.49.011
3. Multi-channel chronoamperometry for biosensor calibration with real-time current monitoring

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
- [ ] **measurement_engine.py** - Background measurement thread (Req 2, 3, 4)
  * QThread subclass: accepts connection, technique config, channels
  * Builds full MethodSCRIPT (technique + MUX loop) via scripts.py and mux.py
  * Sends script, reads streaming response lines, parses packets in real-time
  * Emits Qt signals: data_point_ready, measurement_started, measurement_finished, measurement_error, channel_changed
  * Supports abort (Z command), halt (h), resume (H) during execution
  * Buffers all DataPoints into MeasurementResult for post-run export
  * Validate: `python -c "from src.engine.measurement_engine import MeasurementEngine; print(MeasurementEngine.__doc__)"`

### Phase 3: GUI Application

#### src/gui/
- [ ] **plot_widget.py** - Live pyqtgraph plot with per-channel curves (Req 5)
  * pyqtgraph PlotWidget subclass with technique-aware axis configuration
  * Per-channel curves with distinct colors (16-color palette)
  * add_point(channel, x, y) for real-time updates from engine signals
  * Technique presets: I vs E (CV/LSV/DPV/SWV), I vs t (CA/CP), -Z'' vs Z' (EIS), etc.
  * Auto-range with manual zoom/pan override
  * Clear and reset between measurements
  * Validate: `python -c "from src.gui.plot_widget import LivePlotWidget; print('LivePlotWidget imported')"`

- [ ] **controls.py** - GUI control panels for connection, technique, channels (Req 6)
  * `ConnectionPanel`: COM port combo (auto-detect), connect/disconnect buttons, firmware version label, status indicator
  * `TechniquePanel`: technique dropdown, dynamic parameter fields (spin boxes, range inputs) that update per technique
  * `ChannelPanel`: 4x4 grid of channel checkboxes (1-16), select all/none buttons
  * `MeasurementControlPanel`: Start, Stop (abort), Halt, Resume buttons with enable/disable logic
  * Validate: `python -c "from src.gui.controls import ConnectionPanel; print('Controls imported')"`

- [ ] **main_window.py** - Main application window (Req 6)
  * QMainWindow with dock-based layout: left panel (controls), center (live plot), bottom (log/status)
  * Wire all signals: engine → plot widget, controls → engine
  * Menu bar: File (export, quit), Device (connect, disconnect), Help (about)
  * Status bar: connection state, measurement progress, current channel
  * On measurement complete: prompt for export (CSV + .pssession)
  * Application entry point (`if __name__ == "__main__"`)
  * Validate: `python -c "from src.gui.main_window import MainWindow; print('MainWindow imported')"`

### Phase 4: Data Export

#### src/data/
- [ ] **exporters.py** - CSV and .pssession file export (Req 7)
  * `CSVExporter`: one CSV per channel, columns based on technique (time, potential, current, impedance, phase)
  * `PsSessionExporter`: UTF-16 JSON matching PalmSens .pssession format for CMU.49.011 compatibility
  * Include metadata header: technique, parameters, timestamp, device serial, firmware version
  * Timestamped output directory in exports/ (format: YYYYMMDD_HHMMSS_technique)
  * Validate: `python -c "from src.data.exporters import CSVExporter; print('Exporters imported')"`
