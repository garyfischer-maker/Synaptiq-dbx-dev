"""Unit tests for profiler.metamodel.

Covers:
- Field-level validation (ranges, non-negative, type exclusivity)
- Histogram structural invariants
- Verdict ↔ PSI consistency
- Round-trip JSON serialization (object → JSON → object)
- Schema export shape
- UUIDv7 properties
- Forward-compatibility (extra fields are dropped silently)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from profiler.metamodel import (
    HISTOGRAM_MAX_BINS,
    METAMODEL_VERSION,
    PSI_MODERATE_MAX,
    PSI_STABLE_MAX,
    Alert,
    CategoricalStats,
    ColumnComparison,
    ColumnProfile,
    DatasetProfile,
    Lineage,
    NumericStats,
    ProfilerRun,
    new_run_id,
    schema_for_current_version,
    verdict_from_psi,
)


# ---------------------------------------------------------------------------
# Builders — keep tests readable by hiding required-field plumbing

def _numeric_stats(**overrides) -> NumericStats:
    defaults = dict(
        min=0.0, max=100.0, mean=50.0, stddev=20.0,
        p1=1.0, p5=5.0, p25=25.0, p50=50.0, p75=75.0, p95=95.0, p99=99.0,
        skewness=0.1, kurtosis=2.8,
        histogram_edges=[0.0, 25.0, 50.0, 75.0, 100.0],
        histogram_counts=[10, 30, 30, 10],
    )
    defaults.update(overrides)
    return NumericStats(**defaults)


def _column(name: str = "col_x", **overrides) -> ColumnProfile:
    defaults = dict(
        name=name,
        logical_type="integer", physical_type="INT",
        nullable=True, null_count=0, null_pct=0.0,
        distinct_count=100, distinct_pct=0.1,
    )
    defaults.update(overrides)
    return ColumnProfile(**defaults)


def _dataset(env: str = "TEST", n_cols: int = 2, **overrides) -> DatasetProfile:
    cols = overrides.pop("columns", [_column(name=f"c{i}") for i in range(n_cols)])
    defaults = dict(
        env_label=env, connection="local",
        catalog="test_main", schema="sales", table="orders",
        row_count=1000, column_count=len(cols), columns=cols,
    )
    defaults.update(overrides)
    return DatasetProfile(**defaults)


def _run(**overrides) -> ProfilerRun:
    defaults = dict(
        run_id=new_run_id(),
        created_utc=datetime.now(timezone.utc),
        side_a=_dataset("TEST"),
        side_b=_dataset("PROD"),
    )
    defaults.update(overrides)
    return ProfilerRun(**defaults)


# ---------------------------------------------------------------------------
# NumericStats

class TestNumericStats:
    def test_valid(self):
        s = _numeric_stats()
        assert s.min == 0.0
        assert len(s.histogram_edges) == len(s.histogram_counts) + 1

    def test_edges_must_be_one_longer_than_counts(self):
        with pytest.raises(ValidationError, match="one longer"):
            _numeric_stats(
                histogram_edges=[0.0, 5.0, 10.0],   # 3 edges
                histogram_counts=[5],                # only 1 count → off by one
            )

    def test_negative_count_rejected(self):
        with pytest.raises(ValidationError, match="non-negative"):
            _numeric_stats(histogram_counts=[10, -1, 30, 10])

    def test_unsorted_edges_rejected(self):
        with pytest.raises(ValidationError, match="sorted"):
            _numeric_stats(histogram_edges=[0.0, 50.0, 25.0, 75.0, 100.0])

    def test_max_bins_enforced(self):
        # HISTOGRAM_MAX_BINS+1 counts → too many
        n = HISTOGRAM_MAX_BINS + 1
        with pytest.raises(ValidationError):
            _numeric_stats(
                histogram_edges=[float(i) for i in range(n + 1)],
                histogram_counts=[1] * n,
            )

    def test_min_one_bin_required(self):
        with pytest.raises(ValidationError):
            _numeric_stats(histogram_edges=[0.0], histogram_counts=[])


# ---------------------------------------------------------------------------
# CategoricalStats

class TestCategoricalStats:
    def test_valid(self):
        s = CategoricalStats(top_k={"US": 500, "UK": 200}, entropy=0.5)
        assert s.entropy == 0.5

    def test_negative_entropy_rejected(self):
        with pytest.raises(ValidationError, match="non-negative"):
            CategoricalStats(top_k={"a": 1}, entropy=-0.1)

    def test_negative_topk_count_rejected(self):
        with pytest.raises(ValidationError, match="non-negative"):
            CategoricalStats(top_k={"a": -5}, entropy=0.0)

    def test_topk_size_cap(self):
        with pytest.raises(ValidationError):
            CategoricalStats(
                top_k={str(i): i for i in range(21)},  # 21 > 20
                entropy=0.0,
            )


# ---------------------------------------------------------------------------
# ColumnProfile

class TestColumnProfile:
    def test_null_pct_must_be_in_range(self):
        with pytest.raises(ValidationError):
            _column(null_pct=1.5)

    def test_distinct_pct_must_be_in_range(self):
        with pytest.raises(ValidationError):
            _column(distinct_pct=-0.01)

    def test_negative_null_count_rejected(self):
        with pytest.raises(ValidationError):
            _column(null_count=-1)

    def test_cannot_have_both_numeric_and_categorical(self):
        with pytest.raises(ValidationError, match="cannot have both"):
            _column(
                numeric=_numeric_stats(),
                categorical=CategoricalStats(top_k={"a": 1}, entropy=0.0),
            )

    def test_alerts_and_stereotypes_default_empty(self):
        c = _column()
        assert c.alerts == []
        assert c.stereotypes == []

    def test_can_attach_alert(self):
        c = _column(alerts=[Alert(rule="missing", severity="warn", message="20% null")])
        assert c.alerts[0].rule == "missing"


# ---------------------------------------------------------------------------
# DatasetProfile

class TestDatasetProfile:
    def test_column_count_must_match(self):
        with pytest.raises(ValidationError, match="column_count"):
            _dataset(n_cols=2, column_count=5)

    def test_fqn(self):
        d = _dataset(catalog="c", table="t")
        d2 = DatasetProfile(
            env_label="X", connection="local",
            catalog="c", schema="s", table="t",
            row_count=0, column_count=0, columns=[],
        )
        assert d2.fqn == "c.s.t"

    def test_schema_alias_on_input(self):
        """Input can use either 'schema' or 'schema_'."""
        d = DatasetProfile(
            env_label="X", connection="local",
            catalog="c", schema="s", table="t",   # alias form
            row_count=0, column_count=0, columns=[],
        )
        assert d.schema_ == "s"


# ---------------------------------------------------------------------------
# ColumnComparison + verdict_from_psi

class TestVerdictFromPSI:
    @pytest.mark.parametrize("psi,verdict", [
        (None, "stable"),
        (0.0, "stable"),
        (PSI_STABLE_MAX - 0.001, "stable"),
        (PSI_STABLE_MAX, "moderate"),
        (PSI_MODERATE_MAX - 0.001, "moderate"),
        (PSI_MODERATE_MAX, "significant"),
        (1.0, "significant"),
    ])
    def test_thresholds(self, psi, verdict):
        assert verdict_from_psi(psi) == verdict

    def test_schema_change_dominates(self):
        assert verdict_from_psi(0.99, schema_change="added") == "schema_change"
        assert verdict_from_psi(None, schema_change="type_changed") == "schema_change"


class TestColumnComparison:
    def test_consistent_psi_and_verdict_accepted(self):
        ColumnComparison(column_name="x", psi=0.5, verdict="significant")
        ColumnComparison(column_name="x", psi=0.05, verdict="stable")
        ColumnComparison(column_name="x", psi=0.15, verdict="moderate")

    def test_inconsistent_verdict_rejected(self):
        with pytest.raises(ValidationError, match="inconsistent with PSI"):
            ColumnComparison(column_name="x", psi=0.5, verdict="stable")

    def test_schema_change_requires_schema_change_verdict(self):
        with pytest.raises(ValidationError, match="requires verdict='schema_change'"):
            ColumnComparison(column_name="x", schema_change="added", verdict="stable")

    def test_schema_change_allows_missing_drift_metrics(self):
        cc = ColumnComparison(
            column_name="new_col", schema_change="added", verdict="schema_change"
        )
        assert cc.psi is None

    def test_ks_pvalue_range(self):
        with pytest.raises(ValidationError):
            ColumnComparison(column_name="x", psi=0.05, verdict="stable", ks_pvalue=1.5)


# ---------------------------------------------------------------------------
# Round-trip serialization

class TestRoundTrip:
    def test_minimal_run_roundtrips(self):
        run = _run(run_label="smoke")
        s = run.to_json()
        back = ProfilerRun.model_validate_json(s)
        assert back.run_id == run.run_id
        assert back.run_label == "smoke"
        assert back.side_a.env_label == "TEST"
        assert back.side_b.env_label == "PROD"
        assert back.metamodel_version == METAMODEL_VERSION

    def test_full_run_roundtrips(self):
        col_a = _column(
            name="amount", logical_type="double", physical_type="DOUBLE",
            null_count=50, null_pct=0.05, distinct_count=987, distinct_pct=0.987,
            numeric=_numeric_stats(),
            alerts=[Alert(rule="skewed", severity="warn", message="skew=2.3")],
            stereotypes=["MeasuredAttribute", "Skewed"],
        )
        ds_a = _dataset(env="TEST", columns=[col_a])
        ds_b = _dataset(env="PROD", columns=[col_a.model_copy()])
        cc = ColumnComparison(
            column_name="amount", psi=0.25, ks_stat=0.18, ks_pvalue=0.001,
            verdict="significant", stereotypes=["Drifted"],
        )
        run = ProfilerRun(
            run_id=new_run_id(),
            run_label="full-test",
            created_utc=datetime.now(timezone.utc),
            side_a=ds_a,
            side_b=ds_b,
            comparisons=[cc],
            lineage=Lineage(
                html_profile_a="test_profile.html",
                html_profile_b="prod_profile.html",
                excel_summary="ab_summary.xlsx",
                json_schema="dq-metamodel-v1.schema.json",
            ),
        )
        s = run.to_json()
        back = ProfilerRun.model_validate_json(s)
        assert back == run

    def test_schema_alias_emitted_in_json(self):
        """Wire format uses 'schema', not 'schema_'."""
        run = _run()
        data = json.loads(run.to_json())
        assert "schema" in data["side_a"]
        assert "schema_" not in data["side_a"]


# ---------------------------------------------------------------------------
# Forward compatibility — unknown fields are dropped

class TestForwardCompat:
    def test_unknown_top_level_field_ignored(self):
        run = _run()
        data = json.loads(run.to_json())
        data["future_field"] = {"some": "structure"}
        back = ProfilerRun.model_validate(data)
        assert back.run_id == run.run_id
        # Should not raise; future_field is silently dropped.

    def test_unknown_field_in_column_ignored(self):
        run = _run()
        data = json.loads(run.to_json())
        data["side_a"]["columns"][0]["future_metric"] = 42
        back = ProfilerRun.model_validate(data)
        assert back.side_a.columns[0].name == run.side_a.columns[0].name


# ---------------------------------------------------------------------------
# Schema export

class TestSchemaExport:
    def test_returns_dict(self):
        schema = schema_for_current_version()
        assert isinstance(schema, dict)

    def test_top_level_has_properties(self):
        schema = schema_for_current_version()
        # Pydantic v2 puts the model as top-level with $defs for nested models
        assert "properties" in schema
        assert "metamodel_version" in schema["properties"]
        assert "side_a" in schema["properties"]

    def test_metamodel_version_is_string_default(self):
        schema = schema_for_current_version()
        mv = schema["properties"]["metamodel_version"]
        assert mv["type"] == "string"
        assert mv["default"] == METAMODEL_VERSION


# ---------------------------------------------------------------------------
# UUIDv7

class TestUuidV7:
    def test_returns_uuid(self):
        assert isinstance(new_run_id(), UUID)

    def test_version_is_7(self):
        rid = new_run_id()
        assert rid.version == 7

    def test_time_sortable(self):
        # Generate two IDs with a small sleep; the first should sort first
        a = new_run_id()
        time.sleep(0.002)
        b = new_run_id()
        assert a < b

    def test_uniqueness(self):
        ids = {new_run_id() for _ in range(1000)}
        assert len(ids) == 1000
