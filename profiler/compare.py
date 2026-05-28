"""Schema diff and aggregate-stat diff (milestone 3).

Public API:
    compare_tables(side_a, side_b) -> list[ColumnComparison]
        Full comparison pipeline. Returns one ColumnComparison per unique
        column name across both sides, with schema_change and verdict set.
        PSI / KS / Chi-square / JS divergence are populated in milestone 4.

    schema_diff(side_a, side_b) -> list[ColumnComparison]
        Detects added / removed / type_changed / nullability_changed / unchanged.

    stat_diff(side_a, side_b, comparisons) -> list[ColumnComparison]
        Milestone 3: returns comparisons unchanged (drift metrics added in M4).

Row-level diff is milestone 6.
"""

from __future__ import annotations

from typing import Optional

from .metamodel import (
    ColumnComparison,
    DatasetProfile,
    SchemaChange,
    Stereotype,
    verdict_from_psi,
)


# ---------------------------------------------------------------------------
# Public API


def compare_tables(
    side_a: DatasetProfile,
    side_b: DatasetProfile,
) -> list[ColumnComparison]:
    """Run the full comparison pipeline and return ColumnComparisons.

    Milestone 3: schema diff only — no drift metrics.
    Milestone 4 will call stat_diff after this to add PSI/KS/etc.
    """
    comparisons = schema_diff(side_a, side_b)
    return stat_diff(side_a, side_b, comparisons)


def schema_diff(
    side_a: DatasetProfile,
    side_b: DatasetProfile,
) -> list[ColumnComparison]:
    """Detect schema changes column-by-column.

    Column ordering: Side-A columns first (in A order), then columns only
    present in B (in B order).  This matches how a human reads a diff —
    Side A is the baseline; Side B is the candidate.
    """
    a_map = {c.name: c for c in side_a.columns}
    b_map = {c.name: c for c in side_b.columns}

    # Preserve A-order first, then B-only columns.
    ordered: list[str] = [c.name for c in side_a.columns]
    ordered += [c.name for c in side_b.columns if c.name not in a_map]

    comparisons: list[ColumnComparison] = []
    for col_name in ordered:
        col_a = a_map.get(col_name)
        col_b = b_map.get(col_name)

        change, stereotypes = _classify_change(col_a, col_b)
        verdict = verdict_from_psi(None, schema_change=change)

        comparisons.append(
            ColumnComparison(
                column_name=col_name,
                schema_change=change,
                verdict=verdict,
                stereotypes=stereotypes,
            )
        )

    return comparisons


def stat_diff(
    side_a: DatasetProfile,
    side_b: DatasetProfile,
    comparisons: list[ColumnComparison],
) -> list[ColumnComparison]:
    """Milestone 3: returns comparisons unchanged.

    Milestone 4 enriches each ColumnComparison with psi, ks_stat,
    ks_pvalue, chi_square, js_divergence, and updates verdict accordingly.
    """
    return comparisons


# ---------------------------------------------------------------------------
# Helpers


def _classify_change(
    col_a: Optional[object],
    col_b: Optional[object],
) -> tuple[SchemaChange, list[Stereotype]]:
    """Return (schema_change, stereotypes) for a column pair."""
    if col_a is None:
        return "added", ["SchemaChanged"]
    if col_b is None:
        return "removed", ["SchemaChanged"]

    if col_a.logical_type != col_b.logical_type:  # type: ignore[union-attr]
        return "type_changed", ["SchemaChanged"]

    if col_a.nullable != col_b.nullable:  # type: ignore[union-attr]
        return "nullability_changed", ["SchemaChanged"]

    return "unchanged", []


# ---------------------------------------------------------------------------
# Summary helpers used by excel.py


def schema_change_counts(comparisons: list[ColumnComparison]) -> dict[str, int]:
    """Return counts keyed by SchemaChange value."""
    counts: dict[str, int] = {
        "added": 0,
        "removed": 0,
        "type_changed": 0,
        "nullability_changed": 0,
        "unchanged": 0,
    }
    for c in comparisons:
        counts[c.schema_change] = counts.get(c.schema_change, 0) + 1
    return counts
