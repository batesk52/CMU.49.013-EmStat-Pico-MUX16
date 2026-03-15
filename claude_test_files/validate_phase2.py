"""Validation script for Phase 2 tasks: scripts.py and models.py"""

import sys
sys.path.insert(0, "/home/batesk52/_all_work/_codebases/CMU.49.013-EmStat-Pico-MUX16")

from src.techniques.scripts import generate, supported_techniques, technique_params
from src.data.models import TechniqueConfig, DataPoint, MeasurementResult, ChannelData
from datetime import datetime

print("=" * 60)
print("VALIDATING src/techniques/scripts.py")
print("=" * 60)

# Test supported_techniques
techniques = supported_techniques()
print(f"\nSupported techniques ({len(techniques)}): {techniques}")
assert len(techniques) == 18, f"Expected 18, got {len(techniques)}"

# Required techniques
required = [
    "lsv", "dpv", "swv", "npv", "acv", "cv", "ca", "fca",
    "cp", "ocp", "eis", "geis", "pad", "lsp", "fcv",
    "ca_alt_mux", "cp_alt_mux", "ocp_alt_mux",
]
for t in required:
    assert t in techniques, f"Missing technique: {t}"
print("All 18 required techniques present.")

# Test technique_params for each
for t in techniques:
    p = technique_params(t)
    assert isinstance(p, dict), f"Params for {t} is not a dict"
print("technique_params() works for all techniques.")

# Test generate for each technique (single channel)
for t in techniques:
    p = technique_params(t)
    script = generate(t, p, [1])
    assert isinstance(script, list), f"generate({t}) returned non-list"
    assert len(script) > 0, f"generate({t}) returned empty list"
    # Check safety: on_finished + cell_off
    joined = "\n".join(script)
    assert "on_finished:" in joined, f"{t}: missing on_finished"
    assert "cell_off" in joined, f"{t}: missing cell_off"
    # No empty lines in script
    for i, line in enumerate(script):
        assert line.strip(), f"{t}: empty line at index {i}"
    print(f"  {t}: {len(script)} lines, safety OK")
print("\nAll single-channel scripts generated correctly.")

# Test multi-channel
script_multi = generate("cv", {"e_begin": -0.5, "e_vertex1": 0.5, "e_vertex2": -0.5, "scan_rate": 0.1}, [1, 2, 3])
joined = "\n".join(script_multi)
assert "meas_loop_for" in joined, "Multi-channel should use meas_loop_for"
assert "set_gpio 0x000i" in joined, "Should have CH1 address"
assert "set_gpio 0x011i" in joined, "Should have CH2 address"
assert "set_gpio 0x022i" in joined, "Should have CH3 address"
print("Multi-channel script correct (3 channels with scan loop).")

# Test SI formatting via script output
script_si = generate("ca", {"e_dc": 0.5, "t_interval": 0.001, "t_run": 60.0}, [1])
joined_si = " ".join(script_si)
assert "500m" in joined_si, "0.5V should be 500m"
assert "1m" in joined_si, "0.001s should be 1m"
assert "60" in joined_si, "60.0s should be 60"
print("SI prefix formatting correct.")

# Test MUX alternating
script_alt = generate("ca_alt_mux", {}, [1, 5])
joined_alt = "\n".join(script_alt)
assert "meas_loop_ca_alt_mux" in joined_alt
assert "meas_loop_for" not in joined_alt, "MUX alt should NOT use scan loop"
print("MUX-alternating technique does not wrap in scan loop.")

print("\n" + "=" * 60)
print("VALIDATING src/data/models.py")
print("=" * 60)

# TechniqueConfig
tc = TechniqueConfig("CV", {"e_begin": -0.5}, [1, 2, 3])
assert tc.technique == "cv", "Should normalize to lowercase"
assert tc.channels == [1, 2, 3]
print(f"\nTechniqueConfig: {tc}")

# DataPoint
dp = DataPoint(0.1, 1, {"set_potential": 0.5, "current": 1.2e-6})
assert dp.get("current") == 1.2e-6
assert dp.get("missing", -1.0) == -1.0
print(f"DataPoint: {dp}")

# MeasurementResult
mr = MeasurementResult(technique="cv", start_time=datetime.now())
mr.add_point(DataPoint(0.0, 1, {"current": 1e-6}))
mr.add_point(DataPoint(0.1, 2, {"current": 2e-6}))
mr.add_point(DataPoint(0.2, 1, {"current": 3e-6}))
assert len(mr) == 3
assert mr.channels == [1, 2]
print(f"MeasurementResult: {len(mr)} points, channels={mr.channels}")

# ChannelData
cd = mr.for_channel(1)
assert len(cd) == 2
assert cd.values("current") == [1e-6, 3e-6]
assert cd.timestamps() == [0.0, 0.2]
assert "current" in cd.variable_names
print(f"ChannelData(ch=1): {len(cd)} points, vars={cd.variable_names}")

cd2 = mr.for_channel(2)
assert len(cd2) == 1
print(f"ChannelData(ch=2): {len(cd2)} points")

print("\n" + "=" * 60)
print("ALL VALIDATIONS PASSED")
print("=" * 60)
