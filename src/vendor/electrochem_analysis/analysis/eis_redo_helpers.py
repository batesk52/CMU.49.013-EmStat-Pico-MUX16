"""
EIS redo helpers for CMU.87.082 corrected re-analysis (2026-05-13).

Built on EISCircuitFitter (CMU.17.026) to redo CMU.87.082's predictor
analysis with alpha-corrected R_ct, the expanded 2026-05-03 cohort
(S0181/S0182/S0183), and the new DR_ct = R_ct_post - R_ct_pre variable
enabled by 84.051 post-enzyme EIS data.

This module is NOT a general-purpose library. It encodes the locked
policies from the 2026-05-13 audit ([cmu87082_redo_data_audit.md]):
  - Quality gate: fit_converged AND alpha > 0.9 (strict)
  - S0178 sensitivity source: E049 raw slope at pH 8.48, channels 3/4/5/7 only
  - S0181/S0182/S0183 sensitivity source: E049 cohort_20260506 (matched
    linear_range=(0.1, 210) uM)
  - No cross-source slope mixing; per-source stratification on every ro

See ``_experiments/CMU.87.082.md`` Section 0 for the closeout-correction
context and ``claude_test_files/cmu87082_redo_data_audit.md`` for the
per-decision rationale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from src.vendor.electrochem_analysis.analysis.eis import EISAnalyzer, EISCircuitFitter
from src.vendor.electrochem_analysis.dataloaders import filter_scans, load_psession

logger = logging.getLogger(__name__)

# Locked policy: strict quality gate.
QUALITY_ALPHA_MIN = 0.9

# Locked policy: S0178 sensitivity from E049 pH 8.48, channels 3/4/5/7 only.
S0178_E049_CHANNELS: Tuple[int, ...] = (3, 4, 5, 7)
E049_PH_FOR_SENSITIVITY: float = 8.48

# Permutation-null seed (reproducibility for frequency-resolved analysis).
PERM_NULL_RNG_SEED = 20260513


# ---------------------------------------------------------------------------
# EIS loading + fitting
# ---------------------------------------------------------------------------


@dataclass
class ChannelEIS:
    """Single-channel pre + (optional) post EIS data plus fit results."""

    specimen: str
    channel: int
    pre_data: pd.DataFrame
    post_data: Optional[pd.DataFrame] = None
    pre_fit: Dict[str, float] = field(default_factory=dict)
    post_fit: Dict[str, float] = field(default_factory=dict)

    @property
    def has_post(self) -> bool:
        return self.post_data is not None and not self.post_data.empty


def load_paired_eis_for_specimen(
    specimen: str,
    pre_pssession: Optional[str],
    post_pssession: Optional[str],
) -> List[ChannelEIS]:
    """Load all EIS channels for one specimen as ChannelEIS records.

    Channel numbers are parsed from the scan-name suffix (e.g. ``EIS_Ch3``).
    Channels that exist in pre but not post are kept with ``post_data=None``;
    a post sweep with no matching pre is skipped with a warning.
    """
    if not pre_pssession:
        logger.info("Skipping %s: no pre-enzyme EIS path", specimen)
        return []

    pre_scans = filter_scans(load_psession(pre_pssession), "EIS")
    post_scans: Dict[str, pd.DataFrame] = {}
    if post_pssession:
        post_scans = filter_scans(load_psession(post_pssession), "EIS")

    records: List[ChannelEIS] = []
    for scan_name, pre_df in pre_scans.items():
        ch = _parse_channel(scan_name)
        if ch is None:
            logger.warning("Could not parse channel from %s, skipping", scan_name)
            continue

        # Match post scan by channel number (post scan names are typically
        # the same convention with a different timestamp prefix).
        post_df: Optional[pd.DataFrame] = None
        for post_name, candidate_df in post_scans.items():
            if _parse_channel(post_name) == ch:
                post_df = candidate_df
                break

        records.append(
            ChannelEIS(
                specimen=specimen,
                channel=ch,
                pre_data=pre_df,
                post_data=post_df,
            )
        )

    return records


def _parse_channel(scan_name: str) -> Optional[int]:
    """Parse channel number from a PalmSens scan name.

    Accepts patterns like ``EIS_Ch3``, ``EIS Ch 3``, ``EIS_Channel_03``;
    returns ``None`` if no channel marker is found.
    """
    import re

    # Common patterns observed in CMU.87.082 SPECIMENS scans.
    for pattern in (r"Ch(?:annel)?[_\s]*0*(\d+)", r"_(\d+)$"):
        match = re.search(pattern, scan_name, flags=re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def fit_channel(record: ChannelEIS) -> ChannelEIS:
    """Fit pre and (if available) post EIS for one channel.

    Stores fit results in ``record.pre_fit`` and ``record.post_fit``.
    Non-convergence and physical-sanity NaN guards are preserved verbatim
    from EISCircuitFitter; this function does NOT silently swallow them.
    """
    record.pre_fit = _fit_one(record.pre_data, scan_label=f"{record.specimen}_Ch{record.channel}_pre")
    if record.has_post:
        record.post_fit = _fit_one(record.post_data, scan_label=f"{record.specimen}_Ch{record.channel}_post")
    return record


def _fit_one(data: pd.DataFrame, scan_label: str) -> Dict[str, float]:
    """Run EISCircuitFitter on a single spectrum, return parameter dict.

    Returns a dict of NaN if the fitter raises (which it shouldn't for
    well-formed input, but does for empty / malformed DataFrames).
    """
    try:
        fitter = EISCircuitFitter(data, topology=EISCircuitFitter.CPE_RANDLES)
        fitter.scan_name = scan_label
        fitter.fit()
        return fitter.get_parameters()
    except Exception as exc:
        logger.warning("Fit failed for %s: %s", scan_label, exc)
        return {
            "R_s": np.nan, "R_ct": np.nan, "CPE_Y0": np.nan, "CPE_alpha": np.nan,
            "chi_sq": np.nan,
            "R_s_err": np.nan, "R_ct_err": np.nan, "CPE_Y0_err": np.nan,
            "CPE_alpha_err": np.nan,
        }


def fits_to_dataframe(records: List[ChannelEIS]) -> pd.DataFrame:
    """Flatten ChannelEIS records (with fit results) into a single DataFrame.

    One row per channel, columns include both pre_ and post_ parameter
    suffixes plus DR_ct / DR_s / Dalpha if post is present.
    """
    rows: List[Dict[str, object]] = []
    for r in records:
        row: Dict[str, object] = {
            "specimen": r.specimen,
            "channel": r.channel,
            "has_post": r.has_post,
        }
        # Pre fit params
        for k, v in r.pre_fit.items():
            row[f"pre_{k}"] = v
        # Post fit params + deltas
        if r.has_post:
            for k, v in r.post_fit.items():
                row[f"post_{k}"] = v
            row["dR_ct"] = (
                row["post_R_ct"] - row["pre_R_ct"]
                if np.isfinite(row.get("post_R_ct", np.nan)) and np.isfinite(row.get("pre_R_ct", np.nan))
                else np.nan
            )
            row["dR_s"] = (
                row["post_R_s"] - row["pre_R_s"]
                if np.isfinite(row.get("post_R_s", np.nan)) and np.isfinite(row.get("pre_R_s", np.nan))
                else np.nan
            )
            row["dalpha"] = (
                row["post_CPE_alpha"] - row["pre_CPE_alpha"]
                if np.isfinite(row.get("post_CPE_alpha", np.nan)) and np.isfinite(row.get("pre_CPE_alpha", np.nan))
                else np.nan
            )
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Quality gate
# ---------------------------------------------------------------------------


def apply_quality_gate(
    fits_df: pd.DataFrame,
    alpha_min: float = QUALITY_ALPHA_MIN,
    require_post: bool = False,
) -> pd.DataFrame:
    """Add an ``is_clean_pre`` / ``is_clean_post`` mask to the fits DataFrame.

    ``is_clean_pre`` = pre fit is finite and pre_CPE_alpha >= alpha_min.
    ``is_clean_post`` = same gate applied to post (NaN if no post data).
    ``is_clean_paired`` = both pre and post are clean (only meaningful
    when require_post=True or for DR_ct analyses).

    Does NOT drop rows; the caller decides which mask to apply.
    """
    out = fits_df.copy()

    pre_alpha_ok = out["pre_CPE_alpha"].between(alpha_min, 1.5, inclusive="both")
    pre_finite = out["pre_R_ct"].apply(np.isfinite) & out["pre_R_s"].apply(np.isfinite)
    out["is_clean_pre"] = pre_alpha_ok & pre_finite

    if "post_CPE_alpha" in out.columns:
        post_alpha_ok = out["post_CPE_alpha"].between(alpha_min, 1.5, inclusive="both")
        post_finite = out["post_R_ct"].apply(np.isfinite) & out["post_R_s"].apply(np.isfinite)
        out["is_clean_post"] = post_alpha_ok & post_finite
        out["is_clean_paired"] = out["is_clean_pre"] & out["is_clean_post"]
    else:
        out["is_clean_post"] = False
        out["is_clean_paired"] = False

    return out


def quality_summary_per_specimen(fits_df: pd.DataFrame) -> pd.DataFrame:
    """Per-specimen channel counts at each gate level for the audit table."""
    rows = []
    for spec, group in fits_df.groupby("specimen", sort=True):
        rows.append({
            "specimen": spec,
            "n_total": len(group),
            "n_pre_finite": int(group["pre_R_ct"].apply(np.isfinite).sum()),
            "n_pre_clean": int(group["is_clean_pre"].sum()),
            "n_post_clean": int(group["is_clean_post"].sum()),
            "n_paired_clean": int(group["is_clean_paired"].sum()),
            "alpha_pre_mean": float(group["pre_CPE_alpha"].mean()),
            "alpha_pre_min": float(group["pre_CPE_alpha"].min()),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sensitivity sources (locked per-channel policy)
# ---------------------------------------------------------------------------


def load_s0178_sensitivity_e049(
    e049_pssession: str,
    channels: Tuple[int, ...] = S0178_E049_CHANNELS,
    ph_filter: float = E049_PH_FOR_SENSITIVITY,
) -> pd.DataFrame:
    """S0178 GABA sensitivity from E049 raw slope at pH 8.48.

    LOCKED POLICY (2026-05-13 audit Decision 2): only E049, only the four
    canonical channels (3, 4, 5, 7), only the pH 8.48 sweep. No fallback
    to E050 or E069. Returns columns: specimen, channel, sensitivity_nA_per_uM,
    sensitivity_source.

    The pH filter is informational; the caller is responsible for ensuring
    the e049_pssession path points at the pH 8.48 sweep specifically.

    NOTE: this is a stub. The full E049 CA-loading + linear-fit logic lives
    elsewhere in the notebook; this function expects the caller to wire in
    the appropriate CSV / .pssession path. Adapt to your existing pattern.
    """
    # Placeholder structure; the actual implementation depends on how E049
    # sensitivity is currently extracted (CAAnalyzer.batch_analyze output,
    # CSV export, or inline recompute). The Phase 3 notebook will fill this
    # in using its existing E049 loader; we just shape the DataFrame here.
    logger.warning(
        "load_s0178_sensitivity_e049 is a wiring stub. The notebook "
        "should set this DataFrame directly from its E049 loader output."
    )
    return pd.DataFrame({
        "specimen": ["S0178"] * len(channels),
        "channel": list(channels),
        "sensitivity_nA_per_uM": [np.nan] * len(channels),
        "sensitivity_source": ["E049_pH8.48"] * len(channels),
    })


def load_cohort_20260506_sensitivity(
    cohort_export_dir: str | Path,
) -> pd.DataFrame:
    """S0181/S0182/S0183 GABA sensitivity from E049 cohort_20260506.

    Reads the CAAnalyzer batch_analyze output (linear_range=(0.1, 210) uM)
    from CMU.87.049 Section 11.45. Expected directory layout:

        cohort_export_dir/
            S0181/
                sensitivity_summary.csv  (one row per channel)
            S0182/
                ...
            S0183/
                ...

    Adapt the actual filename to match your existing export convention.
    Returns columns: specimen, channel, sensitivity_nA_per_uM,
    sensitivity_source.
    """
    cohort_dir = Path(cohort_export_dir)
    if not cohort_dir.exists():
        logger.warning(
            "Cohort export dir does not exist: %s (returning empty frame)",
            cohort_dir,
        )
        return pd.DataFrame(columns=["specimen", "channel", "sensitivity_nA_per_uM", "sensitivity_source"])

    frames = []
    for specimen in ("S0181", "S0182", "S0183"):
        spec_csv = cohort_dir / specimen / "sensitivity_summary.csv"
        if not spec_csv.exists():
            # Try a few common alternative paths the caller may use.
            for alt_name in ("ca_summary.csv", "calibration_summary.csv", "sensitivity.csv"):
                alt = cohort_dir / specimen / alt_name
                if alt.exists():
                    spec_csv = alt
                    break

        if not spec_csv.exists():
            logger.warning("No sensitivity CSV found for %s under %s", specimen, cohort_dir)
            continue

        df = pd.read_csv(spec_csv)
        df["specimen"] = specimen
        df["sensitivity_source"] = "E049_cohort_20260506"
        # The caller is responsible for column-name normalization in the
        # notebook; this loader is intentionally flexible.
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["specimen", "channel", "sensitivity_nA_per_uM", "sensitivity_source"])

    return pd.concat(frames, ignore_index=True)


def join_sensitivity(
    fits_df: pd.DataFrame,
    s0178_sens: pd.DataFrame,
    cohort_sens: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join sensitivity onto fits_df using the locked per-channel policy.

    S0178 channels match against s0178_sens (E049 only).
    S0181/S0182/S0183 channels match against cohort_sens (E049 cohort).
    Other specimens get NaN sensitivity.

    Returns fits_df with two new columns: sensitivity_nA_per_uM,
    sensitivity_source.
    """
    out = fits_df.copy()
    out["sensitivity_nA_per_uM"] = np.nan
    out["sensitivity_source"] = None

    # S0178 join
    s0178_lookup = {
        (row["specimen"], int(row["channel"])): (row["sensitivity_nA_per_uM"], row["sensitivity_source"])
        for _, row in s0178_sens.iterrows()
    }
    # Cohort join
    cohort_lookup = {
        (row["specimen"], int(row["channel"])): (row["sensitivity_nA_per_uM"], row["sensitivity_source"])
        for _, row in cohort_sens.iterrows()
    }

    for idx, row in out.iterrows():
        key = (row["specimen"], int(row["channel"]))
        if row["specimen"] == "S0178":
            hit = s0178_lookup.get(key)
        elif row["specimen"] in {"S0181", "S0182", "S0183"}:
            hit = cohort_lookup.get(key)
        else:
            hit = None

        if hit is not None:
            out.at[idx, "sensitivity_nA_per_uM"] = hit[0]
            out.at[idx, "sensitivity_source"] = hit[1]

    return out


# ---------------------------------------------------------------------------
# Statistics: Spearman with permutation null
# ---------------------------------------------------------------------------


def spearman_with_perm_null(
    x: np.ndarray,
    y: np.ndarray,
    n_perm: int = 10_000,
    seed: int = PERM_NULL_RNG_SEED,
) -> Dict[str, float]:
    """Spearman rho of x vs y plus a 95% permutation-null bound.

    Returns dict with keys: rho, p_two_sided, null_95 (the |rho| value
    that the permutation null exceeds with 95% probability), n.

    NaN-pairs are dropped before correlation. If fewer than 4 finite pairs
    remain, returns NaN/None values to flag insufficient data.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)

    if n < 4:
        return {"rho": np.nan, "p_two_sided": np.nan, "null_95": np.nan, "n": n}

    rho, p = stats.spearmanr(x, y)

    rng = np.random.default_rng(seed)
    perm_rhos = np.empty(n_perm)
    y_perm = y.copy()
    for i in range(n_perm):
        rng.shuffle(y_perm)
        perm_rho, _ = stats.spearmanr(x, y_perm)
        perm_rhos[i] = perm_rho if np.isfinite(perm_rho) else 0.0

    null_95 = float(np.quantile(np.abs(perm_rhos), 0.95))

    return {
        "rho": float(rho),
        "p_two_sided": float(p),
        "null_95": null_95,
        "n": n,
    }


# ---------------------------------------------------------------------------
# Frequency-resolved Spearman (re-runs Section 8 with corrected R_ct)
# ---------------------------------------------------------------------------


def frequency_resolved_spearman(
    records: List[ChannelEIS],
    sensitivity_lookup: Dict[Tuple[str, int], float],
    use_clean_mask: Optional[pd.Series] = None,
    component: str = "Z_imag",
    n_perm: int = 1_000,
) -> pd.DataFrame:
    """Per-frequency Spearman rho of EIS component vs sensitivity.

    Iterates over frequency bins in the pre-enzyme EIS spectra, builds the
    per-channel value at each frequency, correlates against sensitivity.

    component: which EIS quantity to correlate at each frequency.
        Supported: "Z_real", "Z_imag", "Z_mag", "phase".

    Returns DataFrame with columns: frequency_hz, rho, null_95, n_pairs.

    Permutation null defaults to n_perm=1000 here (lower than spearman_with_perm_null's
    default of 10k) because we compute it at ~40 frequency points; total
    permutations stay reasonable.
    """
    # Assume all pre-enzyme spectra share the same frequency grid (true
    # for the CMU.87.082 dataset; flag if not).
    if not records:
        return pd.DataFrame()

    ref_freq = records[0].pre_data["Frequency_Hz"].values
    rows = []

    for freq_idx, f in enumerate(ref_freq):
        per_channel_vals = []
        per_channel_sens = []
        for r in records:
            if use_clean_mask is not None and not use_clean_mask.get((r.specimen, r.channel), False):
                continue

            df = r.pre_data
            if freq_idx >= len(df):
                continue

            row = df.iloc[freq_idx]
            if component == "Z_real":
                val = row["Z_real_Ohm"]
            elif component == "Z_imag":
                val = row["Z_imag_Ohm"]
            elif component == "Z_mag":
                val = np.sqrt(row["Z_real_Ohm"] ** 2 + row["Z_imag_Ohm"] ** 2)
            elif component == "phase":
                val = np.degrees(np.arctan2(row["Z_imag_Ohm"], row["Z_real_Ohm"]))
            else:
                raise ValueError(f"Unknown component: {component}")

            sens = sensitivity_lookup.get((r.specimen, r.channel), np.nan)
            per_channel_vals.append(val)
            per_channel_sens.append(sens)

        result = spearman_with_perm_null(
            np.asarray(per_channel_vals),
            np.asarray(per_channel_sens),
            n_perm=n_perm,
        )
        rows.append({
            "frequency_hz": float(f),
            "rho": result["rho"],
            "null_95": result["null_95"],
            "n_pairs": result["n"],
            "component": component,
        })

    return pd.DataFrame(rows)
