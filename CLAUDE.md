# Project Conventions

Template: codebase

## Development Guidelines

### Code Style
- **Language:** Python 3.10+
- **Style Guide:** PEP 8
- **Line Length:** 88 characters (Black formatter)
- **Docstrings:** Google style

### Naming Conventions
- **Files:** snake_case.py
- **Classes:** PascalCase
- **Functions:** snake_case
- **Constants:** UPPER_SNAKE_CASE
- **Private:** _leading_underscore

## Project-Specific Patterns

### Serial Communication
- Always use XON/XOFF flow control (pyserial `xonxoff=True`) per PalmSens comm protocol doc
- All commands terminated with `\n` (LF only, never CR)
- Device echoes first character of command — strip before parsing response
- Timeout reads at 5 seconds for commands, 60+ seconds during measurements

### MethodSCRIPT
- Values use SI prefix notation: `500m` = 0.5, `100u` = 0.0001
- Integer values suffixed with `i`: `0x3FFi`, `16i`
- No empty lines within scripts (device interprets as end-of-script)
- Every script MUST include `on_finished:\n  cell_off` for safety

### MethodSCRIPT Firmware
- Firmware v1.6+ required for compact `loop i <= e` MUX pattern with `set_gpio i`
- `store_var` integer values MUST have `i` suffix: `store_var i 0i aa` (not `0 aa`)
- `add_var i 0b01` — use binary format for GPIO address increment
- `meas_loop_ca` argument order: `p c <e_dc> <t_interval> <t_run>` (interval before run)
- `meas_loop_eis` final argument is the **DC potential** (manual §14.46), not a flag — pass `e_dc`/`i_dc`, not `0`
- SI values are an **integer mantissa + prefix** (`2565`, `100k`, `500m`); a decimal mantissa (`2.565k`) is rejected with `e!4004`. `_format_si` enforces this — do not hand-build values with decimals
- **EIS/GEIS run high-speed mode 3, where the current range MUST be pinned** (`set_autoranging ba {cr} {cr}`, min==max). In-loop range switching corrupts the spectrum on FW 1.6.01 (low-freq Nyquist arc reverses / Z' goes negative). Mode-2 techniques (CV/CA/...) keep real autoranging. Mode-3 has a different valid current-range ladder (`100n,1u,6u,13u,25u,50u,100u,200u,1m,5m`); the mode-2 values `2u`/`63u` return no data in mode 3 (see `parameter_form.current_ranges_for`)
- **EIS Rct is only meaningful if the -Z'' semicircle apex is captured.** The vendored EISAnalyzer derives Rct from the max of |Z''|; if that max lands on the lowest swept frequency (the arc is still rising at `freq_end`), the reported "Rct" is a meaningless extrapolation. `vendor_analysis._eis_apex_assessment` detects this, NULLs `rct_ohm`/`peak_frequency_hz`/`time_constant_s`, and reports `rct_lower_bound_ohm` instead — lower `freq_end` and re-run rather than quoting it. Rs and |Z|@1kHz stay valid. The vendored math is untouched/read-only (PR #19)

### MUX16 Addressing
- Hardware labels are 1-indexed (CH1-CH16), GPIO addresses are 0-indexed
- WE only is multiplexed; RE/CE is shared (common reference/counter electrode)
- Enable bits (9:8) are inverted: 0 = enabled, 1 = disabled

### Data Packet Decoding
- Packet format: `Pvar1;var2;...varN\n`
- Variable: 2-char type code + 7-char hex value + 1-char SI prefix
- Decode: `(hex_to_uint(value) - 2^27) * 10^(SI_exponent)`
- Variable types: `da`=set_potential, `ab`=potential, `ba`=current, `cb`=impedance, `ca`=phase, `cc`=zreal, `cd`=zimag, `dc`=set_frequency

### Threading
- GUI thread: NEVER perform serial I/O or blocking waits
- Engine thread (QThread): all serial communication happens here
- Data flows engine→GUI via Qt signals only (never share mutable state)
- Abort/halt/resume commands sent from GUI thread via PicoConnection (thread-safe write)

### Error Handling
- Serial disconnection: emit error signal, set connection state to disconnected, never raise in GUI thread
- MethodSCRIPT errors: parse device error codes (Appendix A of comm protocol), display in status bar
- Always validate channel numbers (1-16) before generating MUX scripts

### Testing
- **Project tests** in `tests/` mirror `src/` structure
- **Agent validation scripts** in `claude_test_files/`
- Mock serial port for unit tests (do NOT require hardware)
- Use pytest fixtures for PicoConnection and MeasurementEngine setup

## Dependencies

### Required Libraries
```
pyserial>=3.5
PyQt6>=6.5
pyqtgraph>=0.13
numpy>=1.21
```

## Domain Terminology

| Term | Definition |
|------|------------|
| MethodSCRIPT | PalmSens proprietary scripting language for instrument control |
| MUX16 | 16-channel multiplexer that switches WE and RE/CE together |
| WE | Working Electrode — the electrode being measured |
| RE/CE | Reference/Counter Electrode pair |
| pck | Packet — data output block in MethodSCRIPT (pck_start/pck_add/pck_end) |
| SI prefix | Single character denoting scale factor (a,f,p,n,u,m, ,k,M,G,T,P,E) |
| pgstat | Potentiostat — the measurement hardware |

## File Organization

### Source Code
- Keep files under 300 lines
- One class per file for major components
- Group related utilities in module `__init__.py`

### Exports
- Results in `exports/`
- Directory per run: `YYYYMMDD_HHMMSS_technique/`
- Per-channel CSV + single .pssession per run

## Reference Documentation

- [MethodSCRIPT V1.6 Manual](https://assets.palmsens.com/app/uploads/2024/08/MethodSCRIPT-v1_6.pdf)
- [EmStat Pico Communication Protocol V1.5](https://www.palmsens.com/app/uploads/2025/03/Emstat-Pico-communication-protocol-V1.5.pdf)
- [Getting Started with EmStat Pico MUX16](https://www.palmsens.com/app/uploads/2021/06/Getting-Started-with-the-Emstat-Pico-MUX16.pdf)
- [PalmSens MethodSCRIPT Examples (GitHub)](https://github.com/PalmSens/MethodSCRIPT_Examples)
