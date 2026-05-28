"""Unit tests for profiler.profile.

Covers only the pure helper functions (no PySpark required):
    - shannon_entropy
    - _build_histogram
    - _fire_alerts
    - _determine_stereotypes
    - _mock_profile (writes stub HTML + returns valid DatasetProfile)

Spark-dependent paths (_databricks_profile, _numeric_stats, _categorical_stats,
_generate_html) are integration-tested in Databricks.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from profiler.catalog import TableRef, VolumeRef
from profiler.metamodel import (
    CategoricalStats,
    NumericStats,
    new_run_id,
)
from profiler.profile import (
    _build_histogram,
    _determine_stereotypes,
    _fire_alerts,
    _mock_profile,
    shannon_entropy,
)
from profiler.storage import RunFolder


# ---------------------------------------------------------------------------
# Helpers

def _num_stats(**kw) -> NumericStats:
    base = dict(
        min=0.0, max=100.0, mean=50.0, stddev=20.0,
        p1=1.0, p5=5.0, p25=25.0, p50=50.0, p75=75.0, p95=95.0, p99=99.0,
        skewness=0.2, kurtosis=2.8,
        histogram_edges=[0.0, 50.0, 100.0],
        histogram_counts=[40, 60],
    )
    base.update(kw)
    return NumericStats(**base)


def _cat_stats(**kw) -> CategoricalStats:
    base = dict(top_k={"A": 50, "B": 30, "C": 20}, entropy=1.5)
    base.update(kw)
    return CategoricalStats(**base)


def _run_folder(tmp_path: Path) -> RunFolder:
    vol = VolumeRef(catalog="dev", schema="test_main_profiler", volume="ab_runs")
    folder_path = str(tmp_path / "run_001")
    Path(folder_path).mkdir(parents=True, exist_ok=True)
    return RunFolder(
        volume=vol,
        run_id="2025-01-01_1200",
        folder_name="2025-01-01_1200__test-vs-prod__orders",
        path=folder_path,
    )


def _ref() -> TableRef:
    return TableRef(
        connection="DEV (single catalog POC)",
        catalog="dev",
        schema="prod_main_claims",
        table="medical_claim",
    )


# ---------------------------------------------------------------------------
# shannon_entropy


class TestShannonEntropy:
    def test_uniform_two_values(self):
        # Two equally likely values → entropy = 1.0 bit
        entropy = shannon_entropy({"A": 50, "B": 50}, total=100)
        assert math.isclose(entropy, 1.0, abs_tol=1e-6)

    def test_uniform_four_values(self):
        entropy = shannon_entropy({"A": 25, "B": 25, "C": 25, "D": 25}, total=100)
        assert math.isclose(entropy, 2.0, abs_tol=1e-6)

    def test_certain_single_value(self):
        # One value with all rows → entropy = 0
        assert shannon_entropy({"X": 100}, total=100) == 0.0

    def test_empty_top_k(self):
        assert shannon_entropy({}, total=100) == 0.0

    def test_zero_total(self):
        assert shannon_entropy({"A": 10}, total=0) == 0.0

    def test_skewed_distribution_lower_than_uniform(self):
        uniform = shannon_entropy({"A": 50, "B": 50}, total=100)
        skewed = shannon_entropy({"A": 90, "B": 10}, total=100)
        assert skewed < uniform

    def test_counts_with_zeros_ignored(self):
        # Zero-count entries should not affect entropy
        e1 = shannon_entropy({"A": 50, "B": 50}, total=100)
        e2 = shannon_entropy({"A": 50, "B": 50, "C": 0}, total=100)
        assert math.isclose(e1, e2, abs_tol=1e-6)


# ---------------------------------------------------------------------------
# _build_histogram


class TestBuildHistogram:
    def test_basic_shape(self):
        bucket_data = {0: 10, 1: 20, 2: 30, 3: 15, 4: 25}
        edges, counts = _build_histogram(bucket_data, n_bins=5, lo=0.0, hi=100.0)
        assert len(edges) == len(counts) + 1
        assert len(counts) == 5

    def test_counts_match_buckets(self):
        bucket_data = {0: 10, 2: 30}  # bucket 1 is absent
        _, counts = _build_histogram(bucket_data, n_bins=3, lo=0.0, hi=30.0)
        assert counts == [10, 0, 30]

    def test_edges_start_and_end(self):
        edges, _ = _build_histogram({}, n_bins=4, lo=5.0, hi=25.0)
        assert edges[0] == pytest.approx(5.0)
        assert edges[-1] == pytest.approx(25.0)

    def test_edge_count_one_more_than_counts(self):
        for n in [5, 10, 20]:
            edges, counts = _build_histogram({}, n_bins=n, lo=0.0, hi=1.0)
            assert len(edges) == n + 1
            assert len(counts) == n

    def test_equal_width_bins(self):
        edges, _ = _build_histogram({}, n_bins=5, lo=0.0, hi=50.0)
        steps = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
        for s in steps:
            assert s == pytest.approx(10.0)

    def test_missing_buckets_fill_zero(self):
        # Only bucket 1 populated; 0 and 2 should be 0
        bucket_data = {1: 42}
        _, counts = _build_histogram(bucket_data, n_bins=3, lo=0.0, hi=3.0)
        assert counts[0] == 0
        assert counts[1] == 42
        assert counts[2] == 0


# ---------------------------------------------------------------------------
# _fire_alerts


class TestFireAlerts:
    def _base(self, **kw):
        defaults = dict(
            col_name="col_x",
            null_pct=0.0, null_count=0,
            distinct_count=50, distinct_pct=0.5,
            row_count=100,
            numeric=None, categorical=None,
        )
        defaults.update(kw)
        return _fire_alerts(**defaults)

    def test_no_alerts_clean_column(self):
        alerts = self._base()
        assert alerts == []

    def test_missing_warn_above_50_pct(self):
        alerts = self._base(null_pct=0.6, null_count=60)
        rules = [a.rule for a in alerts]
        assert "missing" in rules
        missing = next(a for a in alerts if a.rule == "missing")
        assert missing.severity == "warn"

    def test_missing_critical_above_80_pct(self):
        alerts = self._base(null_pct=0.85, null_count=85)
        missing = next(a for a in alerts if a.rule == "missing")
        assert missing.severity == "critical"

    def test_entirely_null_column(self):
        alerts = self._base(null_pct=1.0, null_count=100)
        rules = [a.rule for a in alerts]
        assert "missing" in rules
        # Should return early — no other alerts
        assert len(alerts) == 1

    def test_constant_alert(self):
        alerts = self._base(distinct_count=1)
        rules = [a.rule for a in alerts]
        assert "constant" in rules

    def test_high_cardinality_categorical(self):
        cat = _cat_stats()
        alerts = self._base(distinct_count=95, distinct_pct=0.95, categorical=cat)
        rules = [a.rule for a in alerts]
        assert "high_cardinality" in rules

    def test_unique_alert_categorical(self):
        cat = _cat_stats()
        alerts = self._base(
            distinct_count=100, distinct_pct=1.0,
            categorical=cat, row_count=100,
        )
        rules = [a.rule for a in alerts]
        assert "unique" in rules

    def test_skewed_alert_numeric(self):
        num = _num_stats(skewness=3.5)
        alerts = self._base(numeric=num)
        rules = [a.rule for a in alerts]
        assert "skewed" in rules

    def test_no_skewed_alert_below_threshold(self):
        num = _num_stats(skewness=1.5)
        alerts = self._base(numeric=num)
        assert not any(a.rule == "skewed" for a in alerts)

    def test_imbalanced_alert_categorical(self):
        cat = _cat_stats(top_k={"A": 95, "B": 5})
        alerts = self._base(categorical=cat, row_count=100)
        rules = [a.rule for a in alerts]
        assert "imbalanced" in rules

    def test_no_imbalanced_alert_balanced_categorical(self):
        cat = _cat_stats(top_k={"A": 50, "B": 50})
        alerts = self._base(categorical=cat, row_count=100)
        assert not any(a.rule == "imbalanced" for a in alerts)

    def test_no_numeric_alerts_on_categorical_column(self):
        cat = _cat_stats()
        alerts = self._base(categorical=cat)
        assert not any(a.rule == "skewed" for a in alerts)


# ---------------------------------------------------------------------------
# _determine_stereotypes


class TestDetermineStereotypes:
    def _base(self, **kw):
        defaults = dict(
            null_pct=0.0,
            distinct_count=50, distinct_pct=0.5,
            row_count=100,
            numeric=None, categorical=None,
        )
        defaults.update(kw)
        return _determine_stereotypes(**defaults)

    def test_clean_column_no_stereotypes(self):
        assert self._base() == []

    def test_constant_stereotype(self):
        assert "Constant" in self._base(distinct_count=1)

    def test_null_spike_stereotype(self):
        assert "NullSpike" in self._base(null_pct=0.6)

    def test_high_cardinality_stereotype_categorical(self):
        cat = _cat_stats()
        result = self._base(distinct_pct=0.95, categorical=cat)
        assert "HighCardinality" in result

    def test_high_cardinality_not_applied_to_numeric(self):
        num = _num_stats()
        result = self._base(distinct_pct=0.95, numeric=num)
        assert "HighCardinality" not in result

    def test_skewed_stereotype(self):
        num = _num_stats(skewness=3.5)
        assert "Skewed" in self._base(numeric=num)

    def test_skewed_below_threshold_no_stereotype(self):
        num = _num_stats(skewness=1.8)
        assert "Skewed" not in self._base(numeric=num)

    def test_imbalanced_stereotype(self):
        cat = _cat_stats(top_k={"A": 95, "B": 5})
        result = self._base(categorical=cat)
        assert "Imbalanced" in result

    def test_multiple_stereotypes(self):
        cat = _cat_stats(top_k={"A": 95, "B": 5})
        result = self._base(null_pct=0.55, categorical=cat, distinct_count=2)
        assert "NullSpike" in result
        assert "Imbalanced" in result


# ---------------------------------------------------------------------------
# _mock_profile


class TestMockProfile:
    def test_returns_valid_dataset_profile(self, tmp_path):
        os.environ["PROFILER_RUNTIME"] = "mock"
        folder = _run_folder(tmp_path)
        ref = _ref()
        profile = _mock_profile(ref, "PROD", folder, "profile_a.html")
        assert profile.catalog == "dev"
        assert profile.schema_ == "prod_main_claims"
        assert profile.table == "medical_claim"
        assert profile.env_label == "PROD"
        assert profile.row_count == 0
        assert profile.column_count == 0
        assert profile.columns == []

    def test_writes_stub_html(self, tmp_path):
        folder = _run_folder(tmp_path)
        _mock_profile(_ref(), "PROD", folder, "profile_a.html")
        html_path = Path(folder.path) / "profile_a.html"
        assert html_path.exists()
        content = html_path.read_text()
        assert "Mock Profile" in content
        assert "prod_main_claims" in content
        assert "medical_claim" in content

    def test_fqn_in_stub_html(self, tmp_path):
        folder = _run_folder(tmp_path)
        ref = TableRef(
            connection="local",
            catalog="dev",
            schema="test_main_claims",
            table="pharmacy_claim",
        )
        _mock_profile(ref, "TEST", folder, "profile_b.html")
        html_path = Path(folder.path) / "profile_b.html"
        content = html_path.read_text()
        assert "test_main_claims" in content
        assert "pharmacy_claim" in content
