"""MethodSCRIPT generator for all electrochemical techniques.

Generates parameterised MethodSCRIPT programs for the EmStat Pico.
Each script follows the template:

    preamble  (pgstat config, current range, cell_on)
    technique (measurement loop with pck_start/pck_add/pck_end)
    postamble (on_finished: cell_off)

Values are formatted with MethodSCRIPT SI prefix notation (e.g.,
``500m`` for 0.5 V). Integer values are suffixed with ``i``.

The public API consists of three functions:

- ``generate(technique, params, channels)`` -- full script lines
- ``supported_techniques()`` -- list of technique keys
- ``technique_params(technique)`` -- default parameters for a technique
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

# Ordered from largest to smallest magnitude.
_SI_TABLE: list[tuple[str, int]] = [
    ("E", 18),
    ("P", 15),
    ("T", 12),
    ("G", 9),
    ("M", 6),
    ("k", 3),
    (" ", 0),
    ("m", -3),
    ("u", -6),
    ("n", -9),
    ("p", -12),
    ("f", -15),
    ("a", -18),
]


def _format_si(value: float) -> str:
    """Format a float using MethodSCRIPT SI prefix notation.

    Selects the SI prefix that keeps the mantissa in the range
    [1, 1000) when possible, falling back to unity for zero.

    Args:
        value: The numeric value to format.

    Returns:
        String such as ``'500m'``, ``'1 '``, or ``'-200u'``.
        The space character represents the unity prefix.

    Examples:
        >>> _format_si(0.5)
        '500m'
        >>> _format_si(0.001)
        '1m'
        >>> _format_si(-0.0002)
        '-200u'
        >>> _format_si(0.0)
        '0 '
    """
    if value == 0.0:
        return "0"

    abs_val = abs(value)

    for prefix, exp in _SI_TABLE:
        scaled = abs_val / (10**exp)
        if scaled >= 1.0 or exp == -18:
            mantissa = value / (10**exp)
            # Unity prefix: use bare number (no trailing space)
            pfx = "" if prefix == " " else prefix
            if abs(mantissa - round(mantissa)) < 1e-9:
                return f"{int(round(mantissa))}{pfx}"
            return f"{mantissa:g}{pfx}"

    # Fallback (should not be reached)
    return f"{value:g}"  # pragma: no cover


def _indent(lines: list[str]) -> list[str]:
    """Indent MethodSCRIPT lines with two spaces for loop body."""
    return [f"  {line}" for line in lines]


def _format_int(value: int) -> str:
    """Format an integer with the MethodSCRIPT ``i`` suffix.

    Args:
        value: Integer value.

    Returns:
        String like ``'10i'`` or ``'0i'``.
    """
    return f"{value}i"


# ---------------------------------------------------------------------------
# Default parameters per technique
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, Any]] = {
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
        "scan_rate": 0.05,
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
        "e_pulse": 0.05,
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
        "e_dc": 0.2,
        "t_run": 10.0,
        "t_interval": 0.1,
        "cr": "100u",
    },
    "fca": {
        "e_dc": 0.2,
        "t_run": 10.0,
        "t_interval": 0.1,
        "cr": "100u",
    },
    "cp": {
        "i_dc": 0.0001,
        "t_run": 10.0,
        "t_interval": 0.1,
        "cr": "100u",
    },
    "ocp": {
        "t_run": 60.0,
        "t_interval": 1.0,
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
        "e_cond": -0.2,
        "t_cond": 0.5,
        "e_dep": -0.5,
        "t_dep": 5.0,
        "e_eq": -0.2,
        "t_eq": 1.0,
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.005,
        "e_pulse": 0.05,
        "t_pulse": 0.05,
        "scan_rate": 0.05,
        "cr": "100u",
    },
    "lsp": {
        "e_begin": -0.5,
        "e_end": 0.5,
        "e_step": 0.01,
        "scan_rate": 0.1,
        "cr": "100u",
    },
    "fcv": {
        "e_begin": -0.5,
        "e_vertex1": 0.5,
        "e_vertex2": -0.5,
        "e_step": 0.01,
        "scan_rate": 1.0,
        "n_scans": 1,
        "cr": "100u",
    },
    # MUX-alternating variants
    "ca_alt_mux": {
        "e_dc": 0.2,
        "t_run": 10.0,
        "t_interval": 0.1,
        "cr": "100u",
    },
    "cp_alt_mux": {
        "i_dc": 0.0001,
        "t_run": 10.0,
        "t_interval": 0.1,
        "cr": "100u",
    },
    "ocp_alt_mux": {
        "t_run": 60.0,
        "t_interval": 1.0,
    },
}

# ---------------------------------------------------------------------------
# Current range helper
# ---------------------------------------------------------------------------

_CR_MAP: dict[str, str] = {
    "100n": "0",
    "2u": "1",
    "4u": "2",
    "8u": "3",
    "16u": "4",
    "32u": "5",
    "63u": "6",
    "100u": "7",
    "1m": "8",
    "10m": "9",
    "100m": "10",
}


def _cr_index(cr: str) -> str:
    """Convert a current range string to its MethodSCRIPT index.

    Args:
        cr: Current range string (e.g., '100u', '1m').

    Returns:
        Index string for ``set_cr``.
    """
    return _CR_MAP.get(cr, "7")  # default 100uA


# ---------------------------------------------------------------------------
# Technique script builders
# ---------------------------------------------------------------------------


def _preamble(params: dict[str, Any]) -> list[str]:
    """Build the standard script preamble.

    Configures both potentiostat channels (chan 1 off, chan 0 in
    potentiostatic mode), sets current range, then turns cell on.
    The dual-channel setup is required for MUX16 operation.
    """
    lines: list[str] = []
    lines.append("e")
    cr = params.get("cr", "100u")
    cr_idx = _cr_index(cr)
    lines.append("var p")
    lines.append("var c")
    lines.append("set_pgstat_chan 1")
    lines.append("set_pgstat_mode 0")
    lines.append("set_pgstat_chan 0")
    lines.append("set_pgstat_mode 2")
    cr = params.get("cr", "100u")
    lines.append(f"set_autoranging 100n {cr}")
    lines.append("cell_on")
    return lines


def _preamble_galvano(params: dict[str, Any]) -> list[str]:
    """Build preamble for galvanostatic techniques (CP, GEIS).

    Configures chan 1 off, chan 0 in galvanostatic mode.
    """
    lines: list[str] = []
    lines.append("e")
    cr = params.get("cr", "100u")
    lines.append("var p")
    lines.append("var c")
    lines.append("set_pgstat_chan 1")
    lines.append("set_pgstat_mode 0")
    lines.append("set_pgstat_chan 0")
    lines.append("set_pgstat_mode 3")
    lines.append(f"set_autoranging 100n {cr}")
    lines.append("cell_on")
    return lines


def _preamble_ocp() -> list[str]:
    """Build preamble for OCP (no cell_on, open circuit).

    Configures chan 1 off, chan 0 in high-impedance mode.
    """
    lines: list[str] = []
    lines.append("e")
    lines.append("var p")
    lines.append("var c")
    lines.append("set_pgstat_chan 1")
    lines.append("set_pgstat_mode 0")
    lines.append("set_pgstat_chan 0")
    lines.append("set_pgstat_mode 2")
    return lines


def _postamble() -> list[str]:
    """Build the standard script postamble with safety cell_off."""
    return [
        "on_finished:",
        "  cell_off",
    ]


def _pck_voltammetry() -> list[str]:
    """Packet config for voltammetric techniques (potential + current)."""
    return [
        "pck_start",
        "pck_add p",
        "pck_add c",
        "pck_end",
    ]


def _pck_amperometry() -> list[str]:
    """Packet config for amperometric techniques (time + current)."""
    return [
        "pck_start",
        "pck_add p",
        "pck_add c",
        "pck_end",
    ]


def _pck_potentiometry() -> list[str]:
    """Packet config for potentiometric techniques (time + potential)."""
    return [
        "pck_start",
        "pck_add p",
        "pck_add c",
        "pck_end",
    ]


def _pck_eis() -> list[str]:
    """Packet config for EIS (Z_real, Z_imag, phase, |Z|, freq).

    Note: EIS may need additional variables declared for full impedance
    data. This uses p and c for now — validate with hardware.
    """
    return [
        "pck_start",
        "pck_add p",
        "pck_add c",
        "pck_end",
    ]


# ---------------------------------------------------------------------------
# Individual technique generators
# ---------------------------------------------------------------------------


def _gen_lsv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_lsv script body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.01))
    scan_rate = _format_si(params.get("scan_rate", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_lsv p c {e_begin} {e_end} {e_step} {scan_rate}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_dpv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_dpv script body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.005))
    e_pulse = _format_si(params.get("e_pulse", 0.05))
    t_pulse = _format_si(params.get("t_pulse", 0.05))
    scan_rate = _format_si(params.get("scan_rate", 0.05))
    lines: list[str] = []
    lines.append(
        f"meas_loop_dpv p c {e_begin} {e_end} {e_step}"
        f" {e_pulse} {t_pulse} {scan_rate}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_swv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_swv script body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.005))
    amplitude = _format_si(params.get("amplitude", 0.025))
    frequency = _format_si(params.get("frequency", 25.0))
    lines: list[str] = []
    lines.append(
        f"meas_loop_swv p c {e_begin} {e_end} {e_step}"
        f" {amplitude} {frequency}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_npv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_npv script body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.01))
    e_pulse = _format_si(params.get("e_pulse", 0.05))
    t_pulse = _format_si(params.get("t_pulse", 0.05))
    t_base = _format_si(params.get("t_base", 0.5))
    lines: list[str] = []
    lines.append(
        f"meas_loop_npv p c {e_begin} {e_end} {e_step}"
        f" {e_pulse} {t_pulse} {t_base}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_acv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_acv script body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.005))
    amplitude = _format_si(params.get("amplitude", 0.01))
    frequency = _format_si(params.get("frequency", 50.0))
    lines: list[str] = []
    lines.append(
        f"meas_loop_acv p c {e_begin} {e_end} {e_step}"
        f" {amplitude} {frequency}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_cv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_cv script body.

    Note: ``nscans`` is a separate command before the measurement loop,
    NOT a trailing argument to ``meas_loop_cv`` (which causes ``!4005``).
    """
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_vertex1 = _format_si(params.get("e_vertex1", 0.5))
    e_vertex2 = _format_si(params.get("e_vertex2", -0.5))
    e_step = _format_si(params.get("e_step", 0.01))
    scan_rate = _format_si(params.get("scan_rate", 0.1))
    n_scans = int(params.get("n_scans", 1))
    scan_body = [
        f"meas_loop_cv p c {e_begin} {e_vertex1} {e_vertex2}"
        f" {e_step} {scan_rate}",
        *_indent(_pck_voltammetry()),
        "endloop",
    ]
    lines: list[str] = []
    for _ in range(n_scans):
        lines.extend(scan_body)
    return lines


def _gen_ca(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_ca script body."""
    e_dc = _format_si(params.get("e_dc", 0.2))
    t_run = _format_si(params.get("t_run", 10.0))
    t_interval = _format_si(params.get("t_interval", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_ca p c {e_dc} {t_interval} {t_run}"
    )
    lines.extend(_indent(_pck_amperometry()))
    lines.append("endloop")
    return lines


def _gen_fca(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_fca (fixed-potential CA) script body."""
    e_dc = _format_si(params.get("e_dc", 0.2))
    t_run = _format_si(params.get("t_run", 10.0))
    t_interval = _format_si(params.get("t_interval", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_fca p c {e_dc} {t_run} {t_interval}"
    )
    lines.extend(_indent(_pck_amperometry()))
    lines.append("endloop")
    return lines


def _gen_cp(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_cp script body."""
    i_dc = _format_si(params.get("i_dc", 0.0001))
    t_run = _format_si(params.get("t_run", 10.0))
    t_interval = _format_si(params.get("t_interval", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_cp p c {i_dc} {t_run} {t_interval}"
    )
    lines.extend(_indent(_pck_potentiometry()))
    lines.append("endloop")
    return lines


def _gen_ocp(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_ocp script body."""
    t_run = _format_si(params.get("t_run", 60.0))
    t_interval = _format_si(params.get("t_interval", 1.0))
    lines: list[str] = []
    lines.append(f"meas_loop_ocp p c {t_run} {t_interval}")
    lines.extend(_indent(_pck_potentiometry()))
    lines.append("endloop")
    return lines


def _gen_eis(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_eis script body."""
    e_dc = _format_si(params.get("e_dc", 0.0))
    e_ac = _format_si(params.get("e_ac", 0.01))
    freq_start = _format_si(params.get("freq_start", 100000.0))
    freq_end = _format_si(params.get("freq_end", 0.1))
    n_freq = int(params.get("n_freq", 50))
    lines: list[str] = []
    lines.append(
        f"meas_loop_eis p c {e_dc} {e_ac}"
        f" {freq_start} {freq_end} {_format_int(n_freq)}"
    )
    lines.extend(_indent(_pck_eis()))
    lines.append("endloop")
    return lines


def _gen_geis(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_geis (galvanostatic EIS) script body."""
    i_dc = _format_si(params.get("i_dc", 0.0))
    i_ac = _format_si(params.get("i_ac", 0.00001))
    freq_start = _format_si(params.get("freq_start", 100000.0))
    freq_end = _format_si(params.get("freq_end", 0.1))
    n_freq = int(params.get("n_freq", 50))
    lines: list[str] = []
    lines.append(
        f"meas_loop_geis p c {i_dc} {i_ac}"
        f" {freq_start} {freq_end} {_format_int(n_freq)}"
    )
    lines.extend(_indent(_pck_eis()))
    lines.append("endloop")
    return lines


def _gen_pad(params: dict[str, Any]) -> list[str]:
    """Generate PAD (preconcentration + stripping DPV) script body.

    PAD combines conditioning, deposition, and equilibration steps
    followed by a DPV measurement.
    """
    e_cond = _format_si(params.get("e_cond", -0.2))
    t_cond = _format_si(params.get("t_cond", 0.5))
    e_dep = _format_si(params.get("e_dep", -0.5))
    t_dep = _format_si(params.get("t_dep", 5.0))
    e_eq = _format_si(params.get("e_eq", -0.2))
    t_eq = _format_si(params.get("t_eq", 1.0))
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.005))
    e_pulse = _format_si(params.get("e_pulse", 0.05))
    t_pulse = _format_si(params.get("t_pulse", 0.05))
    scan_rate = _format_si(params.get("scan_rate", 0.05))
    lines: list[str] = []
    # Conditioning step
    lines.append(f"set_e {e_cond}")
    lines.append(f"wait {t_cond}")
    # Deposition step
    lines.append(f"set_e {e_dep}")
    lines.append(f"wait {t_dep}")
    # Equilibration step
    lines.append(f"set_e {e_eq}")
    lines.append(f"wait {t_eq}")
    # DPV measurement
    lines.append(
        f"meas_loop_dpv p c {e_begin} {e_end} {e_step}"
        f" {e_pulse} {t_pulse} {scan_rate}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_lsp(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_lsp (linear sweep potentiometry) body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_end = _format_si(params.get("e_end", 0.5))
    e_step = _format_si(params.get("e_step", 0.01))
    scan_rate = _format_si(params.get("scan_rate", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_lsp p c {e_begin} {e_end} {e_step} {scan_rate}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


def _gen_fcv(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_fcv (fast cyclic voltammetry) body."""
    e_begin = _format_si(params.get("e_begin", -0.5))
    e_vertex1 = _format_si(params.get("e_vertex1", 0.5))
    e_vertex2 = _format_si(params.get("e_vertex2", -0.5))
    e_step = _format_si(params.get("e_step", 0.01))
    scan_rate = _format_si(params.get("scan_rate", 1.0))
    n_scans = int(params.get("n_scans", 1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_fcv p c {e_begin} {e_vertex1} {e_vertex2}"
        f" {e_step} {scan_rate} {_format_int(n_scans)}"
    )
    lines.extend(_indent(_pck_voltammetry()))
    lines.append("endloop")
    return lines


# --- MUX-alternating techniques ---


def _gen_ca_alt_mux(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_ca_alt_mux body (MUX-alternating CA)."""
    e_dc = _format_si(params.get("e_dc", 0.2))
    t_run = _format_si(params.get("t_run", 10.0))
    t_interval = _format_si(params.get("t_interval", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_ca_alt_mux p c {e_dc} {t_run} {t_interval}"
    )
    lines.extend(_indent(_pck_amperometry()))
    lines.append("endloop")
    return lines


def _gen_cp_alt_mux(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_cp_alt_mux body (MUX-alternating CP)."""
    i_dc = _format_si(params.get("i_dc", 0.0001))
    t_run = _format_si(params.get("t_run", 10.0))
    t_interval = _format_si(params.get("t_interval", 0.1))
    lines: list[str] = []
    lines.append(
        f"meas_loop_cp_alt_mux p c {i_dc} {t_run} {t_interval}"
    )
    lines.extend(_indent(_pck_potentiometry()))
    lines.append("endloop")
    return lines


def _gen_ocp_alt_mux(params: dict[str, Any]) -> list[str]:
    """Generate meas_loop_ocp_alt_mux body (MUX-alternating OCP)."""
    t_run = _format_si(params.get("t_run", 60.0))
    t_interval = _format_si(params.get("t_interval", 1.0))
    lines: list[str] = []
    lines.append(
        f"meas_loop_ocp_alt_mux p c {t_run} {t_interval}"
    )
    lines.extend(_indent(_pck_potentiometry()))
    lines.append("endloop")
    return lines


# ---------------------------------------------------------------------------
# Technique registry
# ---------------------------------------------------------------------------

# Maps technique key to (body_generator, preamble_builder).
# The preamble_builder is a callable that takes params and returns lines.
_TECHNIQUE_REGISTRY: dict[str, tuple] = {
    "lsv": (_gen_lsv, _preamble),
    "dpv": (_gen_dpv, _preamble),
    "swv": (_gen_swv, _preamble),
    "npv": (_gen_npv, _preamble),
    "acv": (_gen_acv, _preamble),
    "cv": (_gen_cv, _preamble),
    "ca": (_gen_ca, _preamble),
    "fca": (_gen_fca, _preamble),
    "cp": (_gen_cp, _preamble_galvano),
    "ocp": (_gen_ocp, _preamble_ocp),
    "eis": (_gen_eis, _preamble),
    "geis": (_gen_geis, _preamble_galvano),
    "pad": (_gen_pad, _preamble),
    "lsp": (_gen_lsp, _preamble),
    "fcv": (_gen_fcv, _preamble),
    # MUX-alternating variants
    "ca_alt_mux": (_gen_ca_alt_mux, _preamble),
    "cp_alt_mux": (_gen_cp_alt_mux, _preamble_galvano),
    "ocp_alt_mux": (_gen_ocp_alt_mux, _preamble_ocp),
}

# Techniques whose _alt_mux loop already handles MUX internally
_ALT_MUX_TECHNIQUES = {"ca_alt_mux", "cp_alt_mux", "ocp_alt_mux"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate(
    technique: str,
    params: dict[str, Any],
    channels: list[int],
) -> list[str]:
    """Generate a complete MethodSCRIPT for the given technique.

    Assembles preamble, MUX channel setup, technique measurement body,
    and the safety postamble (``on_finished: cell_off``).

    For multi-channel runs, the technique body is wrapped in a MUX scan
    loop that iterates over each channel. MUX-alternating techniques
    (``ca_alt_mux``, ``cp_alt_mux``, ``ocp_alt_mux``) handle channel
    switching internally within their measurement loop.

    Args:
        technique: Technique identifier (case-insensitive), e.g.
            ``'cv'``, ``'dpv'``, ``'eis'``, ``'ca_alt_mux'``.
        params: Technique-specific parameters. Missing keys fall back
            to built-in defaults.
        channels: 1-indexed MUX channel numbers (e.g., ``[1, 2, 5]``).

    Returns:
        List of MethodSCRIPT lines (no trailing newlines, no empty
        lines) ready to be sent via ``PicoConnection.send_script()``.

    Raises:
        ValueError: If the technique is not supported or channels
            is empty.
    """
    technique = technique.lower()
    if technique not in _TECHNIQUE_REGISTRY:
        raise ValueError(
            f"Unsupported technique {technique!r}. "
            f"Supported: {supported_techniques()}"
        )
    if not channels:
        raise ValueError("At least one channel must be specified.")

    body_gen, preamble_fn = _TECHNIQUE_REGISTRY[technique]

    # Merge user params with defaults
    merged = dict(_DEFAULTS.get(technique, {}))
    merged.update(params)

    # Build preamble
    if preamble_fn == _preamble_ocp:
        script_lines = _preamble_ocp()
    else:
        script_lines = preamble_fn(merged)

    mux = MuxController()

    # Build technique body
    body = body_gen(merged)

    if technique in _ALT_MUX_TECHNIQUES:
        # Alt-MUX techniques handle MUX switching internally.
        # Just configure GPIO and include the body directly.
        script_lines.extend(mux.gpio_config_script())
        # Set up the initial channel addresses for the MUX
        for ch in channels:
            mux._validate_channel(ch)
        script_lines.extend(body)
    elif len(channels) == 1:
        # Single channel: configure GPIO, select channel, run technique
        script_lines.extend(mux.gpio_config_script())
        script_lines.extend(mux.select_channel_script(channels[0]))
        script_lines.extend(body)
    else:
        # Multi-channel: add loop variables if using compact pattern
        if mux._is_consecutive(channels):
            # Insert var i / var e after the existing var declarations
            # Find insertion point (after last 'var' line)
            insert_idx = 0
            for idx, line in enumerate(script_lines):
                if line.startswith("var "):
                    insert_idx = idx + 1
            script_lines.insert(insert_idx, "var e")
            script_lines.insert(insert_idx, "var i")
        script_lines.extend(
            mux.scan_channels_script_with_body(channels, body)
        )

    # Postamble (safety: cell_off on finish)
    script_lines.extend(_postamble())

    # Strip the leading 'e' command — it's the script-loading trigger
    # that PicoConnection.send_script() handles separately
    if script_lines and script_lines[0] == "e":
        script_lines = script_lines[1:]

    return script_lines


def supported_techniques() -> list[str]:
    """Return a sorted list of all supported technique identifiers.

    Returns:
        List of lowercase technique keys (e.g., ``['acv', 'ca', ...]``).
    """
    return sorted(_TECHNIQUE_REGISTRY.keys())


def technique_params(technique: str) -> dict[str, Any]:
    """Return the default parameters for a technique.

    Args:
        technique: Technique identifier (case-insensitive).

    Returns:
        A copy of the default parameter dict. Returns an empty dict
        if the technique has no defined defaults.

    Raises:
        ValueError: If the technique is not supported.
    """
    technique = technique.lower()
    if technique not in _TECHNIQUE_REGISTRY:
        raise ValueError(
            f"Unsupported technique {technique!r}. "
            f"Supported: {supported_techniques()}"
        )
    return dict(_DEFAULTS.get(technique, {}))
