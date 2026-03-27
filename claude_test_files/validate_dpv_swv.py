"""Validation script for DPV and SWV script generation.

Verifies MethodSCRIPT generation, SI formatting, variable declarations,
multi-channel wrapping, and technique registration -- no hardware required.
"""

import os
import sys

# Ensure project root is on path
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from src.techniques.scripts import (
    _format_si,
    generate,
    supported_techniques,
    technique_params,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record a pass/fail check."""
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" -- {detail}"
        print(msg)


# -----------------------------------------------------------------------
# 1. Technique registration
# -----------------------------------------------------------------------
print("\n=== 1. Technique Registration ===")

techs = supported_techniques()
check("DPV in supported_techniques", "dpv" in techs, f"got {techs}")
check("SWV in supported_techniques", "swv" in techs, f"got {techs}")

dpv_defaults = technique_params("dpv")
swv_defaults = technique_params("swv")
check("DPV defaults not empty", len(dpv_defaults) > 0)
check("SWV defaults not empty", len(swv_defaults) > 0)

# DPV should have scan_rate (per MethodSCRIPT v1.6 spec)
check(
    "DPV defaults include scan_rate",
    "scan_rate" in dpv_defaults,
    f"keys: {list(dpv_defaults.keys())}",
)

# SWV should have amplitude and frequency
check(
    "SWV defaults include amplitude",
    "amplitude" in swv_defaults,
    f"keys: {list(swv_defaults.keys())}",
)
check(
    "SWV defaults include frequency",
    "frequency" in swv_defaults,
    f"keys: {list(swv_defaults.keys())}",
)


# -----------------------------------------------------------------------
# 2. SI formatting for DPV/SWV values
# -----------------------------------------------------------------------
print("\n=== 2. SI Formatting Spot Checks ===")

si_cases = [
    (-0.2, "-200m", "e_begin"),
    (0.6, "600m", "e_end"),
    (0.005, "5m", "e_step"),
    (0.05, "50m", "e_pulse / t_pulse"),
    (0.025, "25m", "amplitude"),
    (0.02, "20m", "scan_rate"),
    (25.0, "25", "frequency (unity prefix)"),
    (0.0, "0m", "zero value"),
]

for value, expected, label in si_cases:
    result = _format_si(value)
    check(
        f"_format_si({value}) = {expected!r} ({label})",
        result == expected,
        f"got {result!r}",
    )


# -----------------------------------------------------------------------
# 3. DPV script generation
# -----------------------------------------------------------------------
print("\n=== 3. DPV Script Generation ===")

dpv_params = {
    "e_begin": -0.2,
    "e_end": 0.6,
    "e_step": 0.005,
    "e_pulse": 0.05,
    "t_pulse": 0.05,
    "scan_rate": 0.02,
    "cr": "100u",
}

dpv_lines = generate("dpv", dpv_params, [1])
dpv_text = "\n".join(dpv_lines)

# Find the meas_loop_dpv line
dpv_loop = [l for l in dpv_lines if l.strip().startswith("meas_loop_dpv")]
check("DPV has meas_loop_dpv", len(dpv_loop) == 1, f"found {len(dpv_loop)}")

if dpv_loop:
    parts = dpv_loop[0].strip().split()
    # meas_loop_dpv p c e_begin e_end e_step e_pulse t_pulse scan_rate
    check(
        "DPV arg count = 9 (cmd + 8 args)",
        len(parts) == 9,
        f"got {len(parts)}: {parts}",
    )
    check("DPV arg[0] = meas_loop_dpv", parts[0] == "meas_loop_dpv")
    check("DPV arg[1] = p (potential var)", parts[1] == "p")
    check("DPV arg[2] = c (current var)", parts[2] == "c")
    check("DPV e_begin = -200m", parts[3] == "-200m", f"got {parts[3]}")
    check("DPV e_end = 600m", parts[4] == "600m", f"got {parts[4]}")
    check("DPV e_step = 5m", parts[5] == "5m", f"got {parts[5]}")
    check("DPV e_pulse = 50m", parts[6] == "50m", f"got {parts[6]}")
    check("DPV t_pulse = 50m", parts[7] == "50m", f"got {parts[7]}")
    check("DPV scan_rate = 20m", parts[8] == "20m", f"got {parts[8]}")

# Safety postamble
check("DPV has on_finished", "on_finished:" in dpv_text)
check("DPV has cell_off", "cell_off" in dpv_text)

# No empty lines (MethodSCRIPT constraint)
check(
    "DPV no empty lines",
    all(line != "" for line in dpv_lines),
    "found empty line in script",
)

# Packet config
check("DPV has pck_start", any("pck_start" in l for l in dpv_lines))
check("DPV has pck_add p", any("pck_add p" in l for l in dpv_lines))
check("DPV has pck_add c", any("pck_add c" in l for l in dpv_lines))
check("DPV has pck_end", any("pck_end" in l for l in dpv_lines))
check("DPV has endloop", any("endloop" in l for l in dpv_lines))

# Preamble
check("DPV has var p", "var p" in dpv_text)
check("DPV has var c", "var c" in dpv_text)
check("DPV has cell_on", "cell_on" in dpv_text)
check("DPV has set_pgstat_mode 2", "set_pgstat_mode 2" in dpv_text)


# -----------------------------------------------------------------------
# 4. SWV script generation
# -----------------------------------------------------------------------
print("\n=== 4. SWV Script Generation ===")

swv_params = {
    "e_begin": -0.2,
    "e_end": 0.6,
    "e_step": 0.005,
    "amplitude": 0.025,
    "frequency": 25.0,
    "cr": "100u",
}

swv_lines = generate("swv", swv_params, [1])
swv_text = "\n".join(swv_lines)

# Find the meas_loop_swv line
swv_loop = [l for l in swv_lines if l.strip().startswith("meas_loop_swv")]
check("SWV has meas_loop_swv", len(swv_loop) == 1, f"found {len(swv_loop)}")

if swv_loop:
    parts = swv_loop[0].strip().split()
    # meas_loop_swv p c f r e_begin e_end e_step amplitude frequency
    check(
        "SWV arg count = 10 (cmd + 4 vars + 5 params)",
        len(parts) == 10,
        f"got {len(parts)}: {parts}",
    )
    check("SWV arg[0] = meas_loop_swv", parts[0] == "meas_loop_swv")
    check("SWV arg[1] = p (potential var)", parts[1] == "p")
    check("SWV arg[2] = c (net current var)", parts[2] == "c")
    check("SWV arg[3] = f (forward current var)", parts[3] == "f")
    check("SWV arg[4] = r (reverse current var)", parts[4] == "r")
    check("SWV e_begin = -200m", parts[5] == "-200m", f"got {parts[5]}")
    check("SWV e_end = 600m", parts[6] == "600m", f"got {parts[6]}")
    check("SWV e_step = 5m", parts[7] == "5m", f"got {parts[7]}")
    check("SWV amplitude = 25m", parts[8] == "25m", f"got {parts[8]}")
    check("SWV frequency = 25", parts[9] == "25", f"got {parts[9]}")

# SWV must declare var f and var r
check("SWV has var f", "var f" in swv_text)
check("SWV has var r", "var r" in swv_text)
check("SWV has var p", "var p" in swv_text)
check("SWV has var c", "var c" in swv_text)

# Safety postamble
check("SWV has on_finished", "on_finished:" in swv_text)
check("SWV has cell_off", "cell_off" in swv_text)

# No empty lines
check(
    "SWV no empty lines",
    all(line != "" for line in swv_lines),
    "found empty line in script",
)

# Packet config (only p and c, not f and r)
pck_adds = [l.strip() for l in swv_lines if "pck_add" in l]
check(
    "SWV packet has exactly 2 pck_add (p and c only)",
    len(pck_adds) == 2,
    f"got {len(pck_adds)}: {pck_adds}",
)

check("SWV has cell_on", "cell_on" in swv_text)
check("SWV has set_pgstat_mode 2", "set_pgstat_mode 2" in swv_text)


# -----------------------------------------------------------------------
# 5. Multi-channel DPV
# -----------------------------------------------------------------------
print("\n=== 5. Multi-Channel DPV ===")

dpv_multi = generate("dpv", dpv_params, [1, 2, 3, 4])
dpv_multi_text = "\n".join(dpv_multi)

check(
    "DPV multi-ch has set_gpio",
    "set_gpio" in dpv_multi_text,
    "no MUX channel switching found",
)
check(
    "DPV multi-ch has meas_loop_dpv",
    "meas_loop_dpv" in dpv_multi_text,
)

# Consecutive channels should use compact loop pattern
check(
    "DPV multi-ch consecutive uses loop var i",
    "var i" in dpv_multi_text,
    "expected compact MUX loop for CH1-4",
)


# -----------------------------------------------------------------------
# 6. Multi-channel SWV
# -----------------------------------------------------------------------
print("\n=== 6. Multi-Channel SWV ===")

swv_multi = generate("swv", swv_params, [1, 2, 3, 4])
swv_multi_text = "\n".join(swv_multi)

check(
    "SWV multi-ch has set_gpio",
    "set_gpio" in swv_multi_text,
    "no MUX channel switching found",
)
check(
    "SWV multi-ch has meas_loop_swv",
    "meas_loop_swv" in swv_multi_text,
)
check(
    "SWV multi-ch has var f and var r",
    "var f" in swv_multi_text and "var r" in swv_multi_text,
)
check(
    "SWV multi-ch consecutive uses loop var i",
    "var i" in swv_multi_text,
    "expected compact MUX loop for CH1-4",
)


# -----------------------------------------------------------------------
# 7. Non-consecutive multi-channel
# -----------------------------------------------------------------------
print("\n=== 7. Non-Consecutive Multi-Channel ===")

swv_sparse = generate("swv", swv_params, [1, 5, 9])
swv_sparse_text = "\n".join(swv_sparse)

check(
    "SWV sparse channels has set_gpio",
    "set_gpio" in swv_sparse_text,
)
# Non-consecutive should NOT use loop var i
check(
    "SWV sparse channels does not use loop var i",
    "var i" not in swv_sparse_text,
    "non-consecutive should not use compact loop",
)


# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print(f"\n{'=' * 50}")
print(f"RESULTS: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
print(f"{'=' * 50}")

if FAIL > 0:
    print("\nSome checks FAILED. Review output above.")
    sys.exit(1)
else:
    print("\nAll checks PASSED.")
    sys.exit(0)
