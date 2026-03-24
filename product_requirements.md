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

## Risks

| Risk | Impact | Likelihood | Mitigation |
|------|---------|------------|------------|
| MethodSCRIPT version mismatch with device firmware | High | Medium | Query firmware version on connect, validate capability codes |
| Serial buffer overflow during fast acquisition | Medium | Medium | XON/XOFF flow control, configurable read buffer size |
| .pssession format incompatibility | Low | Low | Test exports against CMU.49.011 parser |
