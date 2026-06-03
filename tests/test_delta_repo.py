"""Unit tests for profiler.delta_repo.

Tests cover:
- flatten() output shape and row counts
- flatten() field values for each of the five tables
- DDL string correctness (table names, clustering, partitioning)

ingest() and ensure_tables() require a live SparkSession and are not tested
here — they are exercised in the Databricks integration environment.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from profiler.metamodel import (
    Alert,
    CategoricalStats,
    ColumnComparison,
    ColumnProfile,
    DatasetProfile,
    NumericStats,
    ProfilerRun,
    new_run_id,
    verdict_from_psi,
)
from profiler.delta_repo import _ddl, flatten


# ---------------------------------------------------------------------------
# Builders


def _numeric(**kw) -> NumericStats:
    base = dict(
        min=0.0, max=100.0, mean=50.0, stddev=20.0,
        p1=1.0, p5=5.0, p25=25.0, p50=50.0, p75=75.0, p95=95.0, p99=99.0,
        skewness=0.1, kurtosis=2.8,
        histogram_edges=[0.0, 25.0, 50.0, 75.0, 100.0],
        histogram_counts=[10, 30, 30, 10],
    )
    base.update(kw)
    return NumericStats(**base)


def _categorical(**kw) -> CategoricalStats:
    base = dict(top_k={"A": 50, "B": 30}, entropy=1.2)
    base.update(kw)
    return CategoricalStats(**base)


def _col(name: str = "col_x", **kw) -> ColumnProfile:
    base = dict(
        name=name, logical_type="integer", physical_type="INT",
        nullable=True, null_count=5, null_pct=0.05,
        distinct_count=90, distinct_pct=0.9,
    )
    base.update(kw)
    return ColumnProfile(**base)


def _dataset(env: str = "TEST", cols=None, **kw) -> DatasetProfile:
    if cols is None:
        cols = [_col("id"), _col("amount")]
    base = dict(
        env_label=env, connection="local",
        catalog="dev", schema="test_main_sales", table="orders",
        row_count=1000, column_count=len(cols), columns=cols,
    )
    base.update(kw)
    return DatasetProfile(**base)


def _run(comparisons=None, **kw) -> ProfilerRun:
    base = dict(
        run_id=new_run_id(),
        created_utc=datetime.now(timezone.utc),
        side_a=_dataset("TEST"),
        side_b=_dataset("PROD", catalog="dev", schema="prod_main_sales"),
        comparisons=comparisons or [],
    )
    base.update(kw)
    return ProfilerRun(**base)


def _comparison(col: str = "amount", psi: float = 0.25, schema_change: str = "unchanged"):
    verdict = verdict_from_psi(psi, schema_change=schema_change)  # type: ignore[arg-type]
    return ColumnComparison(
        column_name=col,
        schema_change=schema_change,  # type: ignore[arg-type]
        psi=psi if schema_change == "unchanged" else None,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# flatten() — table shape


class TestFlattenShape:
    def test_returns_all_five_tables(self):
        rows = flatten(_run())
        assert set(rows.keys()) == {
            "profiler_runs", "dataset_profiles", "column_profiles",
            "column_alerts", "column_comparisons",
        }

    def test_one_profiler_run_row(self):
        assert len(flatten(_run())["profiler_runs"]) == 1

    def test_two_dataset_profile_rows(self):
        # one per side (A + B)
        assert len(flatten(_run())["dataset_profiles"]) == 2

    def test_column_profiles_count(self):
        # 2 cols per side × 2 sides = 4
        rows = flatten(_run())["column_profiles"]
        assert len(rows) == 4

    def test_no_alerts_empty_column_alerts(self):
        assert flatten(_run())["column_alerts"] == []

    def test_alert_rows_per_column(self):
        alert = Alert(rule="missing", severity="warn", message="high nulls")
        cols = [_col("x", alerts=[alert, alert])]
        run = _run()
        run = _run(side_a=_dataset(cols=cols), side_b=_dataset(cols=cols))
        rows = flatten(run)["column_alerts"]
        # 2 alerts × 2 sides = 4
        assert len(rows) == 4

    def test_comparison_rows_match_input(self):
        comps = [_comparison("a"), _comparison("b")]
        rows = flatten(_run(comparisons=comps))["column_comparisons"]
        assert len(rows) == 2

    def test_no_comparisons_empty(self):
        assert flatten(_run())["column_comparisons"] == []


# ---------------------------------------------------------------------------
# flatten() — profiler_runs fields


class TestFlattenRunRow:
    def setup_method(self):
        self.run = _run()
        self.row = flatten(self.run)["profiler_runs"][0]

    def test_run_id_matches(self):
        assert self.row["run_id"] == str(self.run.run_id)

    def test_metamodel_version_present(self):
        assert self.row["metamodel_version"] == self.run.metamodel_version

    def test_created_utc_is_datetime(self):
        assert isinstance(self.row["created_utc"], datetime)

    def test_created_date_is_date(self):
        from datetime import date
        assert isinstance(self.row["created_date"], date)

    def test_side_fqns(self):
        assert self.row["side_a_fqn"] == self.run.side_a.fqn
        assert self.row["side_b_fqn"] == self.run.side_b.fqn

    def test_lineage_json_is_string(self):
        assert isinstance(self.row["lineage_json"], str)


# ---------------------------------------------------------------------------
# flatten() — dataset_profiles fields


class TestFlattenDatasetRows:
    def setup_method(self):
        self.run = _run()
        self.rows = flatten(self.run)["dataset_profiles"]
        self.sides = {r["side"]: r for r in self.rows}

    def test_sides_are_a_and_b(self):
        assert set(self.sides.keys()) == {"A", "B"}

    def test_side_a_catalog(self):
        assert self.sides["A"]["catalog"] == self.run.side_a.catalog

    def test_side_b_schema(self):
        assert self.sides["B"]["schema"] == self.run.side_b.schema_

    def test_row_count_present(self):
        assert self.sides["A"]["row_count"] == 1000

    def test_run_id_matches(self):
        for row in self.rows:
            assert row["run_id"] == str(self.run.run_id)


# ---------------------------------------------------------------------------
# flatten() — column_profiles fields


class TestFlattenColumnRows:
    def test_numeric_stats_flattened(self):
        cols = [_col("price", numeric=_numeric(mean=42.0, p50=40.0))]
        rows = flatten(_run(side_a=_dataset(cols=cols), side_b=_dataset(cols=cols)))
        cp = flatten(_run(
            side_a=_dataset(cols=cols),
            side_b=_dataset(cols=cols),
        ))["column_profiles"]
        a_rows = [r for r in cp if r["side"] == "A" and r["column_name"] == "price"]
        assert len(a_rows) == 1
        assert a_rows[0]["numeric_mean"] == pytest.approx(42.0)
        assert a_rows[0]["numeric_p50"] == pytest.approx(40.0)
        assert a_rows[0]["cat_entropy"] is None

    def test_categorical_stats_flattened(self):
        cols = [_col("status", categorical=_categorical(entropy=1.5))]
        cp = flatten(_run(
            side_a=_dataset(cols=cols),
            side_b=_dataset(cols=cols),
        ))["column_profiles"]
        a_rows = [r for r in cp if r["side"] == "A" and r["column_name"] == "status"]
        assert a_rows[0]["cat_entropy"] == pytest.approx(1.5)
        assert a_rows[0]["numeric_mean"] is None
        assert json.loads(a_rows[0]["cat_top_k_json"]) == {"A": 50, "B": 30}

    def test_stereotypes_joined(self):
        cols = [_col("x", stereotypes=["NullSpike", "Skewed"])]
        cp = flatten(_run(
            side_a=_dataset(cols=cols),
            side_b=_dataset(cols=cols),
        ))["column_profiles"]
        a_row = next(r for r in cp if r["side"] == "A")
        assert a_row["stereotypes"] == "NullSpike,Skewed"

    def test_no_stereotypes_is_none(self):
        cp = flatten(_run())["column_profiles"]
        assert all(r["stereotypes"] is None for r in cp)


# ---------------------------------------------------------------------------
# flatten() — column_comparisons fields


class TestFlattenComparisonRows:
    def setup_method(self):
        self.run = _run(comparisons=[_comparison("amount", psi=0.25)])
        self.row = flatten(self.run)["column_comparisons"][0]

    def test_column_name(self):
        assert self.row["column_name"] == "amount"

    def test_psi_value(self):
        assert self.row["psi"] == pytest.approx(0.25)

    def test_verdict(self):
        assert self.row["verdict"] == "significant"

    def test_catalog_a_b_present(self):
        assert self.row["catalog_a"] == self.run.side_a.catalog
        assert self.row["catalog_b"] == self.run.side_b.catalog

    def test_schema_change_default(self):
        assert self.row["schema_change"] == "unchanged"

    def test_schema_change_added(self):
        cmp = _comparison("new_col", schema_change="added")
        row = flatten(_run(comparisons=[cmp]))["column_comparisons"][0]
        assert row["schema_change"] == "added"
        assert row["psi"] is None


# ---------------------------------------------------------------------------
# _ddl() — DDL string checks


class TestDDL:
    def setup_method(self):
        self.stmts = _ddl("dev", "test_main_profiler")

    def test_five_statements(self):
        assert len(self.stmts) == 5

    def test_all_create_table_if_not_exists(self):
        for stmt in self.stmts:
            assert "CREATE TABLE IF NOT EXISTS" in stmt

    def test_catalog_schema_in_all_stmts(self):
        for stmt in self.stmts:
            assert "`dev`.`test_main_profiler`" in stmt

    def test_all_use_cluster_by(self):
        for stmt in self.stmts:
            assert "CLUSTER BY" in stmt

    def test_run_id_in_profiler_runs(self):
        assert "run_id" in self.stmts[0]

    def test_cluster_by_in_column_profiles(self):
        # column_profiles is index 2
        assert "CLUSTER BY" in self.stmts[2]
        assert "column_name" in self.stmts[2]

    def test_all_use_delta(self):
        for stmt in self.stmts:
            assert "USING DELTA" in stmt
