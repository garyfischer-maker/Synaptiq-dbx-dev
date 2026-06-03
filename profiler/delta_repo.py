"""Delta governance repository (milestone 4.5.2).

Persists profiler run data to five Delta tables in the output catalog/schema.
The JSON metamodel file is the canonical artifact; these tables are rebuildable
from it.  Direct Delta writes from the app (no Auto Loader).

Table layout:
    profiler_runs        — one row per run (header + lineage)
    dataset_profiles     — one row per side (A/B) per run
    column_profiles      — one row per column per side per run
    column_alerts        — one row per alert per column per side per run
    column_comparisons   — one row per column comparison per run

All tables:
    - CLUSTER BY (Liquid Clustering) — no PARTITIONED BY
    - LIQUID CLUSTERING on (catalog, schema, table, column_name) where applicable
    - MERGE on run_id (UUIDv7) as the idempotent key — safe to re-ingest a run
    - Retention: 1 year; daily OPTIMIZE + weekly VACUUM handled externally
"""

from __future__ import annotations

import importlib
from datetime import date, datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .metamodel import ProfilerRun


# ---------------------------------------------------------------------------
# Table DDL (idempotent)

_CATALOG_SCHEMA_PLACEHOLDER = "{catalog}.{schema}"


def _ddl(catalog: str, schema: str) -> list[str]:
    """Return CREATE TABLE IF NOT EXISTS statements for all five tables."""
    q = f"`{catalog}`.`{schema}`"
    return [
        # ------------------------------------------------------------------
        f"""CREATE TABLE IF NOT EXISTS {q}.profiler_runs (
    run_id          STRING        NOT NULL,
    run_label       STRING,
    metamodel_version STRING      NOT NULL,
    created_utc     TIMESTAMP     NOT NULL,
    created_date    DATE          NOT NULL,
    side_a_fqn      STRING        NOT NULL,
    side_b_fqn      STRING        NOT NULL,
    lineage_json    STRING
)
USING DELTA
CLUSTER BY (run_id)
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')""",

        # ------------------------------------------------------------------
        f"""CREATE TABLE IF NOT EXISTS {q}.dataset_profiles (
    run_id          STRING        NOT NULL,
    side            STRING        NOT NULL,
    created_date    DATE          NOT NULL,
    env_label       STRING,
    connection      STRING,
    catalog         STRING        NOT NULL,
    schema          STRING        NOT NULL,
    table           STRING        NOT NULL,
    row_count       BIGINT,
    column_count    INT,
    duplicate_rows  BIGINT
)
USING DELTA
CLUSTER BY (catalog, schema, table)
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')""",

        # ------------------------------------------------------------------
        f"""CREATE TABLE IF NOT EXISTS {q}.column_profiles (
    run_id          STRING        NOT NULL,
    side            STRING        NOT NULL,
    created_date    DATE          NOT NULL,
    catalog         STRING        NOT NULL,
    schema          STRING        NOT NULL,
    table           STRING        NOT NULL,
    column_name     STRING        NOT NULL,
    logical_type    STRING,
    physical_type   STRING,
    nullable        BOOLEAN,
    null_count      BIGINT,
    null_pct        DOUBLE,
    distinct_count  BIGINT,
    distinct_pct    DOUBLE,
    stereotypes     STRING,
    numeric_mean    DOUBLE,
    numeric_stddev  DOUBLE,
    numeric_p50     DOUBLE,
    numeric_min     DOUBLE,
    numeric_max     DOUBLE,
    cat_entropy     DOUBLE,
    cat_top_k_json  STRING
)
USING DELTA
CLUSTER BY (catalog, schema, table, column_name)
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')""",

        # ------------------------------------------------------------------
        f"""CREATE TABLE IF NOT EXISTS {q}.column_alerts (
    run_id          STRING        NOT NULL,
    side            STRING        NOT NULL,
    created_date    DATE          NOT NULL,
    catalog         STRING        NOT NULL,
    schema          STRING        NOT NULL,
    table           STRING        NOT NULL,
    column_name     STRING        NOT NULL,
    rule            STRING        NOT NULL,
    severity        STRING        NOT NULL,
    message         STRING
)
USING DELTA
CLUSTER BY (catalog, schema, table, column_name)
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')""",

        # ------------------------------------------------------------------
        f"""CREATE TABLE IF NOT EXISTS {q}.column_comparisons (
    run_id          STRING        NOT NULL,
    created_date    DATE          NOT NULL,
    catalog_a       STRING,
    schema_a        STRING,
    table_a         STRING,
    catalog_b       STRING,
    schema_b        STRING,
    table_b         STRING,
    column_name     STRING        NOT NULL,
    schema_change   STRING,
    psi             DOUBLE,
    ks_stat         DOUBLE,
    ks_pvalue       DOUBLE,
    chi_square      DOUBLE,
    js_divergence   DOUBLE,
    verdict         STRING        NOT NULL,
    stereotypes     STRING
)
USING DELTA
CLUSTER BY (catalog_a, schema_a, table_a, column_name)
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')""",
    ]


# ---------------------------------------------------------------------------
# Flatten: ProfilerRun → dict[table_name, list[dict]]


def flatten(run: "ProfilerRun") -> dict[str, list[dict[str, Any]]]:
    """Return a dict keyed by table name, each value a list of row dicts.

    No I/O — pure transformation.  Use the returned dicts with ingest().
    """
    run_id = str(run.run_id)
    created_date = run.created_utc.date()
    lineage_json = run.lineage.model_dump_json(exclude_none=True)

    profiler_runs: list[dict[str, Any]] = [
        {
            "run_id": run_id,
            "run_label": run.run_label,
            "metamodel_version": run.metamodel_version,
            "created_utc": run.created_utc,
            "created_date": created_date,
            "side_a_fqn": run.side_a.fqn,
            "side_b_fqn": run.side_b.fqn,
            "lineage_json": lineage_json,
        }
    ]

    dataset_profiles: list[dict[str, Any]] = []
    column_profiles: list[dict[str, Any]] = []
    column_alerts: list[dict[str, Any]] = []

    for side_label, profile in (("A", run.side_a), ("B", run.side_b)):
        dataset_profiles.append(
            {
                "run_id": run_id,
                "side": side_label,
                "created_date": created_date,
                "env_label": profile.env_label,
                "connection": profile.connection,
                "catalog": profile.catalog,
                "schema": profile.schema_,
                "table": profile.table,
                "row_count": profile.row_count,
                "column_count": profile.column_count,
                "duplicate_rows": profile.duplicate_rows,
            }
        )
        for col in profile.columns:
            import json as _json

            col_base = {
                "run_id": run_id,
                "side": side_label,
                "created_date": created_date,
                "catalog": profile.catalog,
                "schema": profile.schema_,
                "table": profile.table,
                "column_name": col.name,
                "logical_type": col.logical_type,
                "physical_type": col.physical_type,
                "nullable": col.nullable,
                "null_count": col.null_count,
                "null_pct": col.null_pct,
                "distinct_count": col.distinct_count,
                "distinct_pct": col.distinct_pct,
                "stereotypes": ",".join(col.stereotypes) if col.stereotypes else None,
                "numeric_mean": col.numeric.mean if col.numeric else None,
                "numeric_stddev": col.numeric.stddev if col.numeric else None,
                "numeric_p50": col.numeric.p50 if col.numeric else None,
                "numeric_min": col.numeric.min if col.numeric else None,
                "numeric_max": col.numeric.max if col.numeric else None,
                "cat_entropy": col.categorical.entropy if col.categorical else None,
                "cat_top_k_json": (
                    _json.dumps(col.categorical.top_k) if col.categorical else None
                ),
            }
            column_profiles.append(col_base)

            for alert in col.alerts:
                column_alerts.append(
                    {
                        "run_id": run_id,
                        "side": side_label,
                        "created_date": created_date,
                        "catalog": profile.catalog,
                        "schema": profile.schema_,
                        "table": profile.table,
                        "column_name": col.name,
                        "rule": alert.rule,
                        "severity": alert.severity,
                        "message": alert.message,
                    }
                )

    import json as _json

    column_comparisons: list[dict[str, Any]] = [
        {
            "run_id": run_id,
            "created_date": created_date,
            "catalog_a": run.side_a.catalog,
            "schema_a": run.side_a.schema_,
            "table_a": run.side_a.table,
            "catalog_b": run.side_b.catalog,
            "schema_b": run.side_b.schema_,
            "table_b": run.side_b.table,
            "column_name": cmp.column_name,
            "schema_change": cmp.schema_change,
            "psi": cmp.psi,
            "ks_stat": cmp.ks_stat,
            "ks_pvalue": cmp.ks_pvalue,
            "chi_square": cmp.chi_square,
            "js_divergence": cmp.js_divergence,
            "verdict": cmp.verdict,
            "stereotypes": ",".join(cmp.stereotypes) if cmp.stereotypes else None,
        }
        for cmp in run.comparisons
    ]

    return {
        "profiler_runs": profiler_runs,
        "dataset_profiles": dataset_profiles,
        "column_profiles": column_profiles,
        "column_alerts": column_alerts,
        "column_comparisons": column_comparisons,
    }


# ---------------------------------------------------------------------------
# Ingest: write flattened rows to Delta via Spark MERGE


def _exec_sql(statement: str) -> None:
    """Execute one SQL statement via Statement Execution API (no JDBC)."""
    import os as _os, time as _time
    from .catalog import _workspace_client
    w = _workspace_client()
    wid = _os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    result = w.statement_execution.execute_statement(
        warehouse_id=wid,
        statement=statement,
        wait_timeout="50s",
    )
    deadline = _time.time() + 300
    while True:
        state = str(result.status.state).upper() if result.status else "UNKNOWN"
        if "SUCCEEDED" in state:
            return
        if any(s in state for s in ("FAILED", "CANCELLED", "CLOSED")):
            err = (result.status.error.message
                   if (result.status and result.status.error) else state)
            raise RuntimeError(f"SQL failed ({state}): {err}")
        if _time.time() > deadline:
            raise TimeoutError(f"SQL still {state} after 5 minutes")
        _time.sleep(2)
        result = w.statement_execution.get_statement(result.statement_id)


def ensure_tables(catalog: str, schema: str) -> None:
    """Create the five governance tables if they don't exist."""
    for stmt in _ddl(catalog, schema):
        _exec_sql(stmt)


def ingest(
    run: "ProfilerRun",
    catalog: str,
    schema: str,
) -> None:
    """Insert one ProfilerRun into the five governance tables.

    Idempotent: deletes any existing rows for this run_id before inserting.
    Uses batch INSERT (one statement per table, not one per row).
    """
    rows_by_table = flatten(run)
    run_id = str(run.run_id)
    q = f"`{catalog}`.`{schema}`"

    for table_name, table_rows in rows_by_table.items():
        if not table_rows:
            continue
        full_name = f"{q}.`{table_name}`"
        _exec_sql(f"DELETE FROM {full_name} WHERE run_id = '{run_id}'")

        # Batch INSERT — all rows in one statement.
        cols = list(table_rows[0].keys())
        col_list = ", ".join(f"`{c}`" for c in cols)
        value_rows = []
        for row in table_rows:
            vals = ", ".join(_sql_literal(row[c]) for c in cols)
            value_rows.append(f"({vals})")
        _exec_sql(
            f"INSERT INTO {full_name} ({col_list}) "
            f"VALUES {', '.join(value_rows)}"
        )


def _sql_literal(val: Any) -> str:
    """Convert a Python value to a safe SQL literal string."""
    from datetime import date, datetime
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, datetime):
        return f"TIMESTAMP '{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, date):
        return f"DATE '{val.strftime('%Y-%m-%d')}'"
    # String — escape single quotes.
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"
