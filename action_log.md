# Action Log

Project-specific task tracking and history.

---

## In Progress

[Empty - no tasks currently in progress]

---

## Completed

### 2026-06-05: Four PRs landed — #7 save-prompt-lag + 17 hardening + 2 CRITICAL review fixes, #8 E3 generator signatures, #10 wheel-scroll regression, #9 PSTrace method-string fidelity (main at `3d6b5e6`)
- [x | Session | 2026-06-05] PR #7 merged 16:01 UTC (`a9f7992`) → bench validation surfaced 3 follow-ups, all merged same day. Final main `3d6b5e6`. Cumulative test count 69 → 79+ across the day (each PR added regression tests). Linear chain: review → bench → follow-up PRs → merge.

  - **PR #7 — Save-prompt-lag + 17 hardening findings + 2 CRITICAL review fixes** (`fix/save-prompt-lag-and-review-hardening` → `a9f7992`, 17:08-16:01 UTC). Three commits in the squash history:
    - `fecf7b5` — original 17 review-findings hardening + the save-prompt-lag root-cause fix. Engine read loop drops `read_timeout` to a 2 s confirm window after the first empty read (was 3 × 120 s = 360 s when the `+` END_MEAS marker was missed). EIS/GEIS keep the full timeout below 0.1 Hz. Self-diagnosing log + DEBUG sidebar switch added. Findings: E1 abort-on-error path de-energizes cell; E2 params copy; C1 lock-guarded port-timeout; C5 finally-restore in wait_until_idle; C6 close-on-identity-fail; C3/C4 docstring + echo-strip; D1 CSV column seeding; D2 .pssession multi-scan tail absorb; D3 atomic .pssession write; D4 per-row EIS rows; D5 RE/CE provenance fallback derived from electrode mode; G1/C2 worker-thread connect; G2 closeEvent log-handler cleanup; G3 plot throttle ~30 Hz + auto-downsample; G4 wheel forward; G5 auto_save_active reset. E3/E4/E5 deferred to hardware.
    - `5694a4f` — addressed 3-reviewer pass (engine+comms / data / GUI subagents) posted via `/review 7`. 2 CRITICAL fixed: (C1 GUI) removed `setClipToView` + `setDownsampling` entirely from non-EIS curves (CV/FCV would have rendered as a wedge — `x_var="set_potential"` is non-monotonic by design); the 30 Hz render throttle is the real O(N²) fix and the removal also eliminated W9 peak-whisker artifacts on noisy traces. (C2 GUI) `_ConnectWorker` leak + rapid-reconnect race — `finished → deleteLater` wired at creation, `self._connect_worker` nulled in both result slots, Disconnect button disabled in `set_connecting()` so a handshake can't race a disconnect. 7 WARNINGs addressed (W3 BaseException via `except BaseException` + re-raise; W4 timeout-restore invariant documented; W5 D2 REVERTED — recovering remainder made last scan longer than shared DataSet time array, restored known-loadable behavior; W6 D4 fully de-aliased via per-row `[dict(r) for r in …]` across all 14 consumers; W7 added `tests/data/test_incremental_writer_columns.py` — sparse first EIS packet must not drop columns, verified `_EIS_COLS` names match decoded `DataPoint.variables` keys; W8 atomic-write comment softened; W11 extracted `ToggleSwitch → gui/toggle_switch.py` + `ConnectWorker → gui/workers.py` per CLAUDE.md one-class-per-file). 3 WARNINGs kept with rationale: W1 EIS fast-confirm threshold was a false positive (the 2 s only engages after first empty; non-empty data resets `read_timeout` to `MEASUREMENT_TIMEOUT` — verified by tracing engine code at line 442+); W2 shallow copy is sufficient (only mutation in `scripts.py` is `params["_n_rounds"] = n_rounds`, top-level scalar); W10 Bode auto-range drift is pre-existing.
    - `591d880` — bench-validation follow-ups from running on espico1601 + 8.6 kΩ resistor on Ch1: (a) CV closed-cycle error now leads with "E begin and E vertex 2 must match!" using GUI field names; (b) PSTrace amperometry metadata key mapping fix — DC potential read under `E` not generic `E_DC` and equilibration under `T_EQUIL` not `T_EQ`; PSTrace was previously ignoring `E_DC` and showing its 0.5 V default. Verified against native PSTrace .pssession (device PS4A22Z003341); schema captured in `docs/references/pstrace_amperometry_method.md`. Map applied to ca/ca_alt_mux/fca; CV/EIS key mapping deferred to PR #9 (where it landed). (c) Added `claude_test_files/hw_validate_*.py` headless validation harnesses.
  - **Hardware test plan (`/review 7` comment) executed at the bench.** 14-min single-cell plan covered C2 race + E3 script-load + uneven-split multi-scan CV (E5 markers + W5 PSTrace load + C1 regression) + short EIS sweep (E4) + 5-min CA on baseline (G3 throttle long-run regression). Test plan paid off directly — T2 (E3 fca/cp/ocp script-load smoke) surfaced real generator bugs → PR #8; PSTrace metadata audit triggered by W5 PSTrace load → PR #9; PR #7's G4 wheel-fix discovered as a regression while exercising the sidebar mid-bench → PR #10.

  - **PR #10 — Wheel-scroll regression (`fix/disable-sidebar-wheel-scroll` → `54a71da`, 17:08 UTC, 16+/19- in 1 file).** PR #7's G4 made the app-wide no-wheel-scroll filter ALLOW the wheel to change a control's value when focused — reactivated the accidental scroll-to-change bug that PR #6's `33b1a93` had originally fixed. Sidebar fields (`t_eq`, `E vertex`, current range) silently changed value mid-typing. Fix: wheel over `QComboBox` / `QAbstractSpinBox` now NEVER changes value (focused or not); event forwarded to enclosing `QScrollArea` so the settings panel still scrolls over fields (no dead zones). Headless test asserts focused spinbox value unchanged AND scrollbar moves on wheel. 69 tests passing.

  - **PR #8 — E3 `meas_loop` generator signatures (`fix/e3-meas-loop-generators` → `b246778`, 17:19 UTC, 80+/15- in 2 files).** Resolves E3 deferred from PR #7 against MethodSCRIPT v1.6 manual §14.40/14.41/14.44: **ocp** had `meas_loop_ocp p c <run> <interval>` (extra `c` var + swapped) → fixed to `meas_loop_ocp p <interval> <run>` with potential-only packet (no current at open circuit; runs on the Pico); **cp** had `meas_loop_cp p c <i_dc> <run> <interval>` (swapped) → fixed to `<i_dc> <interval> <run>` per §14.41 (interval before run, like CA family). CP still galvanostat-only so untestable on Pico — fix is correctness for any programmatic caller / future EmStat4. **fca** was an invalid command (`meas_loop_fca` does not exist; FCA is EmStat4+ only) → now raises a clear `ValueError`. All three are GUI-hidden (not in `_VERIFIED_TECHNIQUES`).

  - **PR #9 — PSTrace method-string fidelity for CV/SWV/EIS (`fix/pstrace-method-fidelity` → `3d6b5e6`, 17:21 UTC, 204+/18- across 4 files).** Direct extension of `591d880`'s CA fix after reviewing native PSTrace `.pssession` references for CV/SWV/EIS. Two classes of mismatch making PSTrace ignore our params and fall back to template defaults:
    - **TECHNIQUE= enum was shifted** — `lsv=2, dpv=3, swv=4` had wrong offset, so an SWV exported as `TECHNIQUE=4` (= ACV per Table 5). Corrected to `lsv=0, dpv=1, swv=2, npv=3, acv=4` (cv=5, ca=7, eis=14 were already right; confirmed against native CV=5, SWV=2, EIS=14 references).
    - **Param key names** — universal `t_eq → T_EQUIL`; CV/FCV `e_vertex1/2 → E_VTX1/2`; SWV/ACV `amplitude/frequency → E_AMP/FREQ`; EIS `freq_start/end → MAX_FREQ/MIN_FREQ`, `e_dc → E`, `e_ac → AMPLITUDE`. Already-matching keys (`E_BEGIN`, `E_END`, `E_STEP`, `SCAN_RATE`, `N_SCANS`, `N_FREQ`, `T_RUN`, `T_INTERVAL`) untouched.
    - Schema gotcha: some PSTrace files use lowercase top-level JSON keys; authoritative method is `Measurements[0].Method`, not the sometimes-stale top-level `Method`. Captured in new `docs/references/pstrace_method_keys.md`. New `tests/data/test_pstrace_method_keys.py`.
    - **`5dc0908` chore: deactivate DPV** — PSTrace DPV metadata unverifiable against the references available, and DPV wasn't in `_VERIFIED_TECHNIQUES` anyway. Removed from generator path rather than ship suspect metadata. Re-enable when a real DPV .pssession reference is on hand.

  - **Memory updated:** none required. All knowledge captured in-repo (`docs/references/pstrace_amperometry_method.md` from PR #7 + `docs/references/pstrace_method_keys.md` from PR #9).
  - **Branch + repo hygiene:** local main fast-forwarded `a9f7992 → 3d6b5e6` (PRs #10, #8, #9 merges). All three feature branches still exist on origin (deletable post-merge). Local repo has only `main`.
  - Left off: main at `3d6b5e6`, working tree clean. No open PRs. Tests 69+ passing across all PR branches at merge time. Two open Notion tasks still parked: CMU.17.034 (MUX-16 script equivalents, Low priority) and CMU.17.035 (error out on disconnected RE/CE).
  - Next: (1) Address CMU.17.035 (disconnected RE/CE detection) when there's a natural seam — relates to electrode-config modes. (2) Re-enable DPV in PSTrace export when a native DPV .pssession reference is available for metadata-key reverse-engineering (PR #9 leftover). (3) Enclosure-gated bench items from PR #6 (Mode A/B 1 MΩ resistor regression + Mode C BA1613 IDE EIS) still gated on Karl's enclosure build.

### 2026-05-25: PR #6 merged — Electrode Config Modes A/B/C + UX fixes (main at `7c3024f`)
- [x | Session | 2026-05-25] PR #6 (`feature/electrode-config-modes`) hardware-verified on the PalmSens test cell, merged to `main` as `7c3024f`. Branch deleted local + remote. `main` is the only branch. 63/63 tests pass.
  - **Hardware verification (this session):** Ran the pre-enclosure smoke protocol (`docs/pre_enclosure_smoke.md`) on the PalmSens MUX16 test cell with EP2RP5OI / espico1601. Mode A → CH15 alone ohmic at ~123 µA/V; Mode B → CH16 alone ohmic at ~115 µA/V. Confirms firmware accepts `bits[7:4]=14`/`15` and MUX silicon physically routes RE+CE to the GUI-selected position. Mode C (BA1613 IDE bench) still pending — gated on enclosure build per the PR test plan.
  - **5 ancillary UX fixes landed in the same PR (separate commits in the squash history):**
    - `0a6f3c6` `fix: clear export buffer on measurement start` — Export menu was silently writing the prior run's `MeasurementResult` if invoked mid-run. `_on_measurement_started` now nulls `_last_result` and disables the Export action; re-enables on completion or partial-data abort path.
    - `33b1a93` `fix: block accidental wheel-scroll selection in combo/spin boxes` — App-level `QObject` event filter swallows `QWheelEvent` on `QComboBox` / `QAbstractSpinBox`. Discovered when a scroll over the preset combobox cycled through every preset mid-experiment (visible as rapid-fire "Loaded preset: …" log entries).
    - `987fdc6` `feat: Delete Preset button in TechniquePanel` + `6f8f019` `ui: collapse Save/Delete preset buttons to icons` — New Delete button with modal Yes/Cancel confirmation; icon-only `SP_DialogSaveButton` + `SP_TrashIcon` to keep the Settings dock narrow. `PresetManager.is_builtin()` added so the panel disables Delete on undeletable selections without trial-and-error. 6 new tests in `tests/data/test_presets_delete.py`.
    - `1a2f4f0` `fix: remove no_sensing from _BUILTIN_PRESETS so it can be deleted` — The hardcoded built-in mechanism was re-injecting `no_sensing` on every GUI load, defeating the user's prior JSON deletions. `_BUILTIN_PRESETS` is now empty by design; all 10 shipped presets live in `presets/presets.json` and are fully user-managed via the new Delete button. Mechanism preserved (still referenced by `is_builtin` and `delete_preset`) for future use; a monkeypatch test injects a synthetic built-in to keep the deletion-refusal path covered.
  - **Merge conflict resolution (`3c831af`):** `presets.json` conflicted between `main`'s `f4dd112` ("updated presets" — Karl's `chu_2023_mpd` param tweaks: n_scans=5, cr=32u, bw_hz=400, channels=1-16; plus `ca_16ch_stirred_optimal` `t_run` 600→5000s) and the PR branch's `959edc7` ("uppdated presets" — Phase 8 `electrode_config_mode` fields on all presets + SWV deletion via the new GUI Delete button). Git auto-merged the param tweaks. The only manual resolution was choosing the validated long-form descriptions on `no_sensing` and `ca_16ch_stirred_optimal` (branch side) over the GUI Save-Preset defaults ("User preset: NAME") that main had overwritten.
  - **Notion updated:** Appended session log to CMU.17.010 (Hardware Validation page) — same pattern as the existing 2026-03-23 and 2026-05-12 entries. Page: <https://www.notion.so/3235fc7c379a80339420f59dee92e03e>.
  - **Memory updated:** `project_pr6_electrode_modes_verified.md` (status updated to merged on 2026-05-25 after push); `MEMORY.md` index.
  - **Branch + worktree hygiene:** During the verification session a temporary worktree was used at `.claude/worktrees/verify-pr6-electrode-modes` then cleaned up; local `verify-pr6-electrode-modes` and stub `worktree-verify-pr6-electrode-modes` branches both deleted. Only `main` remains.
  - Left off: `main` at `7c3024f`, no open PRs, working tree clean. Two open Notion tasks remain in the project To Do board: CMU.17.034 (MUX-16 script equivalents, Low priority) and CMU.17.035 (error out on disconnected RE/CE). Neither is gated by PR #6.
  - Next: (1) Enclosure mechanical build (Karl's hardware work). (2) Post-enclosure Mode A/B 1 MΩ resistor regression + Mode C BA1613 IDE bench EIS sweep per the PR's "Test plan" section — these are the remaining checkboxes from PR #6. (3) Address CMU.17.035 (disconnected RE/CE detection) when there's a natural seam to add the check — relates to the new electrode-config modes.

### 2026-05-23: Session signoff — Electrode config polish, follow-ups, PR #6 opened, pre-enclosure smoke documented
- [x | Session | 2026-05-23] Post-tournament iteration on `feature/electrode-config-modes` — GUI polish, two reviewer-flagged follow-ups grafted in, PR opened against `main`, and a bench-ready pre-enclosure smoke protocol documented. Branch now at `0c78941` with 10 commits ahead of main and 58 tests passing.
  - **GUI polish (4 commits, all driven by Karl's UX feedback):** (1) Dropped `-> CH15` / `-> CH16` / `-> CH1-CH14` channel-number suffixes from the radio button labels — too implementation-y for end users; the channel numbers remain in tooltips for debugging context (`ca3dd15`). (2) Tabified the Settings and Log docks so the log isn't perpetually visible — Settings raised by default, user clicks Log tab when a measurement runs (`1c5b035`). (3) Moved the dock tab bar from default-bottom to top + wrapped Settings dock contents in `QScrollArea` — fixes Mode C's 14-row ManualChannelPanel overflowing into the tab bar (`657b860`). (4) Removed the inner `QScrollArea` on `TechniquePanel`'s param grid — redundant once the outer Settings dock scrolls; double-scroll was ugly (`60fca3c`).
  - **Follow-ups grafted from Coder 2 (loser of Batch 2):** (1) Per-Curve / per-EISData / session-level `.pssession` electrode-config metadata — `MUXChannel` + `ReCeChannel` + `ElectrodeConfigMode` on every curve and EISData entry, plus top-level `ElectrodeConfigMode` + `ReCeChannels` on the session dict. Cribbed Coder 2's diff via `git show 3ecb51d` (orphan commit still in object store). 4 new tests; suite 58/58 (`9d4e6b3`). (2) Behavior-driven button-click tests for `ManualChannelPanel` — `_apply_same_btn.click()` and `_apply_ch13_btn.click()` replace the prior direct private-slot calls; added one new test for `Apply CH1 to all` button (previously had no coverage). Negative test for out-of-range CH15 kept as a direct call since no button maps to it (`84b2e8d`).
  - **Pre-enclosure smoke documented:** New `docs/pre_enclosure_smoke.md` (`0c78941`). Three-run protocol using the EmStat Pico MUX16 test cell shipped in the kit (16 independent 3-electrode circuits, R_1 = 1 kΩ to R_16 = 8.66 kΩ). Each mode gets a deterministic pass criterion read off the live plot: Run 1 (Mode A) expects only CH15 ohmic at ~123 µA/V; Run 2 (Mode B) expects only CH16 at ~115 µA/V; Run 3 (Mode C "Apply same-position", CH1-CH14) expects all 14 channels ohmic matching Figure 5 of the PalmSens guide minus the reserved channels. Doc explains why one-channel-ohmic IS the validation (proves `bits[7:4]` is electrically active), what's NOT covered (the 16-WE shared-RE+CE workflow needs the enclosure or a breadboard cheat), and includes the CON1/2/3 vs CON2/3/4 connector-naming caveat.
  - **PR opened:** https://github.com/batesk52/CMU.49.013-EmStat-Pico-MUX16/pull/6 — title "Electrode configuration modes A/B/C + enclosure abstraction." Body summarizes the design, files, tournament process (with link to plan), and checked vs unchecked test plan items (code gates checked, hardware bench validation unchecked pending enclosure).
  - **Reviewer follow-ups still parked (2 of original 4 remain):** (a) `_suspend_signals` flag pattern (cleaner than `blockSignals()` dance for batched updates in `ManualChannelPanel`) and (b) `re_ce_channels=[]` + let `TechniqueConfig.__post_init__` auto-fill in `main_window._on_start_measurement` (eliminate GUI-side defaulting mirror). Both small; tackle as separate PRs if/when convenient. The two important ones (per-Curve metadata + behavior-driven tests) landed this session.
  - **Operational notes on /code-team this session:** Tournament hygiene worked end-to-end including the Batch 2 Case 3 wrong-repo rescue (both coders self-rescued to `<sub-repo>/.claude/worktrees/agent-*-rescue/`). The orchestrator's parent-prune ran cleanly post-merge. Confirms the WS-18 staged-write tournament pattern is reliable for sub-repo work where the orchestrator runs in the parent project. No silent failures, no isolation violations either batch.
  - Left off: PR #6 open with 10 commits at `0c78941`. Working tree clean across all repos. Hardware smoke is documented and ready (~5 min bench time using the EmStat Pico MUX16 test cell — no enclosure needed); not yet run. Enclosure mechanical work and TSC.17.003 connector arrival are the gates for the full Phase 2 bench validation in the plan.
  - Next: (1) Run `docs/pre_enclosure_smoke.md` 3-run protocol at the bench — should take ~5 min, validates firmware + routing for all three modes without the enclosure. (2) If smoke passes, the PR is ready to merge from a confidence standpoint (hardware-validation-on-enclosure can land as a follow-up TRA). (3) Karl reviews the PR. (4) Build the enclosure (mechanical). (5) Re-run Mode A/B with proper shared external RE+CE post-enclosure. The two remaining reviewer follow-ups (`_suspend_signals` + GUI defaulting cleanup) are nice-to-haves whenever convenient.

### 2026-05-23: Phase 8 — Electrode Configuration Modes A/B/C + Enclosure Abstraction (code-team tournament, 2 batches, on `feature/electrode-config-modes`)
- [x | Code-Team | 2026-05-23] Two-batch tournament implementation of the high-level Electrode Configuration selector (Modes A/B/C) per approved plan `flickering-moseying-shannon.md`. Modes A (Separate external, RE/CE fixed CH15) and B (On-board combined, RE/CE fixed CH16) support the full 16-WE workflow. Mode C (Manual per-WE pairing) restricts both WE and RE/CE to CH1–CH14, with CH15/CH16 reserved as enclosure infrastructure positions. Enclosure design captured in new `docs/enclosure_design.md`.
  - **Batch 1 — core plumbing + tests (winner: coder-1, commit `797fdb3`):** `MuxController.channel_address(channel, re_ce_channel=1)` now encodes `(re_ce_channel-1) << 4 | (channel-1)`. `scan_channels_script*(channels, re_ce_channels=None)` routes through compact-loop path when RE/CE is constant, sequential fallback when varying. `TechniqueConfig` + `MeasurementResult` gained `re_ce_channels: list[int]` + `electrode_config_mode: str` fields with per-mode validation in `__post_init__`. Module constants `EXTERNAL_RE_CE_CHANNEL=15`, `ON_BOARD_RE_CE_CHANNEL=16`, `MODE_C_MAX_CHANNEL=14` exported from `src/data/models.py`. `generate()` accepts `re_ce_channels` kwarg; `measurement_engine.py` populates the new fields on `MeasurementResult`. Tests: 14 new (7 in `tests/comms/test_mux.py`, 7 in `tests/data/test_models.py`) + 2 RE/CE bit-propagation regressions in `tests/techniques/test_scripts.py`. Suite: 31/31. Selection rationale: explicit `re_ce_channels` kwarg on `generate()` vs Coder 2's `params['re_ce_channels']` smuggling (category error); Coder 1 also threaded values through `measurement_engine.py` so `MeasurementResult` fields actually get populated end-to-end.
  - **Batch 2 — GUI + exports + presets + docs (winner: coder-1, commit `4fc8ca8`):** New `ElectrodeConfigPanel(QGroupBox)` with 3-radio mode selector (signal `mode_changed(str)`) and new `ManualChannelPanel(QGroupBox)` 14-row WE/RE-CE pairing table with 3 bulk-set buttons (same-position, Apply CH1, Apply CH13). `main_window.py` wires both panels into the left dock between Technique and Channels, swaps `ChannelPanel ↔ ManualChannelPanel` visibility on mode change, populates `TechniqueConfig` from the active panel in `_on_start_measurement()` (wraps in try/except → `QMessageBox.warning` for invariant violations; empty-selection guard). `src/data/exporters.py` emits `# Electrode config:` + `# RE/CE channel:` after `# Parameters:` in CSV header. `src/data/pssession_exporter.py` adds `ELECTRODE_CONFIG_MODE` + `RE_CE_CHANNELS=WE:RE_CE,...` to the .pssession METHOD string. `Preset` dataclass + all 10 entries in `presets/presets.json` gained `electrode_config_mode` (default `"external"`) + `re_ce_channels` (default `[]`); `PresetManager._load` filters JSON to `Preset.__dataclass_fields__` for backward + forward compat. New `docs/enclosure_design.md` documents channel assignments, RE/CE pin semantics, the Y-split warning, and NanoSPR BA1613 3-electrode + 2-electrode wiring recipes. Tests: 22 new across 4 files (exporters, presets, GUI panels). Suite: 53/53. Selection rationale: Coder 1 stayed on plan scope (.pssession at one level per spec) vs Coder 2's 3-level plumbing into 2 extra files (`pssession_curves.py`, `pssession_eis.py`); Coder 1's introspection-based preset backward compat (`Preset.__dataclass_fields__`) is self-maintaining vs Coder 2's hardcoded field set (drift risk).
  - **Tournament hygiene:** Both batches ran with 2 coders in parallel background worktrees with `mode="bypassPermissions"` + `run_in_background=true`. Batch 1: clean Case 1 isolation for both coders. Batch 2: **Case 3 fired for both coders** — harness placed worktrees in the parent project (root `_all_work`) instead of the sub-repo; both self-rescued to `<sub-repo>/.claude/worktrees/agent-<id>-rescue/` via the Step 0 protocol. Post-batch parent prune ran cleanly. No feature-branch leakage either batch.
  - **Reviewer follow-ups for the winner (no CRITICAL):** (a) tests call private slots directly (`panel._on_apply_same_position()`); behavior-driven `.click()` style would catch signal-wiring bugs. (b) Coder 2's `_suspend_signals` flag is cleaner than per-widget `blockSignals()` dance for batched updates. (c) Coder 2's per-Curve `MUXChannel`/`ReCeChannel` metadata in `pssession_curves.py`/`pssession_eis.py` is a legitimate forward feature (PSTrace would surface wiring per plot) — park as follow-up. (d) Coder 2's `re_ce_channels=[]` + let `TechniqueConfig.__post_init__` auto-fill is cleaner than GUI-side mirror of defaulting logic. All four worth small follow-up PRs; none gate hardware validation.
  - **Files changed (cumulative, both batches merged into `feature/electrode-config-modes`):** `src/comms/mux.py`, `src/data/models.py`, `src/engine/measurement_engine.py`, `src/techniques/scripts.py`, `src/gui/controls.py`, `src/gui/main_window.py`, `src/data/exporters.py`, `src/data/pssession_exporter.py`, `src/data/presets.py`, `presets/presets.json`, `docs/enclosure_design.md` (NEW), `tests/comms/test_mux.py` (NEW), `tests/data/test_models.py` (NEW), `tests/data/test_exporters_electrode_config.py` (NEW), `tests/data/test_presets_electrode_config.py` (NEW), `tests/gui/__init__.py` (NEW), `tests/gui/test_electrode_config_panels.py` (NEW), `tests/techniques/test_scripts.py` (extended).
  - Left off: `feature/electrode-config-modes` at `4fc8ca8`. Local-only — NOT pushed to origin yet. PR not yet opened against `main`. Hardware validation (Phase 2 of plan's Verification — dummy-load smoke test in Mode A + Mode B regression + Mode C BA1613 bench) blocked on enclosure build (Karl's mechanical work) and connector arrival for TSC.17.003.
  - Next: (1) Karl reviews the merged work on the feature branch; (2) push branch to origin + open PR against `main` once Karl is ready; (3) build the enclosure (Karl, mechanical); (4) bench validation per the plan's Verification Phase 2; (5) follow-up PRs for the four reviewer notes above as time permits.

### 2026-05-20: Session Signoff — EmStat Pico has no galvanostat hardware (CMU.17.032 cancelled)
- [x | Session | 2026-05-20] CP / chronopotentiometry confirmed unsupported on EmStat Pico; CeOx deposition for CMU.87.052 pivots to PalmSens + MUX8-R2.
  - **Finding (other-agent bench, this main session):** MethodSCRIPT v1.6 manual p81 Table 14.41 marks `meas_loop_cp: EmStat Pico: N`; p40 Table 9 marks `set_i: EmStat Pico: N`; §12.6 PGStat mode 6 (galvanostatic) required but Pico has no mode 6. Bench-confirmed via `e!4001 unknown command` on `meas_loop_cp p c -35.4u 100m 10`. Hardware limitation, not code.
  - **Codebase cleanup landed on main (commit `f414339`):** removed `ceox_deposit_single` + four other unused presets (NO_sensing, Test, NO_test, two 16ch CeOx variants); reverted CP-related code in scripts.py / mux.py / test_scripts.py back to pre-session state; `cp` not added to `_VERIFIED_TECHNIQUES` (was temporarily added during diagnosis, then taken back out). Net effect: GUI still doesn't expose CP (never did), 5 dead presets gone, no dormant code introduced. Repo strictly better than before.
  - **Incidental bugs found while diagnosing (kept fixed, dormant on Pico):** `_gen_cp` had `t_run` / `t_interval` args swapped vs. the manual's signature `<i_dc> <t_interval> <t_run>` — regression test added. `scan_channels_script_with_body` lacked `cell_off`/`cell_on` wrapping around MUX switches (unsafe in galvanostatic mode on devices that *do* support it) — `galvanostatic` flag added in `src/comms/mux.py` emits `cell_off → set_gpio → wait 100m → cell_on → wait 50m → body` for CP/GEIS only; CV/CA/EIS paths byte-identical. `_preamble_galvano` selects PGStat mode 3 (should be mode 6) — left unfixed since whole code path is dormant on this hardware; anyone resurrecting CP for an EmStat4 or Nexus should fix this before validating.
  - **Branch hygiene:** `feature/cp-hw-verify` (and earlier `feature/cp-alt-mux` rename) deleted local + remote. Only `main` exists, in sync at `f414339`.
  - **Downstream cleanup landed in companion repos:**
    - `_tasks/registry.yaml` — CMU.17.032 status: Cancelled, completed_date: 2026-05-20, full evidence chain in summary (commit `eb0b94a`). Notion page `3665fc7c-379a-81c8-8ba4-d38abf13026d` Status=Cancelled with detailed body.
    - `_experiments/CMU.87.052.md` — TRR Phase 1 rewritten to specify PalmSens + MUX8-R2; absolute current target `-35.4 µA` for 1.5 mm Ø pad confirmed via Jingxun's `0331_d6.pssession`; RE corrected from 3 M → 1 M KCl per SOP open-items log; sequential per-MUX-channel sequencing language tightened (commit `906c5bf`, later `b4a5ddb`).
    - Root `_all_work` — new artifacts: pivot slide at `exports/presentations/CMU.87.052_pivot_2026-05-20/`, bath-stability literature report at `exports/searches/2026-05-20_ceox-bath-stability.md`, slide-generator script at `claude_test_files/gen_pico_pivot_slide.py`.
  - **Auto-memory saved (user-level):** `reference_palmsens_galvanostat_hardware.md` — cross-conversation note so future sessions don't propose the Pico for galvanostatic work.
  - **Zotero infrastructure repaired:** `_codebases/KB4.45.009-Admin-Tools/.env` created (gitignored) with `ZOTERO_USER_ID=13882935`, `ZOTERO_API_KEY` (Karl to regenerate when convenient since it transited the chat), `ZOTERO_STORAGE_ROOT=D:\OneDrive\Apps\Zotero\Main`. `/zotero-ask` verified working — auth + library + PDF path resolution all functional. Cerium-specific queries returned 0 hits (the SOP literature isn't in Karl's library yet); web fallback was used for the bath-stability report.
  - **Bench day planning:** compressed-scope 1-day execution mapped out for CMU.87.052 (deposition + bleach + CA test, no Pt-black reference) — feasible by ~lunch with proper night-before pre-stage. PSTrace methods documented (CP @ -35.4 µA × 300 s per channel sequential; CA @ +0.3 V with MUX-walk across 3 channels, H₂O₂ spikes at 0.1/1/10/100 µM via 100 µL injections from pre-made 10 µM / 100 µM / 1 mM / 10 mM intermediate stocks).
  - Left off: All four repos clean, all session work pushed. CMU.87.052 awaiting bench day (Karl chooses when, targeted before TRA date 2026-05-24).
  - Next: Bench day for CMU.87.052 — build PSTrace methods, run Phase 1 + post-treatment + CV QC + Phase 2 CA spike series, log to `runs:` in CMU.87.052.md via `/electrochem publish`. Optional: import the 5 SOP CeOx references into Zotero so future `/zotero-ask` queries can cite from library.

### 2026-05-12: Session Signoff — Hardware Validation Sweep (CMU.17.019 + CMU.17.022)
- [x | Session | 2026-05-12] Both open PRs hardware-validated and merged. Two CMU tickets closed.
  - **CMU.17.019 (CA round-robin cadence, PR #4)** — 3 runs × 16 channels in 0.1 M KCl (cr=2u, 2u, 100n; t_run=30s, t_interval=2s, settle_time=50ms). All 16 channels return data on every round. Round interval 2.012 ± 0.007 s (spec: 2.0 ± 0.1 s). Per-channel switching delta 60.8–61.6 ms (theory: 60 ms). 16-ch burst spread 913–921 ms (theory: 960 ms = 16 × 60ms). Best-case noise floor 0.85 nA std dev at cr=100n. **PASS.** Applied conflict-resolution recipe to `scripts.py` (replaced `_CA_ALT_MUX_PER_CHANNEL_BURST_S` magic number with `(settle_s + 0.010)` formula) so user-configurable settle_time is honored. PR #4 squash-merged to main as commit `12f0d9b`. Exports: `20260511_184350`, `184550`, `184642` `_ca_alt_mux/`. Notion TRA pushed to `34c5fc7c-379a-81dc-a508-c88b00a2279b`, Status=Done.
  - **CMU.17.022 (mode-2 BW sweep, PR #5)** — Rebased `feature/bw-sweep-mode2` onto new main (clean rebase, 15/15 tests pass), force-pushed. BW sweep run on 16 channels in 1 mM ferri / 0.1 M KCl (e_dc=0.7V matched to H₂O₂ sensing conditions, not the 0.2V spec — practical deviation noted in TRA). Three BW values: 4 Hz (172 rounds, 6 min, 88 nA detrended std dev), 0.4 Hz (50 rounds, 2 min, **5.4 nA detrended** — ~10× below PSTrace bench), 40 Hz (16 rounds aborted, one channel pegged 1.2 µA — mode-2 instability). 400 Hz baseline not run this session. **Operational verdict: 0.4 Hz is optimal.** Drift dominated raw std dev — Pt-oxide transient at +0.7V on Pt-black — detrended values isolated intrinsic BW noise. `set_max_bandwidth 400m` (0.4 Hz) confirmed valid on firmware v1.6 (prior risk resolved). PR #5 squash-merged to main as commit `bc2854f`. Exports: `20260511_193535_ca_alt_mux` (4 Hz), `193801_ca_alt_mux` (0.4 Hz), `193849_ca_alt_mux` (40 Hz). Notion TRA pushed to `35d5fc7c-379a-81d2-8ee4-c682fd8c3d16`, Status=Done.
  - Both feature branches deleted, stale refs pruned, local on main at `bc2854f`. `gh` CLI authenticated this session (persists across reboots).
  - Left off: Both hardware-validation tickets closed. CMU.17.010 session log appended pointing to 17.019/17.022. Project state: main at `bc2854f`, no open PRs, clean.
  - Next: Optional follow-ups in CMU.17.022 TRA — (a) change default `bw_hz` from 400→0.4 in `no_sensing` preset (behavior-changing, separate PR), (b) 400 Hz baseline run for full noise-vs-BW curve, (c) H₂O₂ calibration at 0.4 Hz on Pt-black (the original goal — should now show clean nA-scale signals).

### 2026-05-11: Session Signoff
- [x | Session | 2026-05-11] Phase 7 BW sweep — implementation, task, PR all shipped
  - Completed: Plan written (`~/.claude/plans/what-is-multi-burst-snug-scroll.md`) and Phase 7 blueprint added to README. Code-team tournament (2 coders) implemented `bw_hz` parameterization across scripts.py / controls.py / presets.json / test_scripts.py + 10 new regression tests (full suite 15/15 green). Coder-1 selected for `_BUILTIN_PRESETS` sync. CMU.17.022 minted in `_tasks/registry.yaml` and pushed to Notion (page `35d5fc7c-379a-81d2-8ee4-c682fd8c3d16`) with full TRR. Branch `feature/bw-sweep-mode2` pushed to origin; PR #5 opened against main (https://github.com/batesk52/CMU.49.013-EmStat-Pico-MUX16/pull/5).
  - Left off: PR #5 OPEN, code-level validation complete (preamble gates pass, pytest 15/15 green, offscreen Qt smoke OK); hardware validation NOT YET run. PR #4 (`claude/fix-mux-chronoamperometry-VWZ2p`, burst-pacing fix) also OPEN and unvalidated on hardware. Both PRs cut from independent branches; combined sweep is a follow-up after both merge.
  - Next: Lab session today — validate both PRs on real EmStat Pico MUX16. For PR #5: launch GUI, confirm "Max Bandwidth" dropdown defaults to 400 Hz with all 7 values, run 4-ch ferricyanide CA at BW ∈ {400, 40, 4, 0.4} Hz per CMU.17.022 TRR, log std dev per channel per BW, find mode-2 stability boundary, verify `set_max_bandwidth 400m` is accepted by firmware v1.6. Document in CMU.17.022 TRA + append rows to `docs/multiplexer_limitations_and_lessons.md`.

### 2026-05-11: Phase 7 — Mode-2 Bandwidth Sweep Implementation (Tournament, 2 coders)
- [x | Agent | 2026-05-11] src/techniques/scripts.py — Added `bw_hz: 400` to `_DEFAULTS` for 14 mode-2 techniques (ca, ca_alt_mux, cv, lsv, dpv, swv, npv, acv, fca, pad, lsp, fcv, cp, ocp); parameterized `_preamble()` to emit `set_max_bandwidth {_format_si(params.get("bw_hz", 400))}`. `_preamble_eis()` and `_preamble_galvano()` left hardcoded at 200k (mode-3 stability lock).
- [x | Agent | 2026-05-11] src/gui/controls.py — Added `bw_hz` to `_PARAM_LABELS` as `("Max Bandwidth", "Hz")`; `_create_param_widget()` renders `bw_hz` as `QComboBox` over `[0.4, 4, 40, 400, 4000, 40000, 200000]` Hz; combobox stores numeric Hz via `setItemData` so `get_params()` recovers float/int via `currentData()`.
- [x | Agent | 2026-05-11] presets/presets.json + src/data/presets.py — Added `bw_hz: 400` to built-in `no_sensing.params` in both the JSON and the `_BUILTIN_PRESETS` Python fallback (keeps in-memory + on-disk preset in sync).
- [x | Agent | 2026-05-11] tests/techniques/test_scripts.py — NEW. 10 regression tests: parametrized bw_hz ∈ [0.4, 4, 40, 400, 4000, 40000, 200000] with SI-prefix expected outputs + default-400 + EIS/galvano 200k locks. Suite 15/15 green.
- Tournament: 2 coders, near-identical implementations, both passed 4/4 validation. Coder-1 selected — deciding factor was sync of `_BUILTIN_PRESETS` in presets.py (coder-2 only edited the JSON, leaving the in-memory fallback drifting).
- Branch: `feature/bw-sweep-mode2` (cut from main, independent of PR #4)
- Left off: Implementation merged to feature branch (fb48c53). Worktrees cleaned. Branch unpushed.
- Next: Create CMU.17.022 milestone via `/task`, then run the 4-point BW sweep (400/40/4/0.4 Hz) on hardware per Phase 7 blueprint.

### 2026-04-30: Session Signoff
- [x | Session | 2026-04-30 22:53] Synced remote branches, fetched open PR #4 ca_alt_mux fix
  - Completed: Fast-forwarded main 4427b95→ee53830 (5 commits the lab pushed remotely: MUX diagnostics + settle_time control, NaN/overload sentinel parser fix, preset updates, EIS preamble hardening, multiplexer_limitations doc). Created local branch `claude/fix-mux-chronoamperometry-VWZ2p` tracking origin (PR #4, single commit 7b871b3 by remote Claude on 2026-04-24).
  - Left off: Currently checked out on `claude/fix-mux-chronoamperometry-VWZ2p`. PR #4 is OPEN — fixes ca_alt_mux pacing so each channel emits a 10ms fast sample and the outer loop waits the remainder of t_interval (matches PSTrace/MUX8-R2 burst behavior).
  - Next: Review PR #4 diff in src/techniques/scripts.py, decide merge vs changes, hardware-validate new ca_alt_mux timing on real EmStat Pico MUX16.

### 2026-03-27: Session Signoff
- [x | Session | 2026-03-27] PsSessionExporter rewrite + SWV/DPV hardware validation — project complete
  - Completed: Rewrote PsSessionExporter for full PSTrace compatibility (22 fixes, 3 new files, PR #2 merged). Validated SWV and DPV on hardware — both passed on first attempt with no code changes (PR #3 merged). Updated Notion pages (CMU.17.010 criterion #6 → PASS, CMU.17.012 → Done, .pssession task → Done). 6 techniques now hardware-validated: CV, CA, CA_alt_mux, EIS, SWV, DPV.
  - Left off: All PRs merged to main (#1 eis_updates, #2 pssession_corrections, #3 dpv_swv_updates). Branches cleaned. Project functionally complete.
  - Next: Remaining untested techniques (LSV, NPV, ACV, FCV, FCA, CP, OCP, GEIS, LSP, PAD) if needed for specific experiments. Otherwise project is done.

### 2026-03-27: PsSessionExporter Rewrite for PSTrace Compatibility (feature/pssession_corrections, PR #2)
- [x | Agent | 2026-03-27] src/data/pssession_exporter.py — NEW. Main PsSessionExporter class with UTF-16 BOM encoding, minified JSON, trailing BOM, .NET DateTime ticks timestamps, unit constants (MicroAmpere, Volt, Time, MicroCoulomb), default Appearance/Hash helpers, method string builder with TECHNIQUE numbers
- [x | Agent | 2026-03-27] src/data/pssession_curves.py — NEW. CV curves (1 per scan per channel), CA curves (1 per channel, zero-based time), full Curve structure (Appearance, Hash, Type, MeasType, XAxis/YAxis=int 0, CorrosionButlerVolmer/Tafel), DataSetCommon with time+potential+current+charge arrays, trapezoidal charge integration, current in MicroAmperes
- [x | Agent | 2026-03-27] src/data/pssession_eis.py — NEW. EISDataList with ImpedimetricMeasurement type (Curves=[]), 22-array DataSetEIS per channel (Frequency, ZRe, ZIm, Z, Phase, Y, YRe, YIm, Capacitance, etc.), computed admittance and capacitance from raw impedance, AppearanceFrequencySubScanCurves
- [x | Agent | 2026-03-27] src/data/exporters.py — Removed old PsSessionExporter (lines 248-624), added re-export from new module preserving import path
- [x | Agent | 2026-03-27] claude_test_files/validate_pssession.py — 69-check structural validation (all pass)
- [x | Agent | 2026-03-27] exports/exports_new/ — Re-exported real CV, EIS, CA data from lab session as .pssession files for PSTrace validation

### 2026-03-27: CMU.17.012 — SWV & DPV Hardware Validation (PASSED, feature/dpv_swv_updates, PR #3)
- [x | Karl | 2026-03-27] SWV single-channel — Device accepted `meas_loop_swv`, peak-shaped voltammogram at ~0.22V with ferricyanide, live plot correct
- [x | Karl | 2026-03-27] SWV multi-channel — CH1+CH4 multiplexed, per-channel data separation and plot colors correct
- [x | Karl | 2026-03-27] SWV data export — CSV columns correct (set_potential, current), .pssession opens in PSTrace
- [x | Karl | 2026-03-27] SWV PalmSens4 comparison — Results visually match PalmSens4 output (no mathematical comparison, eyeball pass)
- [x | Karl | 2026-03-27] DPV single-channel — Device accepted `meas_loop_dpv`, peak-shaped voltammogram correct
- [x | Karl | 2026-03-27] DPV multi-channel — CH1+CH4 multiplexed, per-channel separation correct
- [x | Karl | 2026-03-27] DPV data export — CSV and .pssession export correct
- [x | Karl | 2026-03-27] DPV PalmSens4 comparison — Results visually match PalmSens4 output
- **No code changes needed** — SWV and DPV worked on first attempt. Existing `_gen_swv()` and `_gen_dpv()` implementations were correct.

### 2026-03-26: Session Signoff
- [x | Session | 2026-03-26] CMU.17.011 completed, SWV validation planned
  - Completed: Marked CMU.17.011 (PalmSens4 vs MUX16 Comparison) Done in registry + Notion. Created CMU.17.012 (SWV Hardware Validation) in registry + Notion with 7-step protocol and anticipated bug table. Scheduled Apr 13-17.
  - Left off: SWV code exists but untested on hardware. Validation plan in action_log and Notion.
  - Next: Run SWV on real EmStat Pico MUX16 (CMU.17.012) — single-channel smoke test first, then multi-channel + PalmSens4 comparison

### 2026-03-26: EIS Fixes, t_eq, ca_alt_mux Redesign (feature/eis_updates)
- [x | Agent | 2026-03-26] src/techniques/scripts.py — Fixed EIS autoranging (locked min=max to prevent mid-sweep range switching that corrupted <80 Hz data); added t_eq equilibration time parameter to all techniques; redesigned ca_alt_mux as single self-looping MethodSCRIPT (eliminates e!4001/e!4004 timing races from continuous re-send)
- [x | Agent | 2026-03-26] src/engine/measurement_engine.py — Updated loops_expected for ca_alt_mux (n_rounds × n_channels), channel index wrap-around, global timestamps, removed ca_alt_mux from _CONTINUOUS_TECHNIQUES
- [x | Agent | 2026-03-26] src/comms/serial_connection.py — Hardened script loading: pre-abort (Z), 50ms post-'e' delay, 2ms inter-line delay, added wait_until_idle()
- [x | Agent | 2026-03-26] presets/presets.json — Added EIS_TCK_standard and CV_TCK_standard user presets

### 2026-03-26: EIS Visualization Enhancements (feature/eis_updates)
- [x | Agent | 2026-03-26] src/gui/plot_widget.py — Added CHANNEL_SYMBOLS (16 entries), _is_eis property, conditional EIS markers in _init_channel()
- [x | Agent | 2026-03-26] src/gui/bode_widget.py — NEW. BodePlotWidget with dual |Z| (log-log) and Phase (log-linear) subplots, linked X axes, per-channel markers
- [x | Agent | 2026-03-26] src/gui/eis_plot_container.py — NEW. EISPlotContainer with QComboBox Nyquist/Bode selector, QStackedWidget, data forwarding to both views
- [x | Agent | 2026-03-26] src/gui/main_window.py — Wired EISPlotContainer as central widget, routed technique/data/lifecycle signals through container

### 2026-03-25: Session Signoff
- [x | Session | 2026-03-25] Presentation generation for MUX16 validation update
  - Completed: Created 3-slide deck (CMU.49.013_MUX16_validation_2026-03-25.pptx) — title, CMU.17.010 validation results (8/9 passed, 15 bugs, 4 techniques), CMU.17.011 comparison plan with placeholder for tomorrow's data
  - Left off: Presentation exported to exports/presentations/, CMU.17.011 still Planned
  - Next: Run PalmSens4 vs MUX16 comparison (CMU.17.011) — ferricyanide CV+EIS on Naveen's device, 5% acceptance threshold

### 2026-03-24: Session Signoff
- [x | Session | 2026-03-24] Post-validation project state update + comparison task planning
  - Completed: Synced local repo with remote (branches flattened to main on lab PC), updated action_log/README/registry to reflect hardware-validated state, reviewed 15-bug session log from Notion, confirmed all local fixes superseded by remote, deleted stale phase branches, created CMU.17.011 (PalmSens4 vs MUX16 comparison — CV + EIS with ferricyanide, Tzahi's request)
  - Left off: Main branch clean at c4a9a6f, all project docs updated, CMU.17.010 Done, CMU.17.011 Planned for 2026-03-26
  - Next: Thursday lab session — run PalmSens4 vs MUX16 comparison (CMU.17.011), validate .pssession format against PSTrace

### 2026-03-23: CMU.17.010 — Hardware Validation (PASSED)
- [x | Karl | 2026-03-23] Hardware validation on real EmStat Pico MUX16
  - Device: EP2IC0QZ on COM5, firmware upgraded v1.3 → v1.6
  - 15 bugs found and fixed during testing (see Notion page for full list)
  - Key fixes: pck_add uses variable names not type codes, meas_loop_ca arg order, SI prefix zero formatting, store_var/add_var suffix requirements, EIS pgstat mode, thread-safe logging, serial buffer overflow
  - 4 techniques verified: CV, CA, CA MUX-alternating, EIS (single-ch + multi-ch + export)
  - 8/9 success criteria passed; .pssession export creates file but format needs PSTrace validation
  - Branches flattened to main (6 commits: 2cb90ab → c4a9a6f)
  - Remaining: da8000000 short field investigation, EIS Bode plot option, .pssession PSTrace validation

### 2026-03-19: Session Signoff
- [x | Session | 2026-03-19] Major protocol overhaul — audited against PalmSens official spec, fixed everything
  - Completed: Phase 6 completion fixes (save preset dialog, .pssession export), 2 critical bugfixes (channel_changed emit, EIS Nyquist vars), full protocol spec correction (13 wrong VAR_TYPES, pck_add codes, metadata parsing, scan markers), added 7 advanced EIS types (ce-ck)
  - Branch: phase5/operational-features (commit d7cdb52+)
  - Left off: All code aligned with official PalmSens MethodSCRIPT spec. Branch chain still not merged to main. MUX still uses raw GPIO (not mux_config/mux_set_channel API)
  - Next: Hardware testing with real EmStat Pico MUX16, merge branch chain to main, consider MUX API migration

### 2026-03-19: Protocol Spec Correction (Tournament, 3 coders)
- [x | Agent | 2026-03-19] src/comms/protocol.py — Replaced 13 wrong VAR_TYPES with official PalmSens mapping, added 'i' SI prefix, fixed metadata parsing, added scan markers
- [x | Agent | 2026-03-19] src/techniques/scripts.py — Fixed pck_add codes: amperometry (da+ba), potentiometry (ba+ab), EIS (cc+cd+ca+cb+dc)
- [x | Agent | 2026-03-19] src/gui/plot_widget.py + src/data/exporters.py — Cascaded variable name updates (potential, zreal, zimag, set_frequency)

### 2026-03-19: Bugfix Audit (Tournament, 3 coders)
- [x | Agent | 2026-03-19] src/engine/measurement_engine.py + src/comms/protocol.py — Fixed missing channel_changed emit on END_LOOP + parser channel_index reset
- [x | Agent | 2026-03-19] src/gui/plot_widget.py — Fixed EIS/GEIS Nyquist plot: uses impedance_real/impedance_imaginary vars, negates Z'' by variable name

### 2026-03-19: Session Signoff
- [x | Session | 2026-03-19] Phase 6 completion fixes — audited codebase, found 2 gaps, implemented via code-team tournament
  - Completed: Save Preset dialog now functional (signal + QInputDialog + PresetManager wiring), .pssession export wired into GUI export flow alongside CSV
  - Branch: phase5/operational-features (commit a6fa5a7)
  - Left off: All 9 requirements now fully implemented. Branch chain still not merged to main
  - Next: Manual testing with real EmStat Pico MUX16 hardware, merge branch chain (phase1→phase2→phase3→phase4→phase5) to main

### 2026-03-19: Phase 6 — Completion Fixes (Tournament, 3 coders)
- [x | Agent | 2026-03-19] src/gui/controls.py + src/gui/main_window.py — Save Preset dialog (Req 9). Added save_preset_requested signal, QInputDialog handler, PresetManager.add_preset() call, combo refresh
- [x | Agent | 2026-03-19] src/gui/main_window.py — Wired PsSessionExporter.export_pssession() in _do_export() alongside CSV export (Req 7)

### 2026-03-17: Session Signoff
- [x | Session | 2026-03-17 00:00] Phase 5 operational features implemented (auto-save + presets)
  - Completed: Digested DARPA IV&V email/SOPs, extracted MUX-16-relevant NO sensing protocol details, blueprinted and implemented 2 new features across 6 tasks (2 new files, 4 modified). Also extracted 3 SOPs from zip (NO, E-stim, E-rec — only NO uses MUX16)
  - Branch: phase5/operational-features (from phase4/data-export)
  - Left off: All code implemented and syntax-validated. "Save Preset..." button wired but needs dialog handler
  - Next: Test with real EmStat Pico MUX16 hardware, merge branch chain to main, implement save-preset dialog

### 2026-03-16: Phase 5 — Operational Features
- [x | Agent | 2026-03-16] src/data/models.py - Added AutoSaveConfig dataclass, extended TechniqueConfig
- [x | Agent | 2026-03-16] src/data/incremental_writer.py - NEW. IncrementalCSVWriter with per-loop flush, fsync, thread-safe finish
- [x | Agent | 2026-03-16] src/data/presets.py - NEW. PresetManager with built-in NO Sensing preset (CA_alt_mux, 0.85V, CH1-8)
- [x | Agent | 2026-03-16] src/engine/measurement_engine.py - Added auto-save hooks at END_LOOP, auto_save_completed signal
- [x | Agent | 2026-03-16] src/gui/controls.py - Added preset selector to TechniquePanel, auto-save toggle to MeasurementControlPanel
- [x | Agent | 2026-03-16] src/gui/main_window.py - Wired PresetManager, preset loading, auto-save config, auto_save_completed signal
- [x | Agent | 2026-03-16] Docs - Updated product_requirements (Req 8, 9), README (Phase 5), architecture (data flow, modules)

### 2026-03-15: Session Signoff
- [x | Session | 2026-03-15 22:00] Full implementation complete (10/10 tasks via code-team tournament)
  - Completed: All 4 phases — comms, measurement core, GUI, data export. 10 source files, ~4k lines
  - Branch chain: main → phase1/comms-foundation → phase2/measurement-core → phase3/gui-application → phase4/data-export
  - Left off: GUI launches successfully (`python src/gui/main_window.py`), added requirements.txt
  - Next: Troubleshoot with real EmStat Pico MUX16 hardware, merge branch chain back to main

### 2026-03-15: Phase 4 — Data Export (Tournament)
- [x | Agent-7301 | 2026-03-15 21:30] src/data/exporters.py - Complete. CSVExporter (per-channel, technique-aware columns, metadata headers), PsSessionExporter (UTF-16 JSON PalmSens format), make_export_dir()

### 2026-03-15: Phase 3 — GUI Application (Tournament)
- [x | Agent-6201 | 2026-03-15 20:00] src/gui/plot_widget.py - Complete. LivePlotWidget, 16-color palette, technique presets, EIS Nyquist negation, auto-range
- [x | Agent-6201 | 2026-03-15 20:00] src/gui/controls.py - Complete. ConnectionPanel, TechniquePanel, ChannelPanel, MeasurementControlPanel with state management
- [x | Agent-6202 | 2026-03-15 20:30] src/gui/main_window.py - Complete. Dock layout, signal wiring, menu/status bar, export prompt, entry point (651 lines)

### 2026-03-15: Phase 2 — Measurement Core (Tournament)
- [x | Agent-5103 | 2026-03-15 18:00] src/techniques/scripts.py - Complete. 15 techniques + 3 MUX-alt, SI prefix formatting, pck blocks, template-based generation
- [x | Agent-5103 | 2026-03-15 18:00] src/data/models.py - Complete. TechniqueConfig, DataPoint, MeasurementResult, ChannelData dataclasses
- [x | Agent-5104 | 2026-03-15 18:30] src/engine/measurement_engine.py - Complete. QThread, 5 Qt signals, PacketParser integration, abort/halt/resume, MeasurementResult buffering

### 2026-03-15: Phase 1 — Communication Foundation (Batch 1, Tournament)
- [x | Agent-4821 | 2026-03-15 16:30] src/comms/serial_connection.py - Complete. 230400 baud, XON/XOFF, thread-safe with Lock, echo stripping, abort/halt/resume
- [x | Agent-4821 | 2026-03-15 16:30] src/comms/protocol.py - Complete. Hex packet decoder, 28-bit SI conversion, var type mapping, loop markers, metadata parsing
- [x | Agent-4821 | 2026-03-15 16:30] src/comms/mux.py - Complete. 10-bit GPIO addressing, channel 1-16, config/select/scan scripts, meas_loop_for

### 2026-03-15: Project Initialization
- [x | Manager | 2026-03-15 00:00] Project structure created
  - Core 5 files initialized from codebase template
  - Product requirements defined (7 requirements)
  - Architecture designed (4-layer: comms → engine → data → GUI)
  - Implementation blueprint created (10 tasks across 4 phases)
  - Ready for `/code-agent` or `/code-team` implementation

---

## Blocked/Notes

[Empty - blockers and notes will be added as needed]

---

## Task Format Guide

**In Progress:**
`- [WIP:Agent-####|timestamp] component - description`

**Completed:**
`- [x|Agent-####|timestamp] component - Complete. Notes`

**Blocked:**
`- [BLOCKED] component - Reason for blockage`
