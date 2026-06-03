"""Unit tests for profiler.row_diff (pure helpers only — no Databricks needed)."""

from __future__ import annotations

import pytest

from profiler.row_diff import RowDiffResult, diff_pct, summarise


# ---------------------------------------------------------------------------
# RowDiffResult dataclass


class TestRowDiffResult:
    def _result(self, **kw) -> RowDiffResult:
        defaults = dict(
            key_columns=["claim_id"],
            rows_only_in_a=10,
            rows_only_in_b=5,
            rows_changed=20,
            rows_identical=965,
        )
        defaults.update(kw)
        return RowDiffResult(**defaults)

    def test_has_differences_true(self):
        assert self._result().has_differences is True

    def test_has_differences_false_when_all_identical(self):
        r = self._result(rows_only_in_a=0, rows_only_in_b=0, rows_changed=0)
        assert r.has_differences is False

    def test_has_differences_true_only_removed(self):
        r = self._result(rows_only_in_b=0, rows_changed=0)
        assert r.has_differences is True

    def test_has_differences_true_only_added(self):
        r = self._result(rows_only_in_a=0, rows_changed=0)
        assert r.has_differences is True

    def test_has_differences_true_only_changed(self):
        r = self._result(rows_only_in_a=0, rows_only_in_b=0)
        assert r.has_differences is True

    def test_total_rows_in_a(self):
        r = self._result()
        # rows_only_in_a + rows_changed + rows_identical = 10 + 20 + 965
        assert r.total_rows_in_a == 995

    def test_total_rows_in_b(self):
        r = self._result()
        # rows_only_in_b + rows_changed + rows_identical = 5 + 20 + 965
        assert r.total_rows_in_b == 990

    def test_error_field_default_none(self):
        r = RowDiffResult(key_columns=["id"])
        assert r.error is None

    def test_sample_lists_default_empty(self):
        r = RowDiffResult(key_columns=["id"])
        assert r.sample_removed == []
        assert r.sample_added == []
        assert r.sample_changed == []

    def test_multiple_key_columns(self):
        r = RowDiffResult(key_columns=["claim_id", "claim_line_number"])
        assert len(r.key_columns) == 2


# ---------------------------------------------------------------------------
# diff_pct


class TestDiffPct:
    def test_zero_changed(self):
        assert diff_pct(0, 1000) == "0.0%"

    def test_half(self):
        assert diff_pct(500, 1000) == "50.0%"

    def test_all(self):
        assert diff_pct(100, 100) == "100.0%"

    def test_zero_total(self):
        assert diff_pct(5, 0) == "0.0%"

    def test_small_fraction(self):
        result = diff_pct(1, 1000)
        assert "0.1" in result


# ---------------------------------------------------------------------------
# summarise


class TestSummarise:
    def test_returns_dict(self):
        r = RowDiffResult(
            key_columns=["id"],
            rows_only_in_a=3,
            rows_only_in_b=1,
            rows_changed=7,
            rows_identical=89,
        )
        s = summarise(r)
        assert isinstance(s, dict)

    def test_all_keys_present(self):
        s = summarise(RowDiffResult(key_columns=["id"]))
        for key in ("key_columns", "rows_only_in_a", "rows_only_in_b",
                    "rows_changed", "rows_identical", "has_differences", "error"):
            assert key in s

    def test_values_match(self):
        r = RowDiffResult(
            key_columns=["claim_id"],
            rows_only_in_a=10,
            rows_only_in_b=5,
            rows_changed=20,
            rows_identical=965,
        )
        s = summarise(r)
        assert s["rows_only_in_a"] == 10
        assert s["rows_only_in_b"] == 5
        assert s["rows_changed"] == 20
        assert s["rows_identical"] == 965
        assert s["has_differences"] is True

    def test_error_captured(self):
        r = RowDiffResult(key_columns=["id"], error="warehouse timeout")
        s = summarise(r)
        assert s["error"] == "warehouse timeout"
