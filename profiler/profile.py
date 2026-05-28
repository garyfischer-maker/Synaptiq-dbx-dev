"""Per-table profiling (milestone 2).

Two-phase execution:
    Phase 1 — ydata-profiling ProfileReport  → HTML artifact written to run folder
    Phase 2 — PySpark aggregate queries      → DatasetProfile for the metamodel

Databricks runtime: both phases run against the real Spark session.
Mock runtime:       stub HTML written; empty DatasetProfile returned so the
                    rest of the pipeline (metamodel, Mermaid, Delta repo) can
                    still exercise their code paths locally.

Design notes:
- All Spark and ydata-profiling imports are lazy (inside functions) so the
  module can be imported in the test environment without PySpark installed.
- Column profiling fan-out: each column makes 1–2 Spark passes (base stats +
  histogram).  For very wide tables (>50 columns) the histogram is skipped to
  keep run time reasonable — the metamodel stores an empty histogram in that
  case.
- duplicate_rows is skipped for wide (>30 cols) or large (>200k rows) tables
  because df.distinct().count() is expensive.
"""

from __future__ import annotations

import math
import os
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession

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
# Databricks path


def _databricks_profile(
    ref: TableRef,
    env_label: str,
    folder: RunFolder,
    html_filename: str,
    sample_n: Optional[int],
) -> DatasetProfile:
    spark = _get_spark()
    df = spark.table(ref.fqn)
    row_count = df.count()

    if sample_n is not None and row_count > sample_n:
        fraction = sample_n / row_count
        df = df.sample(fraction=fraction, seed=42)
        row_count = sample_n  # approximate post-sample count

    # Phase 1: HTML via ydata-profiling
    html = _generate_html(df, title=ref.fqn, wide=len(df.columns) > 30)
    write_text(folder, html_filename, html)

    # Phase 2: Spark stats → metamodel objects
    columns = _profile_all_columns(df, row_count)
    dup_rows = _count_duplicates(df, row_count)

    return DatasetProfile(
        env_label=env_label,
        connection=ref.connection,
        catalog=ref.catalog,
        schema=ref.schema,
        table=ref.table,
        row_count=row_count,
        column_count=len(columns),
        duplicate_rows=dup_rows,
        columns=columns,
    )


def _generate_html(df: "DataFrame", title: str, wide: bool) -> str:
    """Run ydata-profiling and return the HTML string."""
    from ydata_profiling import ProfileReport

    # Disable expensive correlation and interactions passes for wide tables.
    kwargs: dict[str, Any] = {"title": title, "lazy": False}
    if wide:
        kwargs["minimal"] = True
    report = ProfileReport(df, **kwargs)
    return report.to_html()


def _profile_all_columns(df: "DataFrame", row_count: int) -> list[ColumnProfile]:
    from pyspark.sql import functions as F
    from pyspark.sql.types import (
        ByteType,
        DateType,
        DecimalType,
        DoubleType,
        FloatType,
        IntegerType,
        LongType,
        ShortType,
        TimestampType,
    )

    skip_histogram = len(df.columns) > 50
    columns: list[ColumnProfile] = []

    for field in df.schema.fields:
        col_name = field.name
        dtype = field.dataType

        logical_type = _logical_type(dtype)
        physical_type = str(dtype)

        # Base null + distinct stats (one pass per column).
        base = df.select(
            (F.count("*") - F.count(F.col(col_name))).alias("null_count"),
            F.approx_count_distinct(F.col(col_name)).alias("distinct_count"),
        ).collect()[0]

        null_count = int(base["null_count"])
        null_pct = null_count / row_count if row_count > 0 else 0.0
        distinct_count = int(base["distinct_count"])
        distinct_pct = distinct_count / row_count if row_count > 0 else 0.0

        is_numeric = isinstance(
            dtype,
            (IntegerType, LongType, ShortType, ByteType, FloatType, DoubleType, DecimalType),
        )
        is_temporal = isinstance(dtype, (DateType, TimestampType))

        numeric: Optional[NumericStats] = None
        categorical: Optional[CategoricalStats] = None

        if is_numeric:
            numeric = _numeric_stats(df, col_name, row_count, skip_histogram)
        elif not is_temporal:
            categorical = _categorical_stats(df, col_name, row_count)

        stereotypes = _determine_stereotypes(
            null_pct, distinct_count, distinct_pct, row_count, numeric, categorical
        )
        alerts = _fire_alerts(
            col_name, null_pct, null_count, distinct_count, distinct_pct,
            row_count, numeric, categorical,
        )

        columns.append(
            ColumnProfile(
                name=col_name,
                logical_type=logical_type,
                physical_type=physical_type,
                nullable=field.nullable,
                null_count=null_count,
                null_pct=null_pct,
                distinct_count=distinct_count,
                distinct_pct=distinct_pct,
                numeric=numeric,
                categorical=categorical,
                alerts=alerts,
                stereotypes=stereotypes,
            )
        )

    return columns


def _numeric_stats(
    df: "DataFrame", col_name: str, row_count: int, skip_histogram: bool
) -> Optional[NumericStats]:
    from pyspark.sql import functions as F

    result = df.select(
        F.mean(F.col(col_name).cast("double")).alias("mean"),
        F.stddev(F.col(col_name).cast("double")).alias("stddev"),
        F.min(F.col(col_name).cast("double")).alias("min"),
        F.max(F.col(col_name).cast("double")).alias("max"),
        F.skewness(F.col(col_name).cast("double")).alias("skewness"),
        F.kurtosis(F.col(col_name).cast("double")).alias("kurtosis"),
        F.percentile_approx(
            F.col(col_name).cast("double"),
            [0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99],
        ).alias("pcts"),
    ).collect()[0]

    if result["min"] is None:
        return None  # column is entirely null

    pcts = result["pcts"] or [0.0] * 7
    lo = float(result["min"])
    hi = float(result["max"])

    if skip_histogram or lo == hi:
        non_null = int(df.filter(F.col(col_name).isNotNull()).count())
        edges = [lo, hi + (1.0 if lo == hi else 0.0)]
        counts = [non_null]
    else:
        n_bins = min(20, HISTOGRAM_MAX_BINS)
        bucket_data = _spark_histogram_buckets(df, col_name, lo, hi, n_bins)
        edges, counts = _build_histogram(bucket_data, n_bins, lo, hi)

    return NumericStats(
        min=lo,
        max=hi,
        mean=float(result["mean"] or 0.0),
        stddev=float(result["stddev"] or 0.0),
        p1=float(pcts[0]),
        p5=float(pcts[1]),
        p25=float(pcts[2]),
        p50=float(pcts[3]),
        p75=float(pcts[4]),
        p95=float(pcts[5]),
        p99=float(pcts[6]),
        skewness=float(result["skewness"] or 0.0),
        kurtosis=float(result["kurtosis"] or 0.0),
        histogram_edges=edges,
        histogram_counts=counts,
    )


def _spark_histogram_buckets(
    df: "DataFrame", col_name: str, lo: float, hi: float, n_bins: int
) -> dict[int, int]:
    """Return {bucket_index: count} for non-null values in [lo, hi]."""
    from pyspark.sql import functions as F

    bucket_df = (
        df.select(
            F.least(
                F.floor(
                    ((F.col(col_name).cast("double") - lo) / (hi - lo)) * n_bins
                ).cast("int"),
                F.lit(n_bins - 1),
            ).alias("bucket")
        )
        .filter(F.col("bucket").isNotNull())
        .groupBy("bucket")
        .count()
        .orderBy("bucket")
    )
    return {row["bucket"]: row["count"] for row in bucket_df.collect()}


def _categorical_stats(
    df: "DataFrame", col_name: str, row_count: int
) -> CategoricalStats:
    from pyspark.sql import functions as F

    top_k_rows = (
        df.filter(F.col(col_name).isNotNull())
        .groupBy(col_name)
        .count()
        .orderBy("count", ascending=False)
        .limit(20)
        .collect()
    )
    top_k = {str(row[col_name]): int(row["count"]) for row in top_k_rows}
    return CategoricalStats(
        top_k=top_k,
        entropy=shannon_entropy(top_k, row_count),
    )


def _count_duplicates(df: "DataFrame", row_count: int) -> int:
    if len(df.columns) > 30 or row_count > 200_000:
        return 0
    return max(0, row_count - df.distinct().count())


# ---------------------------------------------------------------------------
# Pure helpers — testable without PySpark


def _build_histogram(
    bucket_data: dict[int, int], n_bins: int, lo: float, hi: float
) -> tuple[list[float], list[int]]:
    """Construct histogram edges and counts from pre-aggregated bucket data.

    bucket_data maps bucket index (0..n_bins-1) to row count.
    Returns (edges, counts) where len(edges) == len(counts) + 1.
    """
    counts = [bucket_data.get(i, 0) for i in range(n_bins)]
    step = (hi - lo) / n_bins
    edges = [lo + i * step for i in range(n_bins + 1)]
    edges[-1] = hi  # ensure exact upper boundary (avoids float drift)
    return edges, counts


def shannon_entropy(top_k: dict[str, int], total: int) -> float:
    """Shannon entropy (bits) computed from top-K frequency counts."""
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
        alerts.append(Alert(
            rule="missing",
            severity="critical",
            message="Column is entirely null",
        ))
        return alerts  # no further checks make sense

    if null_pct > 0.5:
        severity = "critical" if null_pct > 0.8 else "warn"
        alerts.append(Alert(
            rule="missing",
            severity=severity,
            message=f"{null_pct:.1%} null ({null_count:,} of {row_count:,} rows)",
        ))

    if distinct_count == 1:
        alerts.append(Alert(
            rule="constant",
            severity="warn",
            message="Only one distinct non-null value",
        ))

    if categorical is not None:
        if distinct_count == row_count and row_count > 0:
            alerts.append(Alert(
                rule="unique",
                severity="info",
                message="Every non-null value is unique (potential key column)",
            ))
        elif distinct_pct > 0.9:
            alerts.append(Alert(
                rule="high_cardinality",
                severity="info",
                message=f"{distinct_pct:.1%} unique ({distinct_count:,} distinct values)",
            ))

        if categorical.top_k:
            top_count = max(categorical.top_k.values())
            if row_count > 0 and top_count / row_count > 0.9:
                top_val = max(categorical.top_k, key=categorical.top_k.get)
                alerts.append(Alert(
                    rule="imbalanced",
                    severity="warn",
                    message=f"'{top_val}' represents {top_count / row_count:.1%} of all rows",
                ))

    if numeric is not None and abs(numeric.skewness) > 2.0:
        alerts.append(Alert(
            rule="skewed",
            severity="info",
            message=f"Skewness = {numeric.skewness:.2f} (threshold |skewness| > 2)",
        ))

    return alerts


def _logical_type(dtype: Any) -> str:
    """Map a PySpark DataType to a human-readable logical type string."""
    # Lazy import — only called in Databricks mode.
    from pyspark.sql.types import (
        ByteType,
        DateType,
        DecimalType,
        DoubleType,
        FloatType,
        IntegerType,
        LongType,
        ShortType,
        StringType,
        BooleanType,
        TimestampType,
        TimestampNTZType,
    )
    if isinstance(dtype, IntegerType):   return "integer"
    if isinstance(dtype, LongType):      return "bigint"
    if isinstance(dtype, ShortType):     return "smallint"
    if isinstance(dtype, ByteType):      return "tinyint"
    if isinstance(dtype, FloatType):     return "float"
    if isinstance(dtype, DoubleType):    return "double"
    if isinstance(dtype, DecimalType):   return f"decimal({dtype.precision},{dtype.scale})"
    if isinstance(dtype, StringType):    return "string"
    if isinstance(dtype, BooleanType):   return "boolean"
    if isinstance(dtype, DateType):      return "date"
    if isinstance(dtype, (TimestampType, TimestampNTZType)): return "timestamp"
    return str(dtype).lower().replace("type()", "")


# ---------------------------------------------------------------------------
# Mock path — runs locally without Spark / ydata-profiling


def _mock_profile(
    ref: TableRef,
    env_label: str,
    folder: RunFolder,
    html_filename: str,
) -> DatasetProfile:
    stub_html = (
        f"<html><body>"
        f"<h1>Mock Profile — {ref.fqn}</h1>"
        f"<p>ydata-profiling not available in mock mode "
        f"(PROFILER_RUNTIME=mock).</p>"
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


def _get_spark() -> "SparkSession":
    from pyspark.sql import SparkSession
    return SparkSession.getActiveSession()


def _runtime() -> str:
    return os.environ.get("PROFILER_RUNTIME", "mock").lower()
