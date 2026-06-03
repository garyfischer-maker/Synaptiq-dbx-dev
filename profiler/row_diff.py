"""Row-level diff (milestone 6).

Compares two Unity Catalog tables on a set of key columns and reports:
    - rows_only_in_a  — keys present in Side A but not Side B (removed)
    - rows_only_in_b  — keys present in Side B but not Side A (added)
    - rows_changed    — keys present in both sides with different values
    - rows_identical  — keys present in both sides with identical values

Uses the Databricks Statement Execution API — no JDBC connector needed.

SQL design notes:
    hash(a.*) is invalid when a table alias is in scope (Databricks limitation).
    We use CTEs so that hash(*) is called on a single-table context, then join
    the hash columns to detect row-level changes.

    LIMIT before UNION ALL is invalid in Databricks SQL.
    Changed-row samples are fetched with two separate queries and interleaved
    in Python.
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

    # Sample rows — list of row arrays matching col_names / col_names_changed
    sample_removed: list[list] = field(default_factory=list)
    sample_added:   list[list] = field(default_factory=list)
    sample_changed: list[list] = field(default_factory=list)

    # Column names for display
    col_names:         list[str] = field(default_factory=list)
    col_names_changed: list[str] = field(default_factory=list)  # includes _side

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
    """Compare two tables row-by-row on the given key columns."""
    if not key_columns:
        return RowDiffResult(key_columns=[], error="No key columns specified.")

    result = RowDiffResult(key_columns=key_columns)
    a = ref_a.fqn
    b = ref_b.fqn

    # Join condition using aliases (for LEFT JOINs)
    join_on  = " AND ".join(f"a.`{k}` <=> b.`{k}`" for k in key_columns)
    null_b   = f"b.`{key_columns[0]}` IS NULL"
    null_a   = f"a.`{key_columns[0]}` IS NULL"

    # Key columns without alias (for CTEs where there is only one table)
    key_sel  = ", ".join(f"`{k}`" for k in key_columns)

    # CTE join condition (ha/hb aliases from the CTE)
    key_join = " AND ".join(f"ha.`{k}` <=> hb.`{k}`" for k in key_columns)

    # Hash CTE: computes hash(*) per table in a single-table context so
    # Databricks can resolve * without an alias conflict.
    hash_cte = (
        f"WITH ha AS (SELECT {key_sel}, hash(*) AS _rh FROM {a}),\n"
        f"     hb AS (SELECT {key_sel}, hash(*) AS _rh FROM {b})"
    )

    try:
        # ── 1. Rows only in A (removed) ───────────────────────────────────────
        r = _sql(f"SELECT COUNT(*) FROM {a} a LEFT JOIN {b} b ON {join_on} WHERE {null_b}")
        result.rows_only_in_a = _scalar_int(r)

        # ── 2. Rows only in B (added) ─────────────────────────────────────────
        r = _sql(f"SELECT COUNT(*) FROM {b} b LEFT JOIN {a} a ON {join_on} WHERE {null_a}")
        result.rows_only_in_b = _scalar_int(r)

        # ── 3. Rows changed — CTE approach avoids hash(alias.*) limitation ────
        r = _sql(f"{hash_cte} SELECT COUNT(*) FROM ha JOIN hb ON {key_join} WHERE ha._rh != hb._rh")
        result.rows_changed = _scalar_int(r)

        # ── 4. Rows identical ─────────────────────────────────────────────────
        r = _sql(f"{hash_cte} SELECT COUNT(*) FROM ha JOIN hb ON {key_join} WHERE ha._rh = hb._rh")
        result.rows_identical = _scalar_int(r)

        # ── 5. Column names from table A ──────────────────────────────────────
        r = _sql(f"SELECT * FROM {a} LIMIT 1")
        if r.manifest and r.manifest.schema:
            result.col_names = [c.name for c in r.manifest.schema.columns]
        result.col_names_changed = ["_side"] + result.col_names

        # ── 6. Sample removed rows ────────────────────────────────────────────
        if result.rows_only_in_a > 0:
            r = _sql(f"""
                SELECT a.* FROM {a} a LEFT JOIN {b} b ON {join_on}
                WHERE {null_b} LIMIT {max_sample}
            """)
            result.sample_removed = (r.result.data_array or []) if r.result else []

        # ── 7. Sample added rows ──────────────────────────────────────────────
        if result.rows_only_in_b > 0:
            r = _sql(f"""
                SELECT b.* FROM {b} b LEFT JOIN {a} a ON {join_on}
                WHERE {null_a} LIMIT {max_sample}
            """)
            result.sample_added = (r.result.data_array or []) if r.result else []

        # ── 8. Sample changed rows — two queries, interleaved in Python ────────
        # (LIMIT before UNION ALL is invalid in Databricks SQL)
        if result.rows_changed > 0:
            ck_join = " AND ".join(f"ck.`{k}` <=> src.`{k}`" for k in key_columns)
            # Qualify with ha. to avoid ambiguous column reference when both
            # ha and hb have the same key column names.
            ha_key_sel = ", ".join(f"ha.`{k}`" for k in key_columns)
            changed_cte = (
                f"{hash_cte},\n"
                f"ck AS (SELECT {ha_key_sel} FROM ha JOIN hb ON {key_join} "
                f"WHERE ha._rh != hb._rh LIMIT {max_sample})"
            )
            r_a = _sql(f"{changed_cte} SELECT src.* FROM {a} src JOIN ck ON {ck_join}")
            r_b = _sql(f"{changed_cte} SELECT src.* FROM {b} src JOIN ck ON {ck_join}")
            rows_a = (r_a.result.data_array or []) if r_a.result else []
            rows_b = (r_b.result.data_array or []) if r_b.result else []

            interleaved: list[list] = []
            for ra, rb in zip(rows_a, rows_b):
                interleaved.append(["A"] + list(ra))
                interleaved.append(["B"] + list(rb))
            result.sample_changed = interleaved

    except Exception as exc:  # noqa: BLE001
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# Pure helpers — testable without Databricks


def summarise(result: RowDiffResult) -> dict[str, Any]:
    return {
        "key_columns":     result.key_columns,
        "rows_only_in_a":  result.rows_only_in_a,
        "rows_only_in_b":  result.rows_only_in_b,
        "rows_changed":    result.rows_changed,
        "rows_identical":  result.rows_identical,
        "has_differences": result.has_differences,
        "error":           result.error,
    }


def diff_pct(changed: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{changed / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Internal SQL execution


def _sql(statement: str):
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
    if result.result and result.result.data_array:
        return int(result.result.data_array[0][0])
    return 0
