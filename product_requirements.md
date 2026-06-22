# Product Requirements Document

## Project Overview

**Project Name:** EmStat Pico MUX16 Controller
**Project Code:** CMU.49.013
**Date Created:** 2026-03-15
**Primary Stakeholder:** Tzahi Cohen-Karni Lab (CMU)

## Problem Statement

The lab relies on individual PalmSens potentiostats for electrochemical measurements, limiting throughput to one channel at a time per device. The EmStat Pico MUX16 module enables a single potentiostat to serve 16 channels, but requires custom software to operate directly via MethodSCRIPT over serial — no existing GUI supports the integrated Pico+MUX16 module with live multi-channel visualization.

## Goals

### Primary Goals
1. Provide a Python GUI application to operate the EmStat Pico MUX16 with live plot updates
2. Support all PalmSens electrochemical techniques via MethodSCRIPT
3. Enable 16-channel multiplexed measurements with per-channel visualization

### Success Criteria
- [ ] Connect to EmStat Pico MUX16 via USB serial and confirm firmware version
- [ ] Run any supported technique on any combination of MUX16 channels
- [ ] Display live measurement data during acquisition
- [ ] Export results to both CSV and .pssession formats

## Functional Requirements

### Req 1: Serial Communication
**Description:** Establish and manage serial connection to EmStat Pico MUX16
**Acceptance Criteria:**
- Must connect at 230400 baud, 8N1 with XON/XOFF flow control
- Must detect device by querying firmware version (`t` command)
- Must handle idle mode vs script execution mode transitions
- Must gracefully handle disconnect/reconnect

### Req 2: MethodSCRIPT Protocol
**Description:** Parse and generate MethodSCRIPT commands and data packets
**Acceptance Criteria:**
- Must decode hex-encoded data packets (28-bit values with SI prefix)
- Must map variable type codes to measurement quantities (potential, current, impedance, etc.)
- Must generate valid MethodSCRIPT for all supported techniques
- Must handle measurement loop markers (M, *, L, +)

### Req 3: MUX16 Channel Control
**Description:** Switch and manage 16 multiplexer channels via GPIO
**Acceptance Criteria:**
- Must configure GPIO pins for MUX16 addressing (10-bit: 2 enable + 4 RE/CE + 4 WE)
- Must calculate correct addresses for channels 1-16 (Mux16 mode: WE and RE/CE switched together)
- Must support sequential and selective channel scanning
- Must support alternating MUX techniques (CA, CP, OCP)

### Req 4: Technique Support
**Description:** Generate MethodSCRIPT for all supported electrochemical techniques
**Acceptance Criteria:**
- Must support: LSV, DPV, SWV, NPV, ACV, CV, CA, FCA, CP, OCP, EIS, GEIS, PAD, LSP, FCV
- Must support MUX-alternating variants: CA_alt_mux, CP_alt_mux, OCP_alt_mux
- Must expose technique-specific parameters (potential range, scan rate, frequency, etc.)
- Must include proper cell_on/cell_off and on_finished safety handling

### Req 5: Live Plotting GUI
**Description:** Real-time visualization during measurements
**Acceptance Criteria:**
- Must update plots during data acquisition (not just after completion)
- Must support per-channel color coding for multiplexed runs
- Must display technique-appropriate axes (I vs E for CV, Z'' vs Z' for EIS, I vs t for CA, etc.)
- Must support zoom, pan, and auto-scale

### Req 6: Main Application GUI
**Description:** Full application window for instrument control
**Acceptance Criteria:**
- Must provide connection panel (COM port selection, connect/disconnect)
- Must provide technique selector with parameter fields
- Must provide MUX16 channel selector (checkboxes for channels 1-16)
- Must provide measurement controls (start, stop/abort, halt/resume)
- Must show device status and measurement progress

### Req 7: Data Export
**Description:** Save measurement results in multiple formats
**Acceptance Criteria:**
- Must export per-channel CSV files with headers (time, potential, current, etc.)
- Must export .pssession-compatible JSON for use with CMU.49.011 analysis pipeline
- Must include measurement metadata (technique, parameters, timestamp, channel)
- Must support batch export after multi-channel runs

## Non-Functional Requirements

### Performance
- Live plot update rate: minimum 10 Hz during acquisition
- Serial communication latency: under 50ms round-trip for commands
- GUI must remain responsive during long measurements (background threading)

### Reliability
- Graceful handling of serial disconnection mid-measurement
- `on_finished: cell_off` safety pattern in all generated scripts
- No data loss if GUI crashes (periodic buffer flush to disk)

### Usability
- Single-window application with logical panel layout
- Sensible defaults for all technique parameters
- Clear status indicators (connected/disconnected, measuring/idle)

## Constraints

### Technical Constraints
- **Language:** Python 3.10+
- **GUI Framework:** PyQt6 with pyqtgraph for live plotting
- **Serial:** pyserial with XON/XOFF flow control
- **Platform:** Windows (primary, COM ports), WSL/Linux (secondary)
- **Protocol:** MethodSCRIPT V1.6 over UART at 230400 baud

### Business Constraints
- **Timeline:** Priority week of 2026-03-14
- **Scope:** MUX16 mode only (not MUX256 mode)

## Dependencies

### External Dependencies
- PalmSens EmStat Pico MUX16 hardware (connected via USB-UART adapter)
- pyserial >= 3.5
- PyQt6 >= 6.5
- pyqtgraph >= 0.13
- numpy >= 1.21

### Internal Dependencies
- CMU.49.011-Electrochemistry: .pssession format reference for export compatibility

### Req 8: Incremental Auto-Save
**Description:** Auto-save CSV data during measurement for crash safety
**Acceptance Criteria:**
- Must auto-save CSV data at each MUX loop boundary during measurement
- Must create per-channel CSV files identical in format to manual export
- Must preserve all data collected up to the point of abort or crash
- Must be configurable (enable/disable toggle, output directory selection)
- Must call fsync after each flush for crash safety

### Req 9: Measurement Presets
**Description:** Save and load named measurement configurations
**Acceptance Criteria:**
- Must support named preset configurations (technique, params, channels, auto-save)
- Must ship with a built-in NO Sensing preset (CA_alt_mux, 0.85V, channels 1-8, auto-save on)
- Must allow users to save custom presets
- Must load presets on startup from a JSON file
- Built-in presets cannot be deleted

### Req 10: Electrode Configuration Modes
**Description:** High-level selector for how WE and RE/CE are wired across the MUX (PR #6)
**Acceptance Criteria:**
- Mode A (Separate external): RE/CE fixed at CH15; full 16-WE workflow
- Mode B (On-board combined): RE/CE fixed at CH16; full 16-WE workflow
- Mode C (Manual per-WE pairing): WE and RE/CE restricted to CH1-CH14 with per-WE RE/CE pairing; CH15/CH16 reserved as enclosure infrastructure
- `electrode_config_mode` + `re_ce_channels` carried on `TechniqueConfig`/`MeasurementResult`, persisted in presets, and emitted in CSV + .pssession metadata
- Per-mode validation in `TechniqueConfig.__post_init__` (Mode-C bounds, pairing length)

### Req 11: Configurable Measurement Bandwidth
**Description:** User-controllable mode-2 measurement bandwidth (PR #5, CMU.17.022)
**Acceptance Criteria:**
- `bw_hz` selectable per run for mode-2 techniques via a GUI dropdown
- Ladder: 0.4 / 4 / 40 / 400 / 4000 / 40000 / 200000 Hz; default 400 Hz preserves legacy behavior
- EIS/GEIS remain locked to high-speed mode 3 (200 kHz); they expose no `bw_hz`

### Req 12: Preset Sequencer
**Description:** PSTrace-"Scripts" equivalent — chain saved presets back-to-back on the MUX-16 (PR #13, CMU.17.034)
**Acceptance Criteria:**
- Reorderable steps run sequentially; each step is self-contained (technique, params, channels, electrode mode, repeat, delay)
- Sequences persist to a separate `*.mux16seq` file (presets in `*.mux16`), both outside the repo under `~/.emstat_pico_mux16/`
- Runner reuses the existing engine (one validated `TechniqueConfig` per step), gates on the single-run guard, and validates the whole queue before step 0
- Interactive export prompt suppressed in sequence mode; per-step auto-save (opt-in) into one `<stamp>_sequence/stepNN_<technique>/` parent, EIS/GEIS provenance-forced

### Req 13: Embedded Claude Agent
**Description:** In-app Claude agent dock that drives measurements and analysis (PR #14/#15, CMU.17.042)
**Acceptance Criteria:**
- Chat dock drives the SAME live plots by calling the existing `MeasurementEngine` via an `EngineAdapter`; no separate render path
- Tools: `run_cv/run_ca/run_eis/run_cp/run_geis`, `list_ports/connect/disconnect/device_status/abort_measurement`, `export_session`, vendored CMU.49.011 analysis (`load_session`, `analyze_cv/ecsa/ca/eis/cic/cp`), and preset/sequence persistence `save_preset/save_sequence/load_preset/load_sequence` — 22 tools total
- Self-tuning (PR #19, CMU.17.047): EIS/GEIS results carry a per-channel quality block (overload/NaN → "underranged" + `suggested_cr`; agent re-ranges up the mode-3 ladder until clean, stops at the top); CV/CA carry a noise-scope block (robust residual sigma after a centered median detrend); `analyze_eis` flags Rct as unreliable / a lower bound when the -Z'' semicircle apex was not captured. Preset/sequence tools open the native file dialog in-app (GUI thread) with an explicit-path fallback headless; writes are atomic and refuse to clobber a foreign file
- Qt<->asyncio bridge (`AgentWorker` QThread runs its own asyncio loop); engine completion awaited via a thread-safe future resolved by one-shot GUI-thread slots
- Mock engine provides a no-hardware path; agent module imports with no API key; API key + model set in File > Agent Settings
- No emojis; native deps (numpy/scipy/matplotlib Agg/pandas) eager-imported at module top; vendored 49.011 code is read-only

### Req 14: Headless MCP Server
**Description:** Expose the same run/analyze tools to Claude Code over MCP stdio (CMU.17.042 Batch 4)
**Acceptance Criteria:**
- MCP stdio server reuses the `tools.py` definitions; headless (mock engine when no GUI, `EMSTAT_MCP_PORT` for hardware)
- Eager native imports; no emojis; importable and listable with no hardware and no API key

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|---------|------------|------------|
| MethodSCRIPT version mismatch with device firmware | High | Medium | Query firmware version on connect, validate capability codes |
| Serial buffer overflow during fast acquisition | Medium | Medium | XON/XOFF flow control, configurable read buffer size |
| .pssession format incompatibility | Low | Low | Test exports against CMU.49.011 parser |
