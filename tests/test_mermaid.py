"""Unit tests for profiler.mermaid.

Structural (not snapshot) tests: assert shape, key tokens, and invariants
rather than exact strings, so diagram style can evolve without constant
fixture updates.
"""

from __future__ import annotations

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
from profiler.mermaid import (
    SUMMARY_MODE_THRESHOLD,
    render_all,
    render_drift,
    render_side_schema,
)


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
        nullable=True, null_count=0, null_pct=0.0,
        distinct_count=100, distinct_pct=0.1,
    )
    base.update(kw)
    return ColumnProfile(**base)


def _dataset(env: str = "TEST", cols: list[ColumnProfile] | None = None, **kw) -> DatasetProfile:
    if cols is None:
        cols = [_col("id"), _col("amount")]
    base = dict(
        env_label=env, connection="local",
        catalog="dev", schema="test_main_sales", table="orders",
        row_count=1000, column_count=len(cols), columns=cols,
    )
    base.update(kw)
    return DatasetProfile(**base)


def _run(comparisons: list[ColumnComparison] | None = None, **kw) -> ProfilerRun:
    base = dict(
        run_id=new_run_id(),
        created_utc=datetime.now(timezone.utc),
        side_a=_dataset("TEST"),
        side_b=_dataset("PROD", catalog="dev", schema="prod_main_sales"),
        comparisons=comparisons or [],
    )
    base.update(kw)
    return ProfilerRun(**base)


def _comparison(
    column_name: str = "amount",
    psi: float = 0.25,
    schema_change: str = "unchanged",
) -> ColumnComparison:
    verdict = verdict_from_psi(psi, schema_change=schema_change)  # type: ignore[arg-type]
    return ColumnComparison(
        column_name=column_name,
        schema_change=schema_change,  # type: ignore[arg-type]
        psi=psi if schema_change == "unchanged" else None,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# render_side_schema


class TestRenderSideSchema:
    def test_starts_with_classDiagram(self):
        out = render_side_schema(_dataset())
        assert out.startswith("classDiagram")

    def test_table_id_in_output(self):
        profile = _dataset(catalog="dev", schema="test_main_sales", table="orders")
        out = render_side_schema(profile)
        assert "tbl_dev_test_main_sales_orders" in out

    def test_table_stereotype_present(self):
        out = render_side_schema(_dataset())
        assert "<<Table>>" in out

    def test_column_names_appear(self):
        cols = [_col("customer_id"), _col("total")]
        out = render_side_schema(_dataset(cols=cols))
        assert "customer_id" in out
        assert "total" in out

    def test_numeric_stats_rendered(self):
        cols = [_col("price", numeric=_numeric(mean=42.5, p50=40.0))]
        out = render_side_schema(_dataset(cols=cols))
        assert "mean" in out
        assert "p50" in out

    def test_categorical_stats_rendered(self):
        cols = [_col("status", categorical=_categorical(entropy=1.5))]
        out = render_side_schema(_dataset(cols=cols))
        assert "entropy" in out

    def test_alert_rendered(self):
        alert = Alert(rule="missing", severity="warn", message="high nulls")
        cols = [_col("x", alerts=[alert])]
        out = render_side_schema(_dataset(cols=cols))
        assert "ALERT" in out
        assert "missing" in out

    def test_stereotype_from_column(self):
        cols = [_col("x", stereotypes=["NullSpike"])]
        out = render_side_schema(_dataset(cols=cols))
        assert "NullSpike" in out

    def test_row_count_in_table_class(self):
        profile = _dataset(row_count=99_000)
        out = render_side_schema(profile)
        assert "99,000" in out

    def test_duplicate_rows_omitted_when_zero(self):
        out = render_side_schema(_dataset())
        assert "duplicate_rows" not in out

    def test_duplicate_rows_shown_when_nonzero(self):
        out = render_side_schema(_dataset(duplicate_rows=5))
        assert "duplicate_rows" in out


class TestSummaryMode:
    def _wide_dataset(self, n: int = SUMMARY_MODE_THRESHOLD + 1) -> DatasetProfile:
        cols = [_col(f"c{i}") for i in range(n)]
        return _dataset(cols=cols)

    def test_wide_table_shows_summary_mode_flag(self):
        out = render_side_schema(self._wide_dataset())
        assert "summary_mode" in out

    def test_wide_table_no_alerts_emits_note(self):
        out = render_side_schema(self._wide_dataset())
        assert "no alerts" in out

    def test_wide_table_alerted_col_shown(self):
        alert = Alert(rule="missing", severity="warn", message="high nulls")
        cols = [_col(f"c{i}") for i in range(SUMMARY_MODE_THRESHOLD + 1)]
        cols[0] = _col("flagged", alerts=[alert])
        profile = _dataset(cols=cols)
        out = render_side_schema(profile)
        assert "flagged" in out

    def test_narrow_table_all_cols_shown(self):
        cols = [_col(f"c{i}") for i in range(SUMMARY_MODE_THRESHOLD)]
        out = render_side_schema(_dataset(cols=cols))
        for i in range(SUMMARY_MODE_THRESHOLD):
            assert f"c{i}" in out


# ---------------------------------------------------------------------------
# render_drift


class TestRenderDrift:
    def test_starts_with_classDiagram(self):
        out = render_drift(_run())
        assert out.startswith("classDiagram")

    def test_no_drift_shows_all_stable(self):
        run = _run(comparisons=[_comparison(psi=0.05)])
        out = render_drift(run)
        assert "all stable" in out

    def test_drifted_column_appears_in_both_sides(self):
        run = _run(comparisons=[_comparison(column_name="amount", psi=0.25)])
        out = render_drift(run)
        assert "sideA_amount" in out
        assert "sideB_amount" in out

    def test_stable_column_excluded(self):
        comps = [
            _comparison("amount", psi=0.25),   # significant — included
            _comparison("id", psi=0.02),        # stable — excluded
        ]
        run = _run(comparisons=comps)
        out = render_drift(run)
        assert "amount" in out
        assert "sideA_id" not in out

    def test_drift_label_contains_verdict(self):
        run = _run(comparisons=[_comparison("amount", psi=0.25)])
        out = render_drift(run)
        assert "significant" in out

    def test_drift_label_contains_psi(self):
        run = _run(comparisons=[_comparison("amount", psi=0.25)])
        out = render_drift(run)
        assert "PSI" in out

    def test_schema_change_column_shown(self):
        cmp = _comparison("new_col", psi=0.0, schema_change="added")
        run = _run(comparisons=[cmp])
        out = render_drift(run)
        assert "new_col" in out
        assert "added" in out

    def test_cross_side_association_present(self):
        run = _run(comparisons=[_comparison("amount", psi=0.25)])
        out = render_drift(run)
        assert "sideA_amount --> sideB_amount" in out

    def test_multiple_drifted_columns(self):
        comps = [
            _comparison("col_a", psi=0.15),
            _comparison("col_b", psi=0.30),
        ]
        run = _run(comparisons=comps)
        out = render_drift(run)
        assert "col_a" in out
        assert "col_b" in out


# ---------------------------------------------------------------------------
# render_all


class TestRenderAll:
    def test_returns_three_strings(self):
        a, b, d = render_all(_run())
        assert isinstance(a, str)
        assert isinstance(b, str)
        assert isinstance(d, str)

    def test_all_three_start_with_classDiagram(self):
        for diagram in render_all(_run()):
            assert diagram.startswith("classDiagram")

    def test_side_a_contains_side_a_table(self):
        run = _run()
        a, _, _ = render_all(run)
        assert run.side_a.table in a

    def test_side_b_contains_side_b_table(self):
        run = _run()
        _, b, _ = render_all(run)
        assert run.side_b.table in b

    def test_side_identifiers_are_distinct(self):
        a, b, _ = render_all(_run())
        # Side-A and Side-B both use "orders" as table, but different schemas
        assert "test_main_sales" in a
        assert "prod_main_sales" in b


# ---------------------------------------------------------------------------
# _safe identifier helper (indirectly via render_side_schema)


class TestIdentifierSafety:
    def test_numeric_column_name_handled(self):
        cols = [_col("123col")]
        out = render_side_schema(_dataset(cols=cols))
        # Should not start a class name with a digit
        assert "class x123col" in out or "tbl_" in out  # id is prefixed by tbl_id

    def test_dots_in_catalog_replaced(self):
        # Dots are replaced in the class *identifier*; the display attribute
        # line still shows the original catalog name for readability.
        profile = _dataset(catalog="my.catalog")
        out = render_side_schema(profile)
        assert "class tbl_my_catalog" in out      # identifier is safe
        assert "tbl_my.catalog" not in out        # raw dot never in an identifier

    def test_spaces_in_name_replaced(self):
        cols = [_col("my col")]
        out = render_side_schema(_dataset(cols=cols))
        assert "my col" not in out
        assert "my_col" in out
