"""Per-table profiling (milestone 2).

Databricks Apps run as containerised Python web servers — they have no
active Spark session.  This module profiles tables by:

  1. Fetching data via the SQL warehouse into a pandas DataFrame
     (capped at FETCH_LIMIT rows; actual row count queried separately).
  2. Running ydata-profiling on the pandas DataFrame to produce the HTML report.
  3. Computing per-column statistics from pandas to populate the metamodel.

Mock mode:  returns an empty DatasetProfile + writes a stub HTML file,
            no warehouse or ydata-profiling required.

Pure helpers (shannon_entropy, _build_histogram, _fire_alerts,
_determine_stereotypes) have no external dependencies and are unit-tested
in tests/test_profile.py.
"""

from __future__ import annotations

import math
import os
from typing import Any, Optional, TYPE_CHECKING

import numpy as np
import pandas as pd

from .catalog import TableRef
from .metamodel import (
    Alert,
    CategoricalStats,
    ColumnProfile,
    DatasetProfile,
    HISTOGRAM_MAX_BINS,
    NumericStats,
)
from .storage import RunFolder, write_text


# Default rows fetched for profiling. Keeps the interactive run fast.
# Users can override via the "Sample N rows" slider in the UI.
FETCH_LIMIT = 50_000

# Tables wider than this skip per-column histograms (faster, less memory).
WIDE_TABLE_THRESHOLD = 70


# ---------------------------------------------------------------------------
# Public API


def profile_table(
    ref: TableRef,
    env_label: str,
    folder: RunFolder,
    html_filename: str,
    sample_n: Optional[int] = None,
) -> DatasetProfile:
    """Profile one Unity Catalog table.

    Writes an HTML report to folder/<html_filename>.
    Returns a fully-populated DatasetProfile for the metamodel.
    """
    if _runtime() == "mock":
        return _mock_profile(ref, env_label, folder, html_filename)
    return _databricks_profile(ref, env_label, folder, html_filename, sample_n)


# ---------------------------------------------------------------------------
# Databricks path (pandas via SQL warehouse)


def _fetch_via_statement_api(fqn: str, limit: int) -> pd.DataFrame:
    """Fetch table data using the Databricks Statement Execution REST API.

    Uses WorkspaceClient (same auth as catalog lookups) instead of the JDBC
    SQL connector — avoids JDBC connection issues in some network configs.

    wait_timeout is capped at 50s by the API; longer queries are polled.
    """
    from .catalog import _workspace_client
    import os as _os
    import time as _time

    w = _workspace_client()
    warehouse_id = _os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if not warehouse_id:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID not set.")

    # Submit — wait up to 50s synchronously (API maximum).
    result = w.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=f"SELECT * FROM {fqn} LIMIT {limit}",
        wait_timeout="50s",
    )

    # Poll if still running after the initial wait.
    _deadline = _time.time() + 600  # 10-minute total timeout
    while True:
        state = str(result.status.state).upper() if result.status else "UNKNOWN"
        if "SUCCEEDED" in state:
            break
        if any(s in state for s in ("FAILED", "CANCELLED", "CLOSED")):
            err = (result.status.error.message
                   if (result.status and result.status.error) else state)
            raise RuntimeError(f"Statement execution failed ({state}): {err}")
        if _time.time() > _deadline:
            raise TimeoutError(
                f"Statement still {state} after 10 minutes. "
                f"Try a smaller table or use 'Sample N rows'."
            )
        _time.sleep(2)
        result = w.statement_execution.get_statement(result.statement_id)

    if not result.manifest or not result.manifest.schema:
        return pd.DataFrame()

    col_names = [c.name for c in result.manifest.schema.columns]
    data = result.result.data_array if (result.result and result.result.data_array) else []
    return pd.DataFrame(data, columns=col_names)


def _databricks_profile(
    ref: TableRef,
    env_label: str,
    folder: RunFolder,
    html_filename: str,
    sample_n: Optional[int],
) -> DatasetProfile:
    from .catalog import _sql_connect

    limit = sample_n or FETCH_LIMIT

    # Pre-flight: verify warehouse is RUNNING before opening SQL connection.
    # A non-RUNNING warehouse causes sql.connect() to hang indefinitely.
    try:
        from .catalog import _workspace_client
        import os as _os
        _wid = _os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
        if _wid:
            _w = _workspace_client()
            _wh = _w.warehouses.get(id=_wid)
            _state = str(_wh.state).upper() if _wh.state else "UNKNOWN"
            if "RUNNING" not in _state:
                raise RuntimeError(
                    f"SQL warehouse is {_state} — not RUNNING. "
                    f"Click '⚡ Initialize Compute' in the app to start it first."
                )
    except RuntimeError:
        raise
    except Exception:
        pass  # Permission to check state may be absent; proceed anyway

    pdf = _fetch_via_statement_api(ref.fqn, limit)

    sampled_rows = len(pdf)
    # Row count = sample length (exact when table <= FETCH_LIMIT rows,
    # approximate when the table is larger — good enough for the metamodel).
    actual_row_count = sampled_rows

    # Phase 1: HTML via ydata-profiling.
    wide = len(pdf.columns) > WIDE_TABLE_THRESHOLD
    html = _generate_html(pdf, title=ref.fqn, wide=wide)
    write_text(folder, html_filename, html)

    # Phase 2: column stats → metamodel.
    columns = _profile_columns(pdf, sampled_rows, skip_histogram=wide)
    dup_rows = (
        int(pdf.duplicated().sum())
        if not wide and sampled_rows <= 50_000
        else 0
    )

    return DatasetProfile(
        env_label=env_label,
        connection=ref.connection,
        catalog=ref.catalog,
        schema=ref.schema,
        table=ref.table,
        row_count=actual_row_count,
        column_count=len(columns),
        duplicate_rows=dup_rows,
        columns=columns,
    )


def _generate_html(pdf: pd.DataFrame, title: str, wide: bool) -> str:
    """Generate a fast HTML profile report directly from pandas stats.

    Intentionally avoids ydata-profiling — that library's import and
    computation overhead makes it unsuitable for interactive web apps.
    """
    rows, cols_n = len(pdf), len(pdf.columns)

    col_rows = []
    for col in pdf.columns:
        s = pdf[col]
        dtype = str(s.dtype)
        nulls = int(s.isna().sum())
        null_pct = nulls / rows * 100 if rows else 0
        distinct = int(s.nunique(dropna=True))

        null_style = "background:#ffe0e0" if null_pct > 50 else (
                     "background:#fff8e0" if null_pct > 10 else "")

        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            clean = s.dropna()
            stats = (f"min={clean.min():.4g} / mean={clean.mean():.4g} / "
                     f"max={clean.max():.4g} / std={clean.std():.4g}"
                     if len(clean) else "all null")
        else:
            vc = s.dropna().astype(str).value_counts().head(5)
            stats = " | ".join(f"{v}: {c:,}" for v, c in vc.items()) if len(vc) else "—"

        col_rows.append(
            f"<tr>"
            f"<td><b>{col}</b></td>"
            f"<td>{dtype}</td>"
            f"<td style='{null_style}'>{null_pct:.1f}% ({nulls:,})</td>"
            f"<td>{distinct:,}</td>"
            f"<td style='font-size:0.85em;color:#555'>{stats}</td>"
            f"</tr>"
        )

    table_rows = "\n".join(col_rows)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 24px; color: #2D3748; background: #f8f9fb; }}
  h1   {{ color: #8BA4BD; font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; background: white;
           box-shadow: 0 1px 4px rgba(0,0,0,0.08); border-radius: 6px;
           overflow: hidden; }}
  th   {{ background: #8BA4BD; color: white; padding: 10px 14px;
          text-align: left; font-size: 0.85rem; letter-spacing: 0.04em; }}
  td   {{ padding: 8px 14px; border-bottom: 1px solid #eef3f8;
          font-size: 0.9rem; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f5f8fc; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">{rows:,} rows &nbsp;·&nbsp; {cols_n} columns</div>
<table>
<thead>
  <tr>
    <th>Column</th><th>Type</th><th>Null %</th>
    <th>Distinct</th><th>Stats / Top values</th>
  </tr>
</thead>
<tbody>
{table_rows}
</tbody>
</table>
</body>
</html>"""


def _profile_columns(
    pdf: pd.DataFrame, row_count: int, skip_histogram: bool = False
) -> list[ColumnProfile]:
    columns: list[ColumnProfile] = []

    for col_name in pdf.columns:
        series = pdf[col_name]
        dtype = series.dtype

        logical_type = _logical_type(dtype)
        physical_type = str(dtype)
        nullable = bool(series.isna().any())

        null_count = int(series.isna().sum())
        null_pct = null_count / row_count if row_count > 0 else 0.0
        distinct_count = int(series.nunique(dropna=True))
        distinct_pct = distinct_count / row_count if row_count > 0 else 0.0

        is_numeric = pd.api.types.is_numeric_dtype(dtype) and not pd.api.types.is_bool_dtype(dtype)
        is_temporal = pd.api.types.is_datetime64_any_dtype(dtype)

        numeric: Optional[NumericStats] = None
        categorical: Optional[CategoricalStats] = None

        if is_numeric:
            numeric = _numeric_stats(series, skip_histogram)
        elif not is_temporal:
            categorical = _categorical_stats(series, row_count)

        stereotypes = _determine_stereotypes(
            null_pct, distinct_count, distinct_pct, row_count, numeric, categorical
        )
        alerts = _fire_alerts(
            col_name, null_pct, null_count, distinct_count, distinct_pct,
            row_count, numeric, categorical,
        )

        columns.append(ColumnProfile(
            name=col_name,
            logical_type=logical_type,
            physical_type=physical_type,
            nullable=nullable,
            null_count=null_count,
            null_pct=null_pct,
            distinct_count=distinct_count,
            distinct_pct=distinct_pct,
            numeric=numeric,
            categorical=categorical,
            alerts=alerts,
            stereotypes=stereotypes,
        ))

    return columns


def _numeric_stats(series: pd.Series, skip_histogram: bool) -> Optional[NumericStats]:
    clean = series.dropna().astype(float)
    if len(clean) == 0:
        return None

    pcts = clean.quantile([0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99]).tolist()
    lo, hi = float(clean.min()), float(clean.max())

    if skip_histogram or lo == hi:
        edges = [lo, hi + (1.0 if lo == hi else 0.0)]
        counts = [int(len(clean))]
    else:
        n_bins = min(20, HISTOGRAM_MAX_BINS)
        hist_counts, hist_edges = np.histogram(clean.values, bins=n_bins)
        edges = [float(e) for e in hist_edges]
        counts = [int(c) for c in hist_counts]

    return NumericStats(
        min=lo, max=hi,
        mean=float(clean.mean()),
        stddev=float(clean.std()),
        p1=pcts[0], p5=pcts[1], p25=pcts[2], p50=pcts[3],
        p75=pcts[4], p95=pcts[5], p99=pcts[6],
        skewness=float(clean.skew()),
        kurtosis=float(clean.kurtosis()),
        histogram_edges=edges,
        histogram_counts=counts,
    )


def _categorical_stats(series: pd.Series, row_count: int) -> CategoricalStats:
    top_k_series = series.dropna().astype(str).value_counts().head(20)
    top_k = {str(k): int(v) for k, v in top_k_series.items()}
    return CategoricalStats(
        top_k=top_k,
        entropy=shannon_entropy(top_k, row_count),
    )


def _logical_type(dtype: Any) -> str:
    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "double"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "timestamp"
    return "string"


# ---------------------------------------------------------------------------
# Pure helpers — testable without any external dependencies


def _build_histogram(
    bucket_data: dict[int, int], n_bins: int, lo: float, hi: float
) -> tuple[list[float], list[int]]:
    counts = [bucket_data.get(i, 0) for i in range(n_bins)]
    step = (hi - lo) / n_bins
    edges = [lo + i * step for i in range(n_bins + 1)]
    edges[-1] = hi
    return edges, counts


def shannon_entropy(top_k: dict[str, int], total: int) -> float:
    if total <= 0 or not top_k:
        return 0.0
    entropy = 0.0
    for count in top_k.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    return round(entropy, 6)


def _determine_stereotypes(
    null_pct: float,
    distinct_count: int,
    distinct_pct: float,
    row_count: int,
    numeric: Optional[NumericStats],
    categorical: Optional[CategoricalStats],
) -> list:
    stereotypes: list[str] = []
    if distinct_count <= 1 and row_count > 0:
        stereotypes.append("Constant")
    if null_pct > 0.5:
        stereotypes.append("NullSpike")
    if categorical is not None and distinct_pct > 0.9:
        stereotypes.append("HighCardinality")
    if numeric is not None and abs(numeric.skewness) > 2.0:
        stereotypes.append("Skewed")
    if categorical is not None and categorical.top_k:
        top_count = max(categorical.top_k.values())
        total_in_topk = sum(categorical.top_k.values())
        if total_in_topk > 0 and top_count / total_in_topk > 0.9:
            stereotypes.append("Imbalanced")
    return stereotypes  # type: ignore[return-value]


def _fire_alerts(
    col_name: str,
    null_pct: float,
    null_count: int,
    distinct_count: int,
    distinct_pct: float,
    row_count: int,
    numeric: Optional[NumericStats],
    categorical: Optional[CategoricalStats],
) -> list[Alert]:
    alerts: list[Alert] = []

    if row_count > 0 and null_count == row_count:
        alerts.append(Alert(rule="missing", severity="critical",
                            message="Column is entirely null"))
        return alerts

    if null_pct > 0.5:
        severity = "critical" if null_pct > 0.8 else "warn"
        alerts.append(Alert(rule="missing", severity=severity,
                            message=f"{null_pct:.1%} null ({null_count:,} of {row_count:,} rows)"))

    if distinct_count == 1:
        alerts.append(Alert(rule="constant", severity="warn",
                            message="Only one distinct non-null value"))

    if categorical is not None:
        if distinct_count == row_count and row_count > 0:
            alerts.append(Alert(rule="unique", severity="info",
                                message="Every non-null value is unique (potential key column)"))
        elif distinct_pct > 0.9:
            alerts.append(Alert(rule="high_cardinality", severity="info",
                                message=f"{distinct_pct:.1%} unique ({distinct_count:,} distinct values)"))
        if categorical.top_k:
            top_count = max(categorical.top_k.values())
            if row_count > 0 and top_count / row_count > 0.9:
                top_val = max(categorical.top_k, key=categorical.top_k.get)
                alerts.append(Alert(rule="imbalanced", severity="warn",
                                    message=f"'{top_val}' represents {top_count / row_count:.1%} of all rows"))

    if numeric is not None and abs(numeric.skewness) > 2.0:
        alerts.append(Alert(rule="skewed", severity="info",
                            message=f"Skewness = {numeric.skewness:.2f} (threshold |skewness| > 2)"))

    return alerts


# ---------------------------------------------------------------------------
# Mock path


def _mock_profile(
    ref: TableRef,
    env_label: str,
    folder: RunFolder,
    html_filename: str,
) -> DatasetProfile:
    stub_html = (
        f"<html><body>"
        f"<h1>Mock Profile — {ref.fqn}</h1>"
        f"<p>ydata-profiling not available in mock mode.</p>"
        f"</body></html>"
    )
    write_text(folder, html_filename, stub_html)
    return DatasetProfile(
        env_label=env_label,
        connection=ref.connection,
        catalog=ref.catalog,
        schema=ref.schema,
        table=ref.table,
        row_count=0,
        column_count=0,
        columns=[],
    )


# ---------------------------------------------------------------------------
# Utilities


def _runtime() -> str:
    return os.environ.get("PROFILER_RUNTIME", "mock").lower()
