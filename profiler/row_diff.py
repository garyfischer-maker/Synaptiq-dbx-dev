"""Row-level diff (milestone 6).

Compares two Unity Catalog tables on a set of key columns and reports:
    - rows_only_in_a  — keys present in Side A but not Side B (removed)
    - rows_only_in_b  — keys present in Side B but not Side A (added)
    - rows_changed    — keys present in both sides with different values
    - rows_identical  — keys present in both sides with identical values

Sample rows are fetched for each diff type (up to max_sample).

Uses the Databricks Statement Execution API — no JDBC connector needed.
Row diff is opt-in; the user must supply at least one key column.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import TableRef


# ---------------------------------------------------------------------------
# Result dataclass


@dataclass
class RowDiffResult:
    key_columns: list[str]

    # Counts (over the full tables, not just the sample)
    rows_only_in_a: int = 0   # removed — in A not B
    rows_only_in_b: int = 0   # added   — in B not A
    rows_changed:   int = 0   # same key, at least one value differs
    rows_identical: int = 0   # same key, all values identical

    # Sample rows — list of row arrays matching col_names_*
    sample_removed: list[list] = field(default_factory=list)
    sample_added:   list[list] = field(default_factory=list)
    sample_changed: list[list] = field(default_factory=list)  # interleaved A/B rows

    # Column names for sample display
    col_names: list[str] = field(default_factory=list)

    error: Optional[str] = None

    @property
    def total_rows_in_a(self) -> int:
        return self.rows_only_in_a + self.rows_changed + self.rows_identical

    @property
    def total_rows_in_b(self) -> int:
        return self.rows_only_in_b + self.rows_changed + self.rows_identical

    @property
    def has_differences(self) -> bool:
        return (self.rows_only_in_a + self.rows_only_in_b + self.rows_changed) > 0


# ---------------------------------------------------------------------------
# Public API


def compute_row_diff(
    ref_a: "TableRef",
    ref_b: "TableRef",
    key_columns: list[str],
    max_sample: int = 100,
) -> RowDiffResult:
    """Compare two tables row-by-row on the given key columns.

    Returns a RowDiffResult with counts + sample rows for each diff type.
    Requires PROFILER_RUNTIME=databricks and an active SQL warehouse.
    """
    if not key_columns:
        return RowDiffResult(key_columns=[], error="No key columns specified.")

    result = RowDiffResult(key_columns=key_columns)
    a = ref_a.fqn
    b = ref_b.fqn
    join_on = " AND ".join(f"a.`{k}` <=> b.`{k}`" for k in key_columns)
    null_check = f"b.`{key_columns[0]}` IS NULL"
    null_check_b = f"a.`{key_columns[0]}` IS NULL"

    try:
        # ── 1. Rows only in A (removed) ──────────────────────────────────────
        r = _sql(f"""
            SELECT COUNT(*) FROM {a} a
            LEFT JOIN {b} b ON {join_on}
            WHERE {null_check}
        """)
        result.rows_only_in_a = _scalar_int(r)

        # ── 2. Rows only in B (added) ─────────────────────────────────────────
        r = _sql(f"""
            SELECT COUNT(*) FROM {b} b
            LEFT JOIN {a} a ON {join_on}
            WHERE {null_check_b}
        """)
        result.rows_only_in_b = _scalar_int(r)

        # ── 3. Rows changed (same key, different hash) ────────────────────────
        r = _sql(f"""
            SELECT COUNT(*) FROM {a} a
            JOIN {b} b ON {join_on}
            WHERE hash(a.*) != hash(b.*)
        """)
        result.rows_changed = _scalar_int(r)

        # ── 4. Rows identical ────────────────────────────────────────────────
        r = _sql(f"""
            SELECT COUNT(*) FROM {a} a
            JOIN {b} b ON {join_on}
            WHERE hash(a.*) = hash(b.*)
        """)
        result.rows_identical = _scalar_int(r)

        # ── 5. Sample column names (from table A schema) ──────────────────────
        r = _sql(f"SELECT * FROM {a} LIMIT 1")
        if r.manifest and r.manifest.schema:
            result.col_names = [c.name for c in r.manifest.schema.columns]

        # ── 6. Sample removed rows ────────────────────────────────────────────
        if result.rows_only_in_a > 0:
            r = _sql(f"""
                SELECT a.* FROM {a} a
                LEFT JOIN {b} b ON {join_on}
                WHERE {null_check}
                LIMIT {max_sample}
            """)
            result.sample_removed = r.result.data_array or [] if r.result else []

        # ── 7. Sample added rows ──────────────────────────────────────────────
        if result.rows_only_in_b > 0:
            r = _sql(f"""
                SELECT b.* FROM {b} b
                LEFT JOIN {a} a ON {join_on}
                WHERE {null_check_b}
                LIMIT {max_sample}
            """)
            result.sample_added = r.result.data_array or [] if r.result else []

        # ── 8. Sample changed rows (interleaved A then B per key) ─────────────
        if result.rows_changed > 0:
            key_sel = ", ".join(f"a.`{k}`" for k in key_columns)
            r = _sql(f"""
                SELECT {key_sel},
                    'A' as _side, a.*
                FROM {a} a JOIN {b} b ON {join_on}
                WHERE hash(a.*) != hash(b.*)
                LIMIT {max_sample}
                UNION ALL
                SELECT {key_sel},
                    'B' as _side, b.*
                FROM {a} a JOIN {b} b ON {join_on}
                WHERE hash(a.*) != hash(b.*)
                LIMIT {max_sample}
                ORDER BY {", ".join(f"`{k}`" for k in key_columns)}, _side
            """)
            result.sample_changed = r.result.data_array or [] if r.result else []

    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Pure helpers — testable without Databricks


def summarise(result: RowDiffResult) -> dict[str, Any]:
    """Return a summary dict for display / logging."""
    return {
        "key_columns":    result.key_columns,
        "rows_only_in_a": result.rows_only_in_a,
        "rows_only_in_b": result.rows_only_in_b,
        "rows_changed":   result.rows_changed,
        "rows_identical": result.rows_identical,
        "has_differences": result.has_differences,
        "error":          result.error,
    }


def diff_pct(changed: int, total: int) -> str:
    """Return a human-readable percentage string."""
    if total == 0:
        return "0.0%"
    return f"{changed / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Internal SQL execution


def _sql(statement: str):
    """Execute a statement via Statement Execution API and wait for result."""
    from .catalog import _workspace_client
    w = _workspace_client()
    wid = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    result = w.statement_execution.execute_statement(
        warehouse_id=wid,
        statement=statement.strip(),
        wait_timeout="50s",
    )
    deadline = time.time() + 300
    while True:
        state = str(result.status.state).upper() if result.status else "UNKNOWN"
        if "SUCCEEDED" in state:
            return result
        if any(s in state for s in ("FAILED", "CANCELLED", "CLOSED")):
            err = (result.status.error.message
                   if (result.status and result.status.error) else state)
            raise RuntimeError(f"Row diff SQL failed ({state}): {err}")
        if time.time() > deadline:
            raise TimeoutError("Row diff query timed out after 5 minutes.")
        time.sleep(2)
        result = w.statement_execution.get_statement(result.statement_id)


def _scalar_int(result) -> int:
    """Extract a single integer from a COUNT(*) result."""
    if result.result and result.result.data_array:
        return int(result.result.data_array[0][0])
    return 0
