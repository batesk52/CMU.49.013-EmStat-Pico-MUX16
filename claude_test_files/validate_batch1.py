"""Validation script for batch 1 phase 2 tasks."""

import sys
sys.path.insert(0, "/home/batesk52/_all_work/_codebases/CMU.49.013-EmStat-Pico-MUX16/.claude/worktrees/agent-ae7df7af")

from src.data.models import (
    TechniqueConfig, DataPoint, MeasurementResult, ChannelData
)
from src.techniques.scripts import (
    generate, supported_techniques, technique_params
)

# --- models.py validation ---
print("=== models.py ===")

# TechniqueConfig
tc = TechniqueConfig("CV", {"e_begin": -0.5}, [1, 2])
assert tc.technique == "cv", f"Expected lowercase, got {tc.technique!r}"
print(f"TechniqueConfig: {tc}")

# DataPoint
dp = DataPoint(timestamp=0.1, channel=1, variables={"current": 1e-6})
assert dp.get("current") == 1e-6
assert dp.get("missing", 0.0) == 0.0
print(f"DataPoint: {dp}")

# MeasurementResult
mr = MeasurementResult(technique="cv", channels=[1, 2])
mr.add_point(DataPoint(0.1, 1, {"current": 1e-6}))
mr.add_point(DataPoint(0.2, 2, {"current": 2e-6}))
mr.add_point(DataPoint(0.3, 1, {"current": 3e-6}))
assert mr.num_points == 3
assert mr.measured_channels == [1, 2]
print(f"MeasurementResult: {mr.num_points} points, channels={mr.measured_channels}")

# ChannelData
cd = mr.channel_data(1)
assert cd.num_points == 2
assert cd.values("current") == [1e-6, 3e-6]
assert cd.timestamps() == [0.1, 0.3]
print(f"ChannelData(ch=1): {cd.num_points} points")

print("models.py: ALL CHECKS PASSED\n")

# --- scripts.py validation ---
print("=== scripts.py ===")

# Check all techniques are registered
techs = supported_techniques()
expected = [
    "acv", "ca", "ca_alt_mux", "cp", "cp_alt_mux", "cv",
    "dpv", "eis", "fcv", "fca", "geis", "lsp", "lsv",
    "npv", "ocp", "ocp_alt_mux", "pad", "swv",
]
for t in expected:
    assert t in techs, f"Missing technique: {t}"
print(f"Supported techniques ({len(techs)}): {techs}")

# Validate technique_params returns defaults
cv_params = technique_params("cv")
assert "e_begin" in cv_params
assert "scan_rate" in cv_params
print(f"CV defaults: {cv_params}")

# Generate scripts for various techniques
for tech in techs:
    params = technique_params(tech)
    script = generate(tech, params, [1])
    # Every script must end with on_finished: / cell_off
    assert "on_finished:" in script, f"{tech}: missing on_finished"
    assert "  cell_off" in script, f"{tech}: missing cell_off"
    # No empty lines
    for line in script:
        assert line.strip(), f"{tech}: has empty line"
    print(f"  {tech}: {len(script)} lines, OK")

# Multi-channel CV test
multi = generate("cv", {"e_begin": -0.5, "e_vertex1": 0.5, "e_vertex2": -0.5}, [1, 3, 5])
assert "meas_loop_for" in " ".join(multi), "Multi-channel should use meas_loop_for"
assert "on_finished:" in multi
print(f"Multi-channel CV: {len(multi)} lines")

# SI prefix formatting sanity
from src.techniques.scripts import _format_si
assert _format_si(0.5) == "500m"
assert _format_si(0.001) == "1m"
assert _format_si(0.0) == "0 "
assert _format_si(-0.5) == "-500m"
assert _format_si(100000.0) == "100k"
print("SI prefix formatting: OK")

# Unsupported technique raises
try:
    generate("bogus", {}, [1])
    assert False, "Should have raised ValueError"
except ValueError:
    print("Unsupported technique error: OK")

# Empty channels raises
try:
    generate("cv", {}, [])
    assert False, "Should have raised ValueError"
except ValueError:
    print("Empty channels error: OK")

print("\nscripts.py: ALL CHECKS PASSED")
print("\n=== ALL VALIDATIONS PASSED ===")
