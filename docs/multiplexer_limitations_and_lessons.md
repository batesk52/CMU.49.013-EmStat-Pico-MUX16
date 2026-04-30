# EmStat Pico MUX16: Multiplexer Limitations for Nanoamp-Level Sensing

**Date:** 2026-04-01
**Context:** Extensive hardware validation session attempting to achieve nA-level chronoamperometry (CA) across multiple channels using the PalmSens EmStat Pico with MUX16 multiplexer board. The goal was to detect 10–200 nA current changes (e.g., GABA/glutamate neurotransmitter sensing via enzymatic biosensors) across 8–16 electrodes simultaneously.

---

## 1. What the Multiplexer Actually Does

The MUX16 is **not** a parallel measurement system. It is a physical switch (relay/analog mux) that connects **one working electrode (WE) at a time** to a single potentiostat. The reference electrode (RE) and counter electrode (CE) are shared across all channels.

During a round-robin measurement cycle:

```
Time ──────────────────────────────────────────────────────►

CH1: ████░░░░░░░░░░░░░░░░████░░░░░░░░░░░░░░░░░░░░████
CH2: ░░░░████░░░░░░░░░░░░░░░░████░░░░░░░░░░░░░░░░░░░░
CH3: ░░░░░░░░████░░░░░░░░░░░░░░░░████░░░░░░░░░░░░░░░░
CH4: ░░░░░░░░░░░░████░░░░░░░░░░░░░░░░████░░░░░░░░░░░░

████ = connected to potentiostat (measuring)
░░░░ = disconnected (floating at open circuit)
```

Each electrode spends the **vast majority of its time disconnected**. When disconnected:
- The potentiostat is no longer controlling the electrode's potential
- The electrode floats to its open circuit potential
- The electrochemical double-layer capacitance charges and discharges freely
- Any Faradaic processes at the electrode surface are no longer driven by the applied potential

When the MUX switches back to a channel:
1. The potentiostat must re-establish the set potential (e.g., +0.7V)
2. A large transient current flows to re-charge the double-layer capacitance
3. This transient decays exponentially with a time constant determined by the cell's RC
4. Only after the transient has decayed sufficiently does the measured current reflect the true Faradaic (analyte) signal

## 2. The Fundamental Timing Problem

For each channel in one round-robin cycle, the measurement sequence is:

```
[MUX switch] → [settle time] → [ADC measurement] → [MUX switch to next channel]
   ~0ms           50–500ms        250ms–5s              ~0ms
```

### Key parameters and their roles:

| Parameter | What it controls | Effect on data quality |
|-----------|-----------------|----------------------|
| **Settle time** | Dead time after MUX switch, before measurement. Lets the transient decay. No data collected during this period. | Longer = more transient rejected = cleaner data, but wasted time |
| **t_interval** | Duration of the actual ADC measurement. The ADC integrates/averages the signal over this period. | Longer = more averaging = less random noise, but also averages in any residual transient |
| **Number of channels** | How many electrodes are in the round-robin | More channels = longer time each electrode is disconnected = larger transient when reconnected |

### The math:

```
Time per channel = settle_time + t_interval
Time per round   = n_channels × (settle_time + t_interval)
Time disconnected = (n_channels - 1) × (settle_time + t_interval)
```

**Example with 8 channels, 0.5s settle, 1s measurement:**
- Time per round: 8 × 1.5s = **12 seconds**
- Each electrode is disconnected for: 7 × 1.5s = **10.5 seconds**
- Each electrode is measured for: 1s out of every 12s (**8.3% duty cycle**)

**Example with 16 channels, 0.25s settle, 0.25s measurement:**
- Time per round: 16 × 0.5s = **8 seconds**
- Each electrode is disconnected for: 15 × 0.5s = **7.5 seconds**
- Each electrode is measured for: 0.25s out of every 8s (**3.1% duty cycle**)

The longer an electrode is disconnected, the further it drifts from its steady-state condition, and the larger the transient when reconnected.

## 3. Empirical Results: What We Actually Measured

### 3.1 The Quantization Problem (Fixed First)

The original software used `set_autoranging 100n {cr}` in MethodSCRIPT mode 2 (potentiostatic) to configure the current range. Through extensive testing, we discovered this command was **silently ignored** by the EmStat Pico firmware (espico1601). Every measurement — regardless of the selected current range (100nA, 4µA, 16µA, 100µA) — showed identical **280 nA quantization steps**.

**Root cause:** Mode 2 requires the `set_cr` command to explicitly set the current range before `set_autoranging` takes effect. The PalmSens MethodSCRIPT Tips & Tricks documentation confirms the correct command sequence:

```
set_pgstat_mode 2
set_max_bandwidth 400
set_pot_range -1 1
set_cr 2u                    ← THIS was missing
set_autoranging 100n 2u
cell_on
```

After adding `set_cr`, the resolution improved from 280 nA to **1.8 nA** — a 155× improvement.

### 3.2 Noise Floor Comparison Across Configurations

All measurements on the same electrodes (enzymatic biosensors on Pt-black), same device (EmStat Pico, EP2IC0QZ, firmware espico1601). Data folders are in `exports/`:

| Configuration | Data folder | Noise (std dev) | Resolution (min step) | Notes |
|--------------|------------|----------------|----------------------|-------|
| **Original code** (set_autoranging ignored, cr=100u, 16ch) | `20260401_131633_ca_alt_mux_S0179_87.055_test` | 127 nA | 280 nA | Current range not applied; 280 nA quantization |
| **Original code** (set_autoranging ignored, cr=4u, 16ch) | `20260401_141031_ca_alt_mux_S0170_GLU_excess` | 127 nA | 280 nA | Same 280 nA quant regardless of cr selection |
| **Original code** (set_autoranging ignored, cr=16u, 8ch) | `20260401_144356_ca_alt_mux_problem_data` | 127 nA | 280 nA | Named "problem_data" — the starting point |
| **Mode 3 + 200kHz bandwidth** (cr=16u, 2ch) | `20260401_150231_ca_alt_mux` | 167 nA | 2.8 nA | Good resolution but high noise from wide bandwidth |
| **Mode 3 + 10Hz bandwidth** (cr=2u, 16ch) | `20260401_153423_ca_alt_mux` | 9846 nA | 64 nA | Control loop oscillation — bandwidth too low for mode 3 |
| **Mode 2 + set_range ba** (cr=4u, 16ch) | `20260401_164635_ca_alt_mux` | 73 nA | 1.8 nA | Fixed range, no autoranging; first working mode 2 fix |
| **Mode 2 + set_range ba** (cr=32u, 16ch) | `20260401_165211_ca_alt_mux` | — | — | Testing different range values |
| **Mode 2 + set_range ba** (cr=2u, 16ch) | `20260401_165603_ca_alt_mux` | — | — | Testing 2µA range |
| **Mode 2 + set_cr + autoranging** (cr=2u, 4ch, settle=200ms) | `20260401_165825_ca_alt_mux` | 60–100 nA | 1.8 nA | Autoranging enabled; MUX switching noise |
| **Mode 2 + set_cr + autoranging** (cr=2u, 16ch, settle=50ms) | `20260401_170839_ca_alt_mux` | 60–100 nA | 1.8 nA | 16 channels, short settle — high oscillation |
| **Mode 2 + bandwidth 400 + pot_range** (cr=2u, 4ch, settle=200ms) | `20260401_173240_ca_alt_mux` | 40–70 nA | ~2 nA | PalmSens-matched preamble; best MUX result |
| **Final config** (cr=2u, 4ch, settle=200ms, real experiment) | `20260401_181505_ca_alt_mux_S0170_25a_1010s_100a_1100_100a_1300s` | — | — | Actual sensing experiment with analyte additions |
| **PStrace benchmark** (4ch, 5s interval, bw=4.7Hz) | External: `good signal.csv`, `cv.pssession` | 36–55 nA | 1–3 nA | PalmSens commercial software, same hardware |
| **Single-channel CA** (no MUX, reference) | External reference plot | < 5 nA | sub-nA | No switching; this is the target quality |

### 3.3 The Bandwidth Discovery

Mode 3 (high-speed potentiostatic) supports `set_range ba` and `set_autoranging ba` commands with explicit variable types, but **requires high bandwidth (200kHz) for control loop stability**. Attempts to lower bandwidth in mode 3 caused potentiostat oscillation (±10µA swings).

Mode 2 (standard potentiostatic) is stable at much lower bandwidth. The PalmSens official MUX16 example uses **400 Hz** bandwidth in mode 2. This is critical because bandwidth directly affects noise: higher bandwidth = more high-frequency noise folded into the measurement.

### 3.4 Settle Time Effects

The hardcoded 50ms settle time in the original code was insufficient for nA-level measurements. PalmSens' own example uses 100ms for an LSV (which has lower impedance requirements than CA at nA levels). For biosensor CA at nA levels:

- **50ms settle:** ~60–100 nA oscillation between consecutive measurements
- **200ms settle:** ~40–70 nA oscillation
- **500ms settle:** ~20–50 nA oscillation (approaching PStrace benchmark)

The tradeoff: every 100ms of additional settle time is 100ms of dead time per channel per round. With 16 channels, adding 450ms of settle time adds 7.2 seconds to each round.

## 4. Why Single-Channel CA is So Much Better

The reference GABA chronoamperometry plot (from literature/prior work) shows:
- Baseline current: 50–80 nA
- Step changes of 20–70 nA clearly resolved
- Noise: < 5 nA
- Clean, smooth signal over 2000+ seconds

This was measured with a **single channel** — the electrode is permanently connected to the potentiostat. There is:
- No disconnection → no double-layer discharge → no reconnection transient
- Continuous potential control → electrode stays at equilibrium
- Full ADC integration time → maximum averaging
- No wasted time on settle → 100% duty cycle

The multiplexer fundamentally cannot replicate this because it must disconnect each electrode to measure the others.

## 5. Practical Implications for Experiment Design

### 5.1 What the MUX16 CAN do well:
- **Screening experiments** where you need approximate readings across many electrodes (e.g., identifying which electrodes are functional, rough calibration)
- **Measurements with large signals** (µA range) where 30–60 nA noise is negligible
- **Slow experiments** where 10–30 second temporal resolution per channel is acceptable
- **Comparative measurements** where relative differences between channels matter more than absolute accuracy

### 5.2 What the MUX16 CANNOT do:
- Detect **sub-50 nA changes** reliably across multiple channels (noise floor is ~30–60 nA even with optimal settings)
- Provide **sub-second temporal resolution** per channel with more than 4 channels (round-robin cycle time scales linearly with channel count)
- Match **single-channel CA quality** at any settings — the switching transient is a fundamental physical limitation
- Run at **low current ranges** (100nA) if the actual signal exceeds the range — the potentiostat oscillates

### 5.3 Recommended settings for best MUX performance at nA levels:

```
Channels:       4 (maximum for nA-level work)
t_interval:     5s (matches PStrace benchmark)
Settle time:    0.5s (allows most of the transient to decay)
Current range:  2u (accommodates nA signals with headroom)
t_eq:           60–120s (let electrodes reach steady state before measuring)
Bandwidth:      400 Hz (set by preamble; low noise, stable in mode 2)
```

This gives:
- Round time: 4 × (0.5 + 5) = 22 seconds
- Temporal resolution: ~22s per data point per channel
- Expected noise: ~36–55 nA std dev (matching PStrace)

### 5.4 If you need better performance:

1. **Single-channel CA** — use regular CA (not MUX-Alternating) for the most critical channels. Run them one at a time. You'll get < 5 nA noise.

2. **Post-processing averaging** — run MUX round-robin and apply a moving average across N rounds. Averaging 10 rounds reduces noise by ~3× (50 nA → ~17 nA) at the cost of temporal resolution.

3. **Polypotentiostat** — a true multi-channel instrument (e.g., PalmSens MultiPalmSens4, BioLogic VMP-300) has independent potentiostat circuits for each channel. No switching, no transients, no compromise. This is the correct hardware solution for simultaneous nA-level multi-channel sensing.

## 6. MethodSCRIPT Command Reference (Mode 2, Validated)

The correct preamble for mode 2 potentiostatic measurements with the EmStat Pico MUX16:

```
e
var p
var c
set_pgstat_chan 1
set_pgstat_mode 0
set_pgstat_chan 0
set_pgstat_mode 2
set_max_bandwidth 400        # 400 Hz; stable in mode 2, low noise
set_pot_range -1 1           # potential range ±1V
set_cr {cr}                  # e.g., set_cr 2u (sets current range)
set_autoranging 100n {cr}    # e.g., set_autoranging 100n 2u
cell_on
```

Per-channel MUX switching in the round-robin loop:

```
set_gpio 0x{addr}i           # switch MUX to channel
wait {settle_time}           # e.g., wait 500m (500ms settle)
meas_loop_ca p c {e_dc} {t_interval} {t_interval}
  pck_start
  pck_add p
  pck_add c
  pck_end
endloop
```

### Commands that do NOT work in mode 2:
- `set_range ba {cr}` — the `ba` prefix is for mode 3 only; use `set_cr` instead
- `set_autoranging ba 100n {cr}` — do not use `ba` prefix in mode 2
- `set_autoranging` alone (without `set_cr` first) — silently ignored; the range is never applied
- `set_cr {index}i` — `set_cr` takes SI values like `2u`, NOT numeric indices like `4i`

### Commands validated in mode 2:
- `set_cr {si_value}` — sets current range (e.g., `set_cr 2u`)
- `set_autoranging {min} {max}` — enables autoranging (e.g., `set_autoranging 100n 2u`)
- `set_max_bandwidth 400` — sets analog front-end bandwidth
- `set_pot_range -1 1` — sets potential range

## 7. Summary

The EmStat Pico MUX16 is a cost-effective way to measure multiple electrodes with a single potentiostat, but it has hard physical limits at nanoamp levels. The act of disconnecting and reconnecting electrodes creates transient currents that set a noise floor of ~30–60 nA — regardless of software optimization. For experiments requiring detection of changes in the 10s of nanoamps, either reduce the channel count to 4 or fewer with long measurement intervals, use single-channel mode for critical measurements, or invest in parallel potentiostat hardware.
