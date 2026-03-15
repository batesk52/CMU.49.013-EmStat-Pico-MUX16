"""MethodSCRIPT generator for all electrochemical techniques.

Generates complete MethodSCRIPT programs for the EmStat Pico using a
template-based approach: preamble (potentiostat configuration, cell_on)
followed by the technique measurement loop, followed by the postamble
(on_finished: cell_off safety block).

Each technique is parameterised with sensible defaults and produces
script lines ready to send via ``PicoConnection.send_script()``.

Values are formatted with MethodSCRIPT SI prefix notation (e.g.,
``500m`` for 0.5 V, ``100u`` for 0.0001 A). Integer values carry
the ``i`` suffix.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from src.comms.mux import MuxController

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SI prefix formatting
# ---------------------------------------------------------------------------

# Ordered from largest to smallest magnitude for best-fit selection.
_SI_TABLE: list[tuple[str, int]] = [
    ("E", 18),
    ("P", 15),
    ("T", 12),
    ("G", 9),
    ("M", 6),
    ("k", 3),
    ("", 0),
    ("m", -3),
    ("u", -6),
    ("n", -9),
    ("p", -12),
    ("f", -15),
    ("a", -18),
]


def _format_si(value: float) -> str:
    """Format a float using MethodSCRIPT SI prefix notation.

    Chooses the prefix that keeps the numeric part in a reasonable
    range. Zero is represented as ``0``.

    Args:
        value: The value to format.

    Returns:
        String like ``500m``, ``-100u``, ``1k``, or ``0``.
    """
    if value == 0.0:
        return "0"

    abs_val = abs(value)

    for prefix, exponent in _SI_TABLE:
        scaled = abs_val / (10**exponent)
        if scaled >= 1.0 or exponent == -18:
            # Use this prefix
            scaled_signed = value / (10**exponent)
            # Format: remove unnecessary trailing zeros
            if scaled_signed == int(scaled_signed):
                formatted = f"{int(scaled_signed)}{prefix}"
            else:
                # Up to 6 significant digits, strip trailing zeros
                formatted = f"{scaled_signed:.6g}{prefix}"
            return formatted

    # Fallback — should not reach here
    return f"{value}"  # pragma: no cover


def _format_int(value: int) -> str:
    """Format an integer with the MethodSCRIPT ``i`` suffix.

    Args:
        value: Integer value.

    Returns:
        String like ``16i``, ``0i``, ``-1i``.
    """
    return f"{value}i"


def _format_hex_int(value: int) -> str:
    """Format an integer as a hex literal with ``i`` suffix.

    Args:
        value: Non-negative integer.

    Returns:
        String like ``0x03Fi``.
    """
    return f"0x{value:03X}i"


# ---------------------------------------------------------------------------
# Default parameters per technique
# ---------------------------------------------------------------------------

# Each technique has a dict of parameter names → default values.
# Potential in V, current in A, time in s, frequency in Hz.
_TECHNIQUE_DEFAULTS: dict[str, dict[str, Any]] = {
    "lsv": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.01,
        "scan_rate": 0.1,
        "cr": "100u",
    },
    "dpv": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.005,
        "e_pulse": 0.05,
        "t_pulse": 0.05,
        "scan_rate": 0.01,
        "cr": "100u",
    },
    "swv": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.005,
        "amplitude": 0.025,
        "frequency": 25.0,
        "cr": "100u",
    },
    "npv": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.01,
        "t_pulse": 0.05,
        "t_base": 0.5,
        "cr": "100u",
    },
    "acv": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.005,
        "amplitude": 0.01,
        "frequency": 50.0,
        "cr": "100u",
    },
    "cv": {
        "e_begin": -0.5,
        "e_vertex1": 0.5,
        "e_vertex2": -0.5,
        "e_step": 0.01,
        "scan_rate": 0.1,
        "n_scans": 1,
        "cr": "100u",
    },
    "ca": {
        "e_dc": 0.0,
        "t_interval": 0.1,
        "t_run": 10.0,
        "cr": "100u",
    },
    "fca": {
        "e_dc": 0.0,
        "t_interval": 0.01,
        "t_run": 1.0,
        "cr": "100u",
    },
    "cp": {
        "i_dc": 0.0,
        "t_interval": 0.1,
        "t_run": 10.0,
        "cr": "100u",
    },
    "ocp": {
        "t_interval": 0.1,
        "t_run": 10.0,
    },
    "eis": {
        "e_dc": 0.0,
        "e_ac": 0.01,
        "freq_start": 100000.0,
        "freq_end": 0.1,
        "n_freq": 50,
        "cr": "100u",
    },
    "geis": {
        "i_dc": 0.0,
        "i_ac": 0.00001,
        "freq_start": 100000.0,
        "freq_end": 0.1,
        "n_freq": 50,
        "cr": "100u",
    },
    "pad": {
        "e1": 0.5,
        "t1": 0.1,
        "e2": -0.5,
        "t2": 0.1,
        "e3": 0.0,
        "t3": 0.05,
        "t_interval": 0.01,
        "n_cycles": 10,
        "cr": "100u",
    },
    "lsp": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.01,
        "t_step": 0.5,
        "cr": "100u",
    },
    "fcv": {
        "e_begin": -0.5,
        "e_vertex1": 0.5,
        "e_vertex2": -0.5,
        "scan_rate": 10.0,
        "n_scans": 1,
        "cr": "100u",
    },
    # MUX-alternating techniques
    "ca_alt_mux": {
        "e_dc": 0.0,
        "t_interval": 0.1,
        "t_run": 10.0,
        "cr": "100u",
    },
    "cp_alt_mux": {
        "i_dc": 0.0,
        "t_interval": 0.1,
        "t_run": 10.0,
        "cr": "100u",
    },
    "ocp_alt_mux": {
        "t_interval": 0.1,
        "t_run": 10.0,
    },
}


# ---------------------------------------------------------------------------
# Technique script builders
# ---------------------------------------------------------------------------


def _preamble(params: dict, *, needs_cell: bool = True) -> list[str]:
    """Generate the standard MethodSCRIPT preamble.

    Configures the potentiostat, sets the current range, and turns
    the cell on.

    Args:
        params: Technique parameters (uses 'cr' for current range).
        needs_cell: Whether to include ``cell_on`` (False for OCP).

    Returns:
        List of MethodSCRIPT lines.
    """
    lines: list[str] = []
    cr = params.get("cr", "100u")
    lines.append(f"set_pgstat_chan 0")
    lines.append(f"set_pgstat_mode 0")
    lines.append(f"set_max_bandwidth 200")
    lines.append(f"set_cr {cr}")
    lines.append(f"set_autoranging ba {cr} {cr}")
    if needs_cell:
        lines.append("cell_on")
    return lines


def _postamble() -> list[str]:
    """Generate the standard MethodSCRIPT postamble.

    Every script MUST include ``on_finished: cell_off`` for safety.

    Returns:
        List of MethodSCRIPT lines.
    """
    return [
        "on_finished:",
        "  cell_off",
    ]


def _pck_block(var_codes: list[str]) -> list[str]:
    """Generate a pck_start/pck_add/pck_end block.

    Args:
        var_codes: List of 2-char variable type codes to include
            in the data packet (e.g., ['da', 'ba']).

    Returns:
        List of MethodSCRIPT lines for the packet configuration.
    """
    lines: list[str] = [f"pck_start"]
    for code in var_codes:
        lines.append(f"pck_add {code}")
    lines.append("pck_end")
    return lines


# -- Individual technique builders -----------------------------------------


def _build_lsv(params: dict) -> list[str]:
    """Linear Sweep Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    scan_rate = _format_si(params["scan_rate"])
    lines: list[str] = []
    lines.append(f"meas_loop_lsv p {e_begin} {e_end} {e_step} {scan_rate}")
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_dpv(params: dict) -> list[str]:
    """Differential Pulse Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    e_pulse = _format_si(params["e_pulse"])
    t_pulse = _format_si(params["t_pulse"])
    scan_rate = _format_si(params["scan_rate"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_dpv p {e_begin} {e_end} {e_step} "
        f"{e_pulse} {t_pulse} {scan_rate}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_swv(params: dict) -> list[str]:
    """Square Wave Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    amplitude = _format_si(params["amplitude"])
    frequency = _format_si(params["frequency"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_swv p {e_begin} {e_end} {e_step} "
        f"{amplitude} {frequency}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_npv(params: dict) -> list[str]:
    """Normal Pulse Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    t_pulse = _format_si(params["t_pulse"])
    t_base = _format_si(params["t_base"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_npv p {e_begin} {e_end} {e_step} "
        f"{t_pulse} {t_base}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_acv(params: dict) -> list[str]:
    """AC Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    amplitude = _format_si(params["amplitude"])
    frequency = _format_si(params["frequency"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_acv p {e_begin} {e_end} {e_step} "
        f"{amplitude} {frequency}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_cv(params: dict) -> list[str]:
    """Cyclic Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_vertex1 = _format_si(params["e_vertex1"])
    e_vertex2 = _format_si(params["e_vertex2"])
    e_step = _format_si(params["e_step"])
    scan_rate = _format_si(params["scan_rate"])
    n_scans = _format_int(params.get("n_scans", 1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_cv p {e_begin} {e_vertex1} {e_vertex2} "
        f"{e_step} {scan_rate} {n_scans}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


def _build_ca(params: dict) -> list[str]:
    """Chronoamperometry."""
    e_dc = _format_si(params["e_dc"])
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(f"meas_loop_ca p {e_dc} {t_interval} {t_run}")
    lines.extend(["  " + l for l in _pck_block(["da", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_fca(params: dict) -> list[str]:
    """Fast Chronoamperometry."""
    e_dc = _format_si(params["e_dc"])
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(f"meas_loop_ca p {e_dc} {t_interval} {t_run}")
    lines.extend(["  " + l for l in _pck_block(["da", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_cp(params: dict) -> list[str]:
    """Chronopotentiometry."""
    i_dc = _format_si(params["i_dc"])
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(f"meas_loop_cp p {i_dc} {t_interval} {t_run}")
    lines.extend(["  " + l for l in _pck_block(["ab", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_ocp(params: dict) -> list[str]:
    """Open Circuit Potential."""
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(f"meas_loop_ocp p {t_interval} {t_run}")
    lines.extend(["  " + l for l in _pck_block(["ab", "ca"])])
    lines.append("endloop")
    return lines


def _build_eis(params: dict) -> list[str]:
    """Electrochemical Impedance Spectroscopy."""
    e_dc = _format_si(params["e_dc"])
    e_ac = _format_si(params["e_ac"])
    freq_start = _format_si(params["freq_start"])
    freq_end = _format_si(params["freq_end"])
    n_freq = _format_int(params.get("n_freq", 50))
    lines: list[str] = []
    lines.append(
        f"meas_loop_eis p {e_dc} {e_ac} {freq_start} {freq_end} {n_freq}"
    )
    lines.extend(
        ["  " + l for l in _pck_block(["cc", "dc", "dd"])]
    )
    lines.append("endloop")
    return lines


def _build_geis(params: dict) -> list[str]:
    """Galvanostatic EIS."""
    i_dc = _format_si(params["i_dc"])
    i_ac = _format_si(params["i_ac"])
    freq_start = _format_si(params["freq_start"])
    freq_end = _format_si(params["freq_end"])
    n_freq = _format_int(params.get("n_freq", 50))
    lines: list[str] = []
    lines.append(
        f"meas_loop_geis p {i_dc} {i_ac} "
        f"{freq_start} {freq_end} {n_freq}"
    )
    lines.extend(
        ["  " + l for l in _pck_block(["cc", "dc", "dd"])]
    )
    lines.append("endloop")
    return lines


def _build_pad(params: dict) -> list[str]:
    """Pulsed Amperometric Detection."""
    e1 = _format_si(params["e1"])
    t1 = _format_si(params["t1"])
    e2 = _format_si(params["e2"])
    t2 = _format_si(params["t2"])
    e3 = _format_si(params["e3"])
    t3 = _format_si(params["t3"])
    t_interval = _format_si(params["t_interval"])
    n_cycles = _format_int(params.get("n_cycles", 10))
    lines: list[str] = []
    lines.append(
        f"meas_loop_pad p {e1} {t1} {e2} {t2} "
        f"{e3} {t3} {t_interval} {n_cycles}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_lsp(params: dict) -> list[str]:
    """Linear Sweep / Staircase Potential."""
    e_begin = _format_si(params["e_begin"])
    e_end = _format_si(params["e_end"])
    e_step = _format_si(params["e_step"])
    t_step = _format_si(params["t_step"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_lsp p {e_begin} {e_end} {e_step} {t_step}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_fcv(params: dict) -> list[str]:
    """Fast Cyclic Voltammetry."""
    e_begin = _format_si(params["e_begin"])
    e_vertex1 = _format_si(params["e_vertex1"])
    e_vertex2 = _format_si(params["e_vertex2"])
    scan_rate = _format_si(params["scan_rate"])
    n_scans = _format_int(params.get("n_scans", 1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_cv p {e_begin} {e_vertex1} {e_vertex2} "
        f"{_format_si(0.001)} {scan_rate} {n_scans}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba"])])
    lines.append("endloop")
    return lines


# MUX-alternating technique builders

def _build_ca_alt_mux(params: dict) -> list[str]:
    """Chronoamperometry with MUX-alternating."""
    e_dc = _format_si(params["e_dc"])
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_ca_alt_mux p {e_dc} {t_interval} {t_run}"
    )
    lines.extend(["  " + l for l in _pck_block(["da", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_cp_alt_mux(params: dict) -> list[str]:
    """Chronopotentiometry with MUX-alternating."""
    i_dc = _format_si(params["i_dc"])
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_cp_alt_mux p {i_dc} {t_interval} {t_run}"
    )
    lines.extend(["  " + l for l in _pck_block(["ab", "ba", "ca"])])
    lines.append("endloop")
    return lines


def _build_ocp_alt_mux(params: dict) -> list[str]:
    """OCP with MUX-alternating."""
    t_interval = _format_si(params["t_interval"])
    t_run = _format_si(params["t_run"])
    lines: list[str] = []
    lines.append(
        f"meas_loop_ocp_alt_mux p {t_interval} {t_run}"
    )
    lines.extend(["  " + l for l in _pck_block(["ab", "ca"])])
    lines.append("endloop")
    return lines


# ---------------------------------------------------------------------------
# Technique registry
# ---------------------------------------------------------------------------

# Maps normalised technique name → (builder_function, needs_cell_on)
_TECHNIQUE_BUILDERS: dict[str, tuple[Any, bool]] = {
    "lsv": (_build_lsv, True),
    "dpv": (_build_dpv, True),
    "swv": (_build_swv, True),
    "npv": (_build_npv, True),
    "acv": (_build_acv, True),
    "cv": (_build_cv, True),
    "ca": (_build_ca, True),
    "fca": (_build_fca, True),
    "cp": (_build_cp, True),
    "ocp": (_build_ocp, False),
    "eis": (_build_eis, True),
    "geis": (_build_geis, True),
    "pad": (_build_pad, True),
    "lsp": (_build_lsp, True),
    "fcv": (_build_fcv, True),
    "ca_alt_mux": (_build_ca_alt_mux, True),
    "cp_alt_mux": (_build_cp_alt_mux, True),
    "ocp_alt_mux": (_build_ocp_alt_mux, False),
}

# Techniques that handle MUX internally (device-level alternation)
_MUX_ALT_TECHNIQUES = {"ca_alt_mux", "cp_alt_mux", "ocp_alt_mux"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def supported_techniques() -> list[str]:
    """Return a sorted list of all supported technique identifiers.

    Returns:
        List of technique name strings (e.g., ['acv', 'ca', 'cv', ...]).
    """
    return sorted(_TECHNIQUE_BUILDERS.keys())


def technique_params(technique: str) -> dict:
    """Return the default parameters for a technique.

    Args:
        technique: Technique identifier (case-insensitive).

    Returns:
        Dictionary of parameter name to default value. Returns a copy
        so the caller can modify it freely.

    Raises:
        ValueError: If the technique is not supported.
    """
    key = technique.lower()
    if key not in _TECHNIQUE_DEFAULTS:
        raise ValueError(
            f"Unknown technique {technique!r}. "
            f"Supported: {supported_techniques()}"
        )
    return dict(_TECHNIQUE_DEFAULTS[key])


def generate(
    technique: str,
    params: dict,
    channels: list[int],
) -> list[str]:
    """Generate a complete MethodSCRIPT for a technique and channels.

    Composes:
    1. MUX GPIO configuration (if multiple channels)
    2. Preamble (potentiostat config, current range, cell_on)
    3. Channel scanning loop wrapping the technique measurement
    4. Postamble (on_finished: cell_off)

    For single-channel measurements, the MUX is set directly without
    a scan loop. For MUX-alternating techniques (``ca_alt_mux``,
    ``cp_alt_mux``, ``ocp_alt_mux``), the device handles channel
    alternation internally.

    Args:
        technique: Technique identifier (case-insensitive, e.g., 'cv').
        params: Technique parameters. Missing keys are filled from
            defaults via ``technique_params()``.
        channels: 1-indexed MUX channel numbers (1-16).

    Returns:
        List of MethodSCRIPT lines ready for ``send_script()``.

    Raises:
        ValueError: If the technique is not supported or channels
            list is empty.
    """
    key = technique.lower()
    if key not in _TECHNIQUE_BUILDERS:
        raise ValueError(
            f"Unknown technique {technique!r}. "
            f"Supported: {supported_techniques()}"
        )
    if not channels:
        raise ValueError("Channel list must not be empty.")

    builder_fn, needs_cell = _TECHNIQUE_BUILDERS[key]

    # Merge user params over defaults
    merged = technique_params(key)
    merged.update(params)

    # Build the technique-specific measurement loop lines
    technique_lines = builder_fn(merged)

    mux = MuxController()
    script: list[str] = []

    # GPIO configuration
    script.extend(mux.gpio_config_script())

    # Preamble: potentiostat setup + cell_on
    script.extend(_preamble(merged, needs_cell=needs_cell))

    if key in _MUX_ALT_TECHNIQUES:
        # MUX-alternating: set first channel, device alternates
        script.extend(mux.select_channel_script(channels[0]))
        script.extend(technique_lines)
    elif len(channels) == 1:
        # Single channel: select and run
        script.extend(mux.select_channel_script(channels[0]))
        script.extend(technique_lines)
    else:
        # Multi-channel: wrap technique in a channel scan loop
        n_ch = len(channels)
        script.append(f"meas_loop_for ch c {_format_int(n_ch)}")
        for i, ch in enumerate(channels):
            addr = mux.channel_address(ch)
            if i > 0:
                script.append(f"  add_var ch {_format_int(1)} {_format_int(0)}")
            script.append(f"  set_gpio {_format_hex_int(addr)}")
            # Indent technique lines inside the channel loop
            for tl in technique_lines:
                script.append(f"  {tl}")
        script.append("endloop")

    # Safety postamble
    script.extend(_postamble())

    return script
