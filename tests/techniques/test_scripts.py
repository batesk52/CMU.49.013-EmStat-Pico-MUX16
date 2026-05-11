"""Regression tests for MethodSCRIPT preamble bandwidth handling.

Phase 7 (CMU.17.022) parameterised mode-2 ``set_max_bandwidth`` via the
``bw_hz`` technique param. Mode-3 preambles (``_preamble_eis`` and
``_preamble_galvano``) remain locked at 200 kHz for control-loop
stability and must NOT be influenced by ``bw_hz``.

These tests guard:

1. ``_preamble({'cr': '2u', 'bw_hz': bw})`` emits the SI-formatted
   ``set_max_bandwidth {expected}`` line for each sweep value.
2. Omitting ``bw_hz`` falls back to the legacy 400 Hz default.
3. ``_preamble_eis`` and ``_preamble_galvano`` ignore ``bw_hz`` and
   keep ``set_max_bandwidth 200k``.
"""

from __future__ import annotations

import pytest

from src.techniques.scripts import (
    _preamble,
    _preamble_eis,
    _preamble_galvano,
)


# Expected SI strings derived from ``_format_si`` (mantissa kept in
# [1, 1000) wherever possible; unity prefix emits no suffix).
_BW_CASES = [
    (0.4, "400m"),
    (4, "4"),
    (40, "40"),
    (400, "400"),
    (4000, "4k"),
    (40000, "40k"),
    (200000, "200k"),
]


@pytest.mark.parametrize("bw_hz, expected", _BW_CASES)
def test_preamble_emits_si_formatted_bandwidth(
    bw_hz: float, expected: str
) -> None:
    """_preamble must emit set_max_bandwidth using _format_si(bw_hz)."""
    lines = _preamble({"cr": "2u", "bw_hz": bw_hz})
    expected_line = f"set_max_bandwidth {expected}"
    assert expected_line in lines, (
        f"Expected line {expected_line!r} in preamble for bw_hz={bw_hz}; "
        f"got: {lines}"
    )


def test_preamble_defaults_to_400hz_when_bw_hz_missing() -> None:
    """Omitting bw_hz preserves the legacy 400 Hz default."""
    lines = _preamble({"cr": "2u"})
    assert "set_max_bandwidth 400" in lines, (
        f"Default bandwidth broken; got: {lines}"
    )


def test_preamble_eis_ignores_bw_hz_and_stays_200k() -> None:
    """Mode-3 EIS preamble must remain hardcoded at 200 kHz."""
    # Without bw_hz
    lines = _preamble_eis({"cr": "10u"})
    assert "set_max_bandwidth 200k" in lines, (
        f"EIS preamble must stay 200k; got: {lines}"
    )

    # Even if a caller incorrectly passes bw_hz, EIS must ignore it.
    lines_with_bw = _preamble_eis({"cr": "10u", "bw_hz": 4})
    assert "set_max_bandwidth 200k" in lines_with_bw, (
        f"EIS preamble must ignore bw_hz; got: {lines_with_bw}"
    )
    assert "set_max_bandwidth 4" not in lines_with_bw, (
        "EIS preamble must not switch to bw_hz value"
    )


def test_preamble_galvano_ignores_bw_hz_and_stays_200k() -> None:
    """Mode-3 galvanostatic preamble (CP / GEIS) must stay at 200 kHz."""
    # Without bw_hz
    lines = _preamble_galvano({"cr": "10u"})
    assert "set_max_bandwidth 200k" in lines, (
        f"Galvanostatic preamble must stay 200k; got: {lines}"
    )

    # Even if a caller incorrectly passes bw_hz, galvano must ignore it.
    lines_with_bw = _preamble_galvano({"cr": "10u", "bw_hz": 4})
    assert "set_max_bandwidth 200k" in lines_with_bw, (
        "Galvanostatic preamble must ignore bw_hz; got: "
        f"{lines_with_bw}"
    )
    assert "set_max_bandwidth 4" not in lines_with_bw, (
        "Galvanostatic preamble must not switch to bw_hz value"
    )
