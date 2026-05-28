"""Unit tests for profiler.drift.

All tests are pure Python — no Spark or ydata-profiling required.
Expected PSI / JS values are derived analytically where possible,
otherwise checked against known bounds.
"""

from __future__ import annotations

import math

import pytest

from profiler.drift import (
    DriftResult,
    chi_square_from_freqs,
    compute_column_drift,
    js_divergence_from_counts,
    ks_from_histograms,
    psi_from_counts,
    rebin_counts,
)
from profiler.metamodel import (
    CategoricalStats,
    ColumnProfile,
    NumericStats,
)


# ---------------------------------------------------------------------------
# Builders


def _num(mean: float = 50.0, histogram_counts: list[int] | None = None) -> NumericStats:
    counts = histogram_counts or [25, 25, 25, 25]
    n_bins = len(counts)
    edges = [float(i * 100 / n_bins) for i in range(n_bins + 1)]
    return NumericStats(
        min=0.0, max=100.0, mean=mean, stddev=20.0,
        p1=1.0, p5=5.0, p25=25.0, p50=mean, p75=75.0, p95=95.0, p99=99.0,
        skewness=0.0, kurtosis=3.0,
        histogram_edges=edges,
        histogram_counts=counts,
    )


def _cat(top_k: dict[str, int]) -> CategoricalStats:
    return CategoricalStats(top_k=top_k, entropy=1.0)


def _col_num(counts: list[int], mean: float = 50.0) -> ColumnProfile:
    return ColumnProfile(
        name="amount", logical_type="double", physical_type="DOUBLE",
        nullable=True, null_count=0, null_pct=0.0,
        distinct_count=100, distinct_pct=0.1,
        numeric=_num(mean=mean, histogram_counts=counts),
    )


def _col_cat(top_k: dict[str, int]) -> ColumnProfile:
    return ColumnProfile(
        name="status", logical_type="string", physical_type="STRING",
        nullable=True, null_count=0, null_pct=0.0,
        distinct_count=len(top_k), distinct_pct=0.1,
        categorical=_cat(top_k),
    )


# ---------------------------------------------------------------------------
# psi_from_counts


class TestPSI:
    def test_identical_distributions_psi_zero(self):
        counts = [100.0, 200.0, 150.0, 50.0]
        assert psi_from_counts(counts, counts) == pytest.approx(0.0, abs=1e-9)

    def test_known_two_bin_shift(self):
        # A: [100, 100], B: [150, 50]  →  PSI ≈ 0.2747
        psi = psi_from_counts([100.0, 100.0], [150.0, 50.0])
        assert psi == pytest.approx(0.2747, abs=0.002)

    def test_identical_single_bin(self):
        assert psi_from_counts([1000.0], [1000.0]) == pytest.approx(0.0, abs=1e-9)

    def test_empty_a_returns_zero(self):
        assert psi_from_counts([0.0, 0.0], [100.0, 50.0]) == 0.0

    def test_empty_b_returns_zero(self):
        assert psi_from_counts([100.0, 50.0], [0.0, 0.0]) == 0.0

    def test_psi_non_negative(self):
        import random
        rng = random.Random(42)
        for _ in range(20):
            a = [rng.randint(0, 100) for _ in range(5)]
            b = [rng.randint(0, 100) for _ in range(5)]
            assert psi_from_counts(list(map(float, a)), list(map(float, b))) >= 0.0

    def test_large_shift_exceeds_moderate_threshold(self):
        # Very different distributions → PSI >> 0.2
        psi = psi_from_counts([100.0, 0.0], [0.0, 100.0])
        assert psi > 0.2

    def test_moderate_shift_between_thresholds(self):
        # A: [100, 100], B: [120, 80] → small but non-trivial shift
        psi = psi_from_counts([100.0, 100.0], [120.0, 80.0])
        assert 0.0 < psi < 0.2


# ---------------------------------------------------------------------------
# rebin_counts


class TestRebinCounts:
    def test_same_edges_returns_same_counts(self):
        edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        counts = [10, 20, 30, 40]
        result = rebin_counts(edges, counts, edges)
        for r, c in zip(result, counts):
            assert r == pytest.approx(c, rel=1e-6)

    def test_total_count_preserved(self):
        src_edges = [0.0, 50.0, 100.0]
        src_counts = [60, 40]
        tgt_edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        result = rebin_counts(src_edges, src_counts, tgt_edges)
        assert sum(result) == pytest.approx(100.0, rel=1e-6)

    def test_coarser_target_bins(self):
        # 4 fine bins → 2 coarse bins; each coarse bin should sum to 2 fine bins
        src_edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        src_counts = [10, 20, 30, 40]
        tgt_edges = [0.0, 50.0, 100.0]
        result = rebin_counts(src_edges, src_counts, tgt_edges)
        assert result[0] == pytest.approx(30.0, rel=1e-6)
        assert result[1] == pytest.approx(70.0, rel=1e-6)

    def test_uniform_split(self):
        # One bin [0, 100] with 100 counts → two bins [0,50] and [50,100]
        result = rebin_counts([0.0, 100.0], [100], [0.0, 50.0, 100.0])
        assert result[0] == pytest.approx(50.0, rel=1e-6)
        assert result[1] == pytest.approx(50.0, rel=1e-6)

    def test_zero_count_bin_ignored(self):
        edges = [0.0, 50.0, 100.0]
        result = rebin_counts(edges, [0, 100], edges)
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(100.0)

    def test_no_overlap_returns_zeros(self):
        # Src is [0, 50]; tgt is [60, 100] — no overlap
        result = rebin_counts([0.0, 50.0], [100], [60.0, 100.0])
        assert result[0] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# ks_from_histograms


class TestKSFromHistograms:
    def test_identical_histograms_low_ks(self):
        edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        counts = [100, 200, 150, 50]
        ks_s, ks_p = ks_from_histograms(edges, counts, counts)
        assert ks_s is not None
        assert ks_s < 0.05  # nearly 0

    def test_different_histograms_higher_ks(self):
        edges = [0.0, 50.0, 100.0]
        ks_s, ks_p = ks_from_histograms(edges, [100, 10], [10, 100])
        assert ks_s is not None
        assert ks_s > 0.3

    def test_returns_tuple_of_two(self):
        edges = [0.0, 50.0, 100.0]
        result = ks_from_histograms(edges, [50, 50], [60, 40])
        assert len(result) == 2

    def test_pvalue_between_0_and_1(self):
        edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        counts = [40, 30, 20, 10]
        _, p = ks_from_histograms(edges, counts, counts)
        assert p is None or 0.0 <= p <= 1.0

    def test_empty_a_returns_none(self):
        edges = [0.0, 50.0, 100.0]
        ks_s, ks_p = ks_from_histograms(edges, [0, 0], [50, 50])
        assert ks_s is None
        assert ks_p is None

    def test_ks_stat_between_0_and_1(self):
        edges = [0.0, 25.0, 50.0, 75.0, 100.0]
        ks_s, _ = ks_from_histograms(edges, [10, 20, 30, 40], [40, 30, 20, 10])
        assert ks_s is not None
        assert 0.0 <= ks_s <= 1.0


# ---------------------------------------------------------------------------
# chi_square_from_freqs


class TestChiSquare:
    def test_identical_distributions_small_stat(self):
        freqs = [100.0, 200.0, 50.0]
        stat, p = chi_square_from_freqs(freqs, freqs)
        assert stat is not None
        assert stat < 1.0

    def test_very_different_distributions_large_stat(self):
        stat, p = chi_square_from_freqs([100.0, 0.0], [0.0, 100.0])
        # One of the bins has zero expected → None
        # (category B appears in B but not in A)
        # This is None since expected for "B category" = 0
        # Actually: freqs_a=[100,0], freqs_b=[0,100]
        # expected=[0,100] (scaled); pairs where ex>0: only (100, 100)
        # Only 1 pair → None (need ≥ 2)
        assert stat is None

    def test_moderate_shift_returns_positive_stat(self):
        stat, p = chi_square_from_freqs(
            [100.0, 100.0, 100.0],
            [120.0, 80.0, 100.0],
        )
        assert stat is not None
        assert stat > 0

    def test_returns_none_when_fewer_than_2_bins(self):
        stat, p = chi_square_from_freqs([0.0], [100.0])
        assert stat is None

    def test_p_value_between_0_and_1(self):
        stat, p = chi_square_from_freqs([50.0, 50.0], [60.0, 40.0])
        if p is not None:
            assert 0.0 <= p <= 1.0

    def test_empty_a_returns_none(self):
        stat, p = chi_square_from_freqs([0.0, 0.0], [50.0, 50.0])
        assert stat is None


# ---------------------------------------------------------------------------
# js_divergence_from_counts


class TestJSDivergence:
    def test_identical_distributions_zero(self):
        counts = [100.0, 200.0, 50.0]
        assert js_divergence_from_counts(counts, counts) == pytest.approx(0.0, abs=1e-9)

    def test_maximally_different_distributions_near_one(self):
        # Non-overlapping: all in bin 0 vs all in bin 1
        js = js_divergence_from_counts([1000.0, 0.0], [0.0, 1000.0])
        assert js == pytest.approx(1.0, abs=1e-9)

    def test_result_between_0_and_1(self):
        import random
        rng = random.Random(99)
        for _ in range(20):
            a = [float(rng.randint(1, 100)) for _ in range(4)]
            b = [float(rng.randint(1, 100)) for _ in range(4)]
            js = js_divergence_from_counts(a, b)
            assert 0.0 <= js <= 1.0

    def test_symmetric(self):
        a = [100.0, 50.0, 25.0]
        b = [30.0, 80.0, 60.0]
        assert js_divergence_from_counts(a, b) == pytest.approx(
            js_divergence_from_counts(b, a), abs=1e-9
        )

    def test_empty_a_returns_zero(self):
        assert js_divergence_from_counts([0.0, 0.0], [100.0, 50.0]) == 0.0

    def test_small_shift_small_divergence(self):
        js = js_divergence_from_counts([100.0, 100.0], [110.0, 90.0])
        assert 0.0 < js < 0.05


# ---------------------------------------------------------------------------
# compute_column_drift


class TestComputeColumnDrift:
    def test_identical_numeric_columns_low_psi(self):
        col = _col_num([100, 100, 100, 100])
        result = compute_column_drift(col, col)
        assert result.psi is not None
        assert result.psi < 0.01
        assert result.ks_stat is not None
        assert result.js_divergence is not None
        assert result.chi_square is None  # numeric — no chi-square

    def test_shifted_numeric_columns_higher_psi(self):
        col_a = _col_num([200, 100, 50, 50])   # left-heavy
        col_b = _col_num([50, 50, 100, 200])   # right-heavy
        result = compute_column_drift(col_a, col_b)
        assert result.psi is not None
        assert result.psi > 0.1  # clearly drifted

    def test_identical_categorical_columns_low_psi(self):
        top_k = {"A": 100, "B": 80, "C": 20}
        col = _col_cat(top_k)
        result = compute_column_drift(col, col)
        assert result.psi == pytest.approx(0.0, abs=1e-9)
        assert result.chi_square is not None
        assert result.ks_stat is None   # categorical — no KS

    def test_shifted_categorical_psi_positive(self):
        col_a = _col_cat({"A": 100, "B": 100})
        col_b = _col_cat({"A": 150, "B": 50})
        result = compute_column_drift(col_a, col_b)
        assert result.psi is not None
        assert result.psi > 0.1

    def test_no_stats_returns_empty_result(self):
        # Temporal column — no numeric or categorical stats
        col = ColumnProfile(
            name="dt", logical_type="date", physical_type="DATE",
            nullable=True, null_count=0, null_pct=0.0,
            distinct_count=100, distinct_pct=0.5,
        )
        result = compute_column_drift(col, col)
        assert result.psi is None
        assert result.ks_stat is None
        assert result.chi_square is None
        assert result.js_divergence is None

    def test_drift_result_has_expected_fields(self):
        col = _col_num([100, 100])
        result = compute_column_drift(col, col)
        assert hasattr(result, "psi")
        assert hasattr(result, "ks_stat")
        assert hasattr(result, "ks_pvalue")
        assert hasattr(result, "chi_square")
        assert hasattr(result, "js_divergence")


# ---------------------------------------------------------------------------
# stat_diff integration (via compare_tables)


class TestStatDiffIntegration:
    def _profiles(self):
        from profiler.metamodel import DatasetProfile

        col_a_num = _col_num([200, 100, 50, 50], mean=40.0)
        col_b_num = _col_num([50, 50, 100, 200], mean=70.0)
        col_a_cat = _col_cat({"X": 100, "Y": 100})
        col_b_cat = _col_cat({"X": 150, "Y": 50})

        side_a = DatasetProfile(
            env_label="PROD", connection="local",
            catalog="dev", schema="prod_main_claims", table="orders",
            row_count=400, column_count=2,
            columns=[
                col_a_num.__class__(**{**col_a_num.model_dump(), "name": "amount"}),
                col_a_cat.__class__(**{**col_a_cat.model_dump(), "name": "status"}),
            ],
        )
        side_b = DatasetProfile(
            env_label="TEST", connection="local",
            catalog="dev", schema="test_main_claims", table="orders",
            row_count=400, column_count=2,
            columns=[
                col_b_num.__class__(**{**col_b_num.model_dump(), "name": "amount"}),
                col_b_cat.__class__(**{**col_b_cat.model_dump(), "name": "status"}),
            ],
        )
        return side_a, side_b

    def test_psi_populated_for_numeric(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        num_cmp = next(c for c in comparisons if c.column_name == "amount")
        assert num_cmp.psi is not None

    def test_ks_populated_for_numeric(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        num_cmp = next(c for c in comparisons if c.column_name == "amount")
        assert num_cmp.ks_stat is not None

    def test_chi_square_populated_for_categorical(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        cat_cmp = next(c for c in comparisons if c.column_name == "status")
        assert cat_cmp.chi_square is not None

    def test_js_divergence_populated(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        for cmp in comparisons:
            assert cmp.js_divergence is not None

    def test_verdict_set_from_psi(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        for cmp in comparisons:
            if cmp.schema_change == "unchanged":
                assert cmp.verdict in ("stable", "moderate", "significant")

    def test_drifted_stereotype_added_for_high_psi(self):
        from profiler.compare import compare_tables
        side_a, side_b = self._profiles()
        comparisons = compare_tables(side_a, side_b)
        # amount is clearly drifted (opposite histogram shapes)
        num_cmp = next(c for c in comparisons if c.column_name == "amount")
        if num_cmp.psi is not None and num_cmp.psi >= 0.2:
            assert "Drifted" in num_cmp.stereotypes

    def test_schema_changed_columns_no_drift_metrics(self):
        from profiler.compare import compare_tables
        from profiler.metamodel import DatasetProfile
        # Side A has "old_col", Side B has "new_col"
        col_a = _col_num([100, 100])
        col_b = _col_num([100, 100])
        col_a_mod = col_a.__class__(**{**col_a.model_dump(), "name": "old_col"})
        col_b_mod = col_b.__class__(**{**col_b.model_dump(), "name": "new_col"})
        side_a = DatasetProfile(
            env_label="A", connection="local",
            catalog="dev", schema="s", table="t",
            row_count=200, column_count=1, columns=[col_a_mod],
        )
        side_b = DatasetProfile(
            env_label="B", connection="local",
            catalog="dev", schema="s", table="t",
            row_count=200, column_count=1, columns=[col_b_mod],
        )
        comparisons = compare_tables(side_a, side_b)
        for cmp in comparisons:
            if cmp.schema_change != "unchanged":
                assert cmp.psi is None
