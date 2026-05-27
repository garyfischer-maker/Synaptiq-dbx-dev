"""Per-table profiling (milestone 2).

Wraps ydata-profiling's Spark backend to produce an HTML profile report per
side. Writes the report into the run folder and returns the ProfileReport
object (kept in memory only long enough to call compare()).

Honest stub in milestone 1.
"""

from __future__ import annotations


def profile_table(*args, **kwargs):
    raise NotImplementedError(
        "profile_table is not implemented in milestone 1 — scheduled for "
        "milestone 2 (ydata-profiling Spark integration)."
    )


def compare_profiles(*args, **kwargs):
    raise NotImplementedError(
        "compare_profiles is not implemented in milestone 1 — scheduled for "
        "milestone 5 (ydata compare() side-by-side HTML)."
    )
