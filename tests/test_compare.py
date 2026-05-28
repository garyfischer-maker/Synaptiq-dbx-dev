"""Unit tests for profiler.compare."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from profiler.compare import compare_tables, schema_change_counts, schema_diff
from profiler.metamodel import (
    ColumnProfile,
    DatasetProfile,
    ProfilerRun,
    new_run_id,
)


# ---------------------------------------------------------------------------
# Builders


def _col(name: str, logical_type: str = "string", nullable: bool = True, **kw) -> ColumnProfile:
    return ColumnProfile(
        name=name,
        logical_type=logical_type,
        physical_type=logical_type.upper(),
        nullable=nullable,
        null_count=0, null_pct=0.0,
        distinct_count=10, distinct_pct=0.1,
        **kw,
    )


def _dataset(cols: list[ColumnProfile], env: str = "TEST") -> DatasetProfile:
    return DatasetProfile(
        env_label=env, connection="local",
        catalog="dev", schema="test_main_claims", table="medical_claim",
        row_count=1000, column_count=len(cols), columns=cols,
    )


# ---------------------------------------------------------------------------
# schema_diff


class TestSchemaDiff:
    def test_identical_schemas_all_unchanged(self):
        cols = [_col("id"), _col("amount", "double")]
        result = schema_diff(_dataset(cols), _dataset(cols))
        assert all(c.schema_change == "unchanged" for c in result)
        assert all(c.verdict == "stable" for c in result)

    def test_column_count_matches_union(self):
        a = _dataset([_col("x"), _col("y")])
        b = _dataset([_col("y"), _col("z")])
        result = schema_diff(a, b)
        names = [c.column_name for c in result]
        assert set(names) == {"x", "y", "z"}

    def test_removed_column(self):
        a = _dataset([_col("id"), _col("old_col")])
        b = _dataset([_col("id")])
        result = {c.column_name: c for c in schema_diff(a, b)}
        assert result["old_col"].schema_change == "removed"
        assert result["old_col"].verdict == "schema_change"

    def test_added_column(self):
        a = _dataset([_col("id")])
        b = _dataset([_col("id"), _col("new_col")])
        result = {c.column_name: c for c in schema_diff(a, b)}
        assert result["new_col"].schema_change == "added"
        assert result["new_col"].verdict == "schema_change"

    def test_type_changed(self):
        a = _dataset([_col("amount", "integer")])
        b = _dataset([_col("amount", "double")])
        result = schema_diff(a, b)
        assert result[0].schema_change == "type_changed"
        assert result[0].verdict == "schema_change"

    def test_nullability_changed(self):
        a = _dataset([_col("id", nullable=False)])
        b = _dataset([_col("id", nullable=True)])
        result = schema_diff(a, b)
        assert result[0].schema_change == "nullability_changed"
        assert result[0].verdict == "schema_change"

    def test_schema_changed_columns_have_schema_changed_stereotype(self):
        a = _dataset([_col("gone")])
        b = _dataset([])
        result = schema_diff(a, b)
        assert "SchemaChanged" in result[0].stereotypes

    def test_unchanged_columns_have_no_stereotypes(self):
        cols = [_col("x")]
        result = schema_diff(_dataset(cols), _dataset(cols))
        assert result[0].stereotypes == []

    def test_ordering_a_columns_first(self):
        a = _dataset([_col("a"), _col("b")])
        b = _dataset([_col("b"), _col("c")])
        names = [c.column_name for c in schema_diff(a, b)]
        # a and b come first (A order), c last (B-only)
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("c")

    def test_empty_both_sides(self):
        assert schema_diff(_dataset([]), _dataset([])) == []

    def test_empty_side_a(self):
        b = _dataset([_col("x"), _col("y")])
        result = schema_diff(_dataset([]), b)
        assert all(c.schema_change == "added" for c in result)

    def test_empty_side_b(self):
        a = _dataset([_col("x"), _col("y")])
        result = schema_diff(a, _dataset([]))
        assert all(c.schema_change == "removed" for c in result)


# ---------------------------------------------------------------------------
# compare_tables


class TestCompareTables:
    def test_returns_list_of_column_comparisons(self):
        cols = [_col("id"), _col("amount", "double")]
        result = compare_tables(_dataset(cols), _dataset(cols))
        assert len(result) == 2

    def test_stat_diff_is_passthrough_in_milestone_3(self):
        # stat_diff should not modify comparisons (PSI added in milestone 4)
        cols = [_col("id")]
        result = compare_tables(_dataset(cols), _dataset(cols))
        assert result[0].psi is None
        assert result[0].ks_stat is None
        assert result[0].chi_square is None
        assert result[0].js_divergence is None

    def test_mixed_schema(self):
        a = _dataset([_col("id"), _col("old")])
        b = _dataset([_col("id"), _col("new")])
        result = {c.column_name: c for c in compare_tables(a, b)}
        assert result["id"].schema_change == "unchanged"
        assert result["old"].schema_change == "removed"
        assert result["new"].schema_change == "added"


# ---------------------------------------------------------------------------
# schema_change_counts


class TestSchemaChangeCounts:
    def test_all_zeros_on_empty(self):
        counts = schema_change_counts([])
        assert all(v == 0 for v in counts.values())

    def test_counts_correctly(self):
        cols = [_col("a"), _col("b"), _col("c")]
        a = _dataset(cols)
        b = _dataset([_col("a"), _col("d")])  # b removed, c removed, d added
        comparisons = schema_diff(a, b)
        counts = schema_change_counts(comparisons)
        assert counts["unchanged"] == 1   # a
        assert counts["removed"] == 2     # b, c
        assert counts["added"] == 1       # d

    def test_all_keys_present(self):
        counts = schema_change_counts([])
        for key in ("added", "removed", "type_changed", "nullability_changed", "unchanged"):
            assert key in counts
