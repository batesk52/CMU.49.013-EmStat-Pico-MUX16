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
