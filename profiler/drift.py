"""Distribution-drift metrics (milestone 4).

All computation works from the pre-computed histogram / top-K data already
stored in NumericStats and CategoricalStats — no Spark re-read required.

Metrics:
    PSI (Population Stability Index)   — numeric + categorical
    KS statistic + p-value             — numeric only
    Chi-square statistic               — categorical only
    JS divergence (bits, [0,1])        — numeric + categorical

Public API:
    compute_column_drift(col_a, col_b) -> DriftResult
        Dispatches to numeric or categorical path based on column type.

    psi_from_counts(counts_a, counts_b) -> float
    rebin_counts(src_edges, src_counts, tgt_edges) -> list[float]
    ks_from_histograms(edges, counts_a, counts_b) -> (stat, pvalue)
    chi_square_from_freqs(freqs_a, freqs_b) -> (stat, pvalue)
    js_divergence_from_counts(counts_a, counts_b) -> float

PSI thresholds (from metamodel):
    < 0.10  → stable
    0.10 ≤  → moderate
    0.20 ≤  → significant
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .metamodel import ColumnProfile


# ---------------------------------------------------------------------------
# Result container


@dataclass
class DriftResult:
    psi: Optional[float] = None
    ks_stat: Optional[float] = None
    ks_pvalue: Optional[float] = None
    chi_square: Optional[float] = None
    js_divergence: Optional[float] = None


# ---------------------------------------------------------------------------
# Public dispatch


def compute_column_drift(
    col_a: "ColumnProfile",
    col_b: "ColumnProfile",
) -> DriftResult:
    """Compute all applicable drift metrics for a matched column pair.

    Numeric  → PSI + KS + JS divergence
    Categorical → PSI + Chi-square + JS divergence
    Temporal / no stats → empty DriftResult
    """
    if col_a.numeric is not None and col_b.numeric is not None:
        return _numeric_drift(col_a.numeric, col_b.numeric)

    if col_a.categorical is not None and col_b.categorical is not None:
        total_a = col_a.null_count + sum(col_a.categorical.top_k.values())
        total_b = col_b.null_count + sum(col_b.categorical.top_k.values())
        return _categorical_drift(
            col_a.categorical, col_b.categorical, total_a, total_b
        )

    return DriftResult()


# ---------------------------------------------------------------------------
# Numeric drift path


def _numeric_drift(num_a, num_b) -> DriftResult:
    # Rebin B onto A's edges so both distributions use the same bins.
    counts_b_rebinned = rebin_counts(
        num_b.histogram_edges,
        num_b.histogram_counts,
        num_a.histogram_edges,
    )

    counts_a_f = [float(c) for c in num_a.histogram_counts]

    psi_val = psi_from_counts(counts_a_f, counts_b_rebinned)
    ks_s, ks_p = ks_from_histograms(
        num_a.histogram_edges,
        num_a.histogram_counts,
        [max(0, round(c)) for c in counts_b_rebinned],
    )
    js_val = js_divergence_from_counts(counts_a_f, counts_b_rebinned)

    return DriftResult(
        psi=round(psi_val, 6),
        ks_stat=round(ks_s, 6) if ks_s is not None else None,
        ks_pvalue=round(ks_p, 6) if ks_p is not None else None,
        js_divergence=round(js_val, 6),
    )


# ---------------------------------------------------------------------------
# Categorical drift path


def _categorical_drift(cat_a, cat_b, total_a: int, total_b: int) -> DriftResult:
    all_cats = sorted(set(cat_a.top_k) | set(cat_b.top_k))
    if not all_cats:
        return DriftResult()

    counts_a = [float(cat_a.top_k.get(c, 0)) for c in all_cats]
    counts_b = [float(cat_b.top_k.get(c, 0)) for c in all_cats]

    psi_val = psi_from_counts(counts_a, counts_b)
    js_val = js_divergence_from_counts(counts_a, counts_b)
    chi_s, _ = chi_square_from_freqs(counts_a, counts_b)

    return DriftResult(
        psi=round(psi_val, 6),
        chi_square=round(chi_s, 6) if chi_s is not None else None,
        js_divergence=round(js_val, 6),
    )


# ---------------------------------------------------------------------------
# Core metric functions — all pure Python, testable without Spark


def psi_from_counts(
    counts_a: list[float],
    counts_b: list[float],
    epsilon: float = 1e-4,
) -> float:
    """Population Stability Index between reference (A) and actual (B).

    Epsilon smoothing prevents log(0) when a bin is empty on one side.
    Identical distributions always yield PSI = 0.
    """
    total_a = sum(counts_a)
    total_b = sum(counts_b)
    if total_a == 0 or total_b == 0:
        return 0.0

    n = len(counts_a)
    psi = 0.0
    for ca, cb in zip(counts_a, counts_b):
        p = (ca + epsilon) / (total_a + n * epsilon)
        q = (cb + epsilon) / (total_b + n * epsilon)
        psi += (q - p) * math.log(q / p)

    return max(0.0, psi)


def rebin_counts(
    src_edges: list[float],
    src_counts: list[int],
    tgt_edges: list[float],
) -> list[float]:
    """Redistribute src histogram counts onto tgt bin edges.

    Assumes uniform distribution within each source bin.
    Preserves total count (up to floating-point error).
    """
    n_tgt = len(tgt_edges) - 1
    result = [0.0] * n_tgt

    for i, count in enumerate(src_counts):
        if count == 0:
            continue
        bin_lo = src_edges[i]
        bin_hi = src_edges[i + 1]
        bin_width = bin_hi - bin_lo
        if bin_width <= 0:
            continue

        for j in range(n_tgt):
            tgt_lo = tgt_edges[j]
            tgt_hi = tgt_edges[j + 1]
            overlap = max(0.0, min(bin_hi, tgt_hi) - max(bin_lo, tgt_lo))
            if overlap > 0:
                result[j] += count * overlap / bin_width

    return result


def ks_from_histograms(
    edges: list[float],
    counts_a: list[int],
    counts_b: list[int],
    max_pseudo_samples: int = 10_000,
) -> tuple[Optional[float], Optional[float]]:
    """Approximate two-sample KS test from aligned histograms.

    Expands each bin to `count` copies of the bin midpoint (capped at
    max_pseudo_samples per side) then runs scipy.stats.ks_2samp.
    """
    total_a = sum(counts_a)
    total_b = sum(counts_b)
    if total_a == 0 or total_b == 0:
        return None, None

    from scipy.stats import ks_2samp

    midpoints = [(edges[i] + edges[i + 1]) / 2.0 for i in range(len(counts_a))]

    def _expand(counts: list[int], total: int) -> list[float]:
        scale = min(1.0, max_pseudo_samples / max(total, 1))
        samples: list[float] = []
        for mid, c in zip(midpoints, counts):
            n = max(1, round(c * scale)) if c > 0 else 0
            samples.extend([mid] * n)
        return samples

    a_samples = _expand(counts_a, total_a)
    b_samples = _expand(counts_b, total_b)

    if not a_samples or not b_samples:
        return None, None

    result = ks_2samp(a_samples, b_samples)
    return float(result.statistic), float(result.pvalue)


def chi_square_from_freqs(
    freqs_a: list[float],
    freqs_b: list[float],
) -> tuple[Optional[float], Optional[float]]:
    """Chi-square goodness-of-fit test: does B match A's distribution?

    A is the reference (expected); B is the observed.
    Returns (statistic, p_value).  Returns (None, None) when the test
    cannot be computed (all-zero expected or fewer than 2 categories).
    """
    from scipy.stats import chisquare

    total_a = sum(freqs_a)
    total_b = sum(freqs_b)
    if total_a == 0 or total_b == 0:
        return None, None

    # Scale A to match B's total.
    expected = [fa / total_a * total_b for fa in freqs_a]

    # Filter bins where expected == 0 (undefined chi-square contribution).
    pairs = [(ob, ex) for ob, ex in zip(freqs_b, expected) if ex > 0]
    if len(pairs) < 2:
        return None, None

    obs, exp = zip(*pairs)
    try:
        stat, p = chisquare(list(obs), list(exp))
        return float(stat), float(p)
    except Exception:
        return None, None


def js_divergence_from_counts(
    counts_a: list[float],
    counts_b: list[float],
) -> float:
    """Jensen-Shannon divergence in bits [0, 1].

    Uses log base 2; identical distributions return 0.0; maximally
    different (non-overlapping) distributions return 1.0.
    """
    total_a = sum(counts_a)
    total_b = sum(counts_b)
    if total_a == 0 or total_b == 0:
        return 0.0

    p = [c / total_a for c in counts_a]
    q = [c / total_b for c in counts_b]
    m = [(pi + qi) / 2 for pi, qi in zip(p, q)]

    def _kl_term(a: float, b: float) -> float:
        if a <= 0 or b <= 0:
            return 0.0
        return a * math.log2(a / b)

    kl_pm = sum(_kl_term(pi, mi) for pi, mi in zip(p, m))
    kl_qm = sum(_kl_term(qi, mi) for qi, mi in zip(q, m))

    return min(1.0, max(0.0, (kl_pm + kl_qm) / 2))
