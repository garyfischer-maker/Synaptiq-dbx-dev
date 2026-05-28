"""Mermaid diagram renderer (milestone 4.5.2).

Generates three .mmd strings from a ProfilerRun:
    1. side_a_schema  — Side-A table with per-column stats and alerts
    2. side_b_schema  — Side-B table (same shape as Side-A)
    3. drift_only     — Columns with non-stable verdicts and cross-side links

All three serialize from the same ProfilerRun object graph — the metamodel
is the single source of truth.  Callers should write the strings as
`schema_a.mmd`, `schema_b.mmd`, `drift.mmd` in the run folder.
"""

from __future__ import annotations

import re
from typing import Optional

from profiler.metamodel import (
    ColumnComparison,
    ColumnProfile,
    DatasetProfile,
    ProfilerRun,
)

# Tables wider than this render in summary mode (alerted columns only).
SUMMARY_MODE_THRESHOLD = 30


# ---------------------------------------------------------------------------
# Public API


def render_all(run: ProfilerRun) -> tuple[str, str, str]:
    """Return (side_a_schema, side_b_schema, drift_only) as Mermaid strings."""
    return (
        render_side_schema(run.side_a),
        render_side_schema(run.side_b),
        render_drift(run),
    )


def render_side_schema(profile: DatasetProfile) -> str:
    """Mermaid classDiagram for one side: table node + per-column nodes."""
    wide = profile.column_count > SUMMARY_MODE_THRESHOLD
    visible = [c for c in profile.columns if not wide or c.alerts]

    lines: list[str] = ["classDiagram"]
    tbl_id = _tbl_id(profile)
    lines += _table_class(tbl_id, profile, summary=wide)

    for col in visible:
        col_id = f"{tbl_id}_{_safe(col.name)}"
        lines += _column_class(col_id, col)
        lines.append(f"    {tbl_id} --> {col_id}")

    if wide and not visible:
        lines.append(
            f'    note for {tbl_id} "All {profile.column_count} columns — no alerts"'
        )

    return "\n".join(lines)


def render_drift(run: ProfilerRun) -> str:
    """Mermaid classDiagram showing only columns with non-stable verdicts."""
    drifted = [c for c in run.comparisons if c.verdict != "stable"]

    lines: list[str] = ["classDiagram"]

    if not drifted:
        a_id = _tbl_id(run.side_a)
        b_id = _tbl_id(run.side_b)
        lines += _table_class(a_id, run.side_a)
        lines += _table_class(b_id, run.side_b)
        lines.append(f"    {a_id} --> {b_id} : all stable")
        return "\n".join(lines)

    a_cols = {c.name: c for c in run.side_a.columns}
    b_cols = {c.name: c for c in run.side_b.columns}

    for cmp in drifted:
        a_id = f"sideA_{_safe(cmp.column_name)}"
        b_id = f"sideB_{_safe(cmp.column_name)}"
        lines += _drift_col_class(a_id, "A", cmp, a_cols.get(cmp.column_name))
        lines += _drift_col_class(b_id, "B", cmp, b_cols.get(cmp.column_name))
        lines.append(f"    {a_id} --> {b_id} : {_drift_label(cmp)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helpers


def _safe(name: str) -> str:
    """Return a Mermaid-safe identifier component (alphanumeric + underscore)."""
    out = re.sub(r"[^a-zA-Z0-9]", "_", name)
    if out and out[0].isdigit():
        out = "x" + out
    return out or "x"


def _tbl_id(profile: DatasetProfile) -> str:
    return (
        f"tbl_{_safe(profile.catalog)}"
        f"_{_safe(profile.schema_)}"
        f"_{_safe(profile.table)}"
    )


def _table_class(
    tbl_id: str, profile: DatasetProfile, summary: bool = False
) -> list[str]:
    lines = [
        f"    class {tbl_id} {{",
        f"        <<Table>>",
        f"        catalog : {profile.catalog}",
        f"        schema : {profile.schema_}",
        f"        table : {profile.table}",
        f"        row_count : {profile.row_count:,}",
        f"        column_count : {profile.column_count}",
    ]
    if profile.duplicate_rows:
        lines.append(f"        duplicate_rows : {profile.duplicate_rows:,}")
    if summary:
        lines.append("        summary_mode : alerts only")
    lines.append("    }")
    return lines


def _column_class(col_id: str, col: ColumnProfile) -> list[str]:
    stereotype = "_".join(col.stereotypes) if col.stereotypes else "MeasuredAttribute"
    null_pct = f"{col.null_pct * 100:.1f} pct"
    distinct_pct = f"{col.distinct_pct * 100:.1f} pct"

    lines = [
        f"    class {col_id} {{",
        f"        <<{stereotype}>>",
        f"        type : {col.logical_type}",
        f"        nullable : {str(col.nullable).lower()}",
        f"        null_pct : {null_pct}",
        f"        distinct_pct : {distinct_pct}",
    ]
    if col.numeric:
        n = col.numeric
        lines += [
            f"        mean : {n.mean:.4g}",
            f"        stddev : {n.stddev:.4g}",
            f"        p50 : {n.p50:.4g}",
        ]
    elif col.categorical:
        lines.append(f"        entropy : {col.categorical.entropy:.4g}")
        if col.categorical.top_k:
            top1_key = next(iter(col.categorical.top_k))
            lines.append(f"        top1 : {top1_key} ({col.categorical.top_k[top1_key]:,})")
    for alert in col.alerts:
        lines.append(f"        ALERT : {alert.severity} {alert.rule}")
    lines.append("    }")
    return lines


def _drift_col_class(
    col_id: str,
    side: str,
    cmp: ColumnComparison,
    col: Optional[ColumnProfile],
) -> list[str]:
    ltype = col.logical_type if col else "removed"
    stereotype = f"Side{side}"
    if cmp.schema_change != "unchanged":
        stereotype += f"_{cmp.schema_change}"

    lines = [
        f"    class {col_id} {{",
        f"        <<{stereotype}>>",
        f"        column : {cmp.column_name}",
        f"        type : {ltype}",
    ]
    if col and col.numeric:
        n = col.numeric
        lines += [
            f"        mean : {n.mean:.4g}",
            f"        p50 : {n.p50:.4g}",
        ]
    elif col and col.categorical:
        lines.append(f"        entropy : {col.categorical.entropy:.4g}")
    lines.append("    }")
    return lines


def _drift_label(cmp: ColumnComparison) -> str:
    parts: list[str] = []
    if cmp.schema_change != "unchanged":
        parts.append(cmp.schema_change)
    else:
        parts.append(f"Drifted {cmp.verdict}")
    if cmp.psi is not None:
        parts.append(f"PSI {cmp.psi:.3f}")
    return " ".join(parts)
