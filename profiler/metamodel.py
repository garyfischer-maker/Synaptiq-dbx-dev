"""Data Quality Metamodel (milestone 4.5.1).

Canonical Python representation of a profiler run, expressed as Pydantic v2
models. Every other output format (JSON file, Mermaid diagram, Delta repo
ingestion, future XMI export) serializes from these objects — the metamodel
is the single source of truth.

Design decisions (see project memory `DQ Metamodel design decisions`):

- METAMODEL_VERSION bumps on breaking schema changes. Emit
  `dq-metamodel-v<MAJOR>.schema.json` per major version into each run folder
  so consumers can validate against the shipped contract.
- run_id is UUIDv7 (RFC 9562): time-sortable, globally unique,
  retry-idempotent. Implemented inline to avoid a third-party dep.
- Histograms use parallel arrays capped at 50 bins. Stored as
  `(histogram_edges[N+1], histogram_counts[N])` rather than a list of
  `{edge, count}` objects — cheaper to serialize and Z-order.
- Stereotypes are JSON-tag strings (`["MeasuredAttribute","Drifted"]`), not
  formal UML Profile machinery.
- `verdict` derives from PSI thresholds but is stored explicitly for query
  speed; a validator enforces consistency.
- All models use `extra="ignore"` so old code can read new (minor-version)
  payloads without crashing. Major-version bumps are explicit breaks.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ---------------------------------------------------------------------------
# Constants

METAMODEL_VERSION: str = "1.0"
HISTOGRAM_MAX_BINS: int = 50

# Standard credit-risk PSI thresholds.
PSI_STABLE_MAX: float = 0.1     # PSI < 0.1 → stable
PSI_MODERATE_MAX: float = 0.2   # 0.1 ≤ PSI < 0.2 → moderate; ≥ 0.2 → significant


# ---------------------------------------------------------------------------
# Type aliases for the controlled vocabulary

Stereotype = Literal[
    "MeasuredAttribute",
    "HighCardinality",
    "Constant",
    "NullSpike",
    "Skewed",
    "Imbalanced",
    "HighCorrelation",
    "Drifted",
    "SchemaChanged",
]

AlertRule = Literal[
    "constant",
    "zero",
    "high_cardinality",
    "unique",
    "missing",
    "skewed",
    "imbalanced",
    "high_correlation",
    "infinite",
]

AlertSeverity = Literal["info", "warn", "critical"]

SchemaChange = Literal[
    "added",
    "removed",
    "type_changed",
    "nullability_changed",
    "unchanged",
]

Verdict = Literal["stable", "moderate", "significant", "schema_change"]


# ---------------------------------------------------------------------------
# Base class — shared config for every model

class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,        # accept either field name OR alias on input
        str_strip_whitespace=True,
        extra="ignore",               # forward-compat: drop unknown fields silently
    )


# ---------------------------------------------------------------------------
# Per-column statistics

class NumericStats(_Base):
    """Descriptive statistics for a numeric column.

    Histograms are stored as parallel arrays so that counts[i] = number of
    values in [edges[i], edges[i+1]). Therefore len(edges) == len(counts) + 1.
    """

    min: float
    max: float
    mean: float
    stddev: float
    p1: float
    p5: float
    p25: float
    p50: float
    p75: float
    p95: float
    p99: float
    skewness: float
    kurtosis: float
    histogram_edges: list[float] = Field(min_length=2, max_length=HISTOGRAM_MAX_BINS + 1)
    histogram_counts: list[int] = Field(min_length=1, max_length=HISTOGRAM_MAX_BINS)

    @model_validator(mode="after")
    def _check_histogram(self) -> "NumericStats":
        if len(self.histogram_edges) != len(self.histogram_counts) + 1:
            raise ValueError(
                f"histogram_edges must be exactly one longer than histogram_counts; "
                f"got {len(self.histogram_edges)} edges vs {len(self.histogram_counts)} counts"
            )
        if any(c < 0 for c in self.histogram_counts):
            raise ValueError("histogram_counts must be non-negative")
        if any(a > b for a, b in zip(self.histogram_edges, self.histogram_edges[1:])):
            raise ValueError("histogram_edges must be sorted non-decreasing")
        return self


class CategoricalStats(_Base):
    """Top-K frequencies and information-theoretic measures for a categorical column.

    `top_k` keys are stringified values; counts are absolute (not normalized).
    """

    top_k: dict[str, int] = Field(default_factory=dict, max_length=20)
    entropy: float = 0.0

    @field_validator("entropy")
    @classmethod
    def _entropy_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("entropy must be non-negative")
        return v

    @field_validator("top_k")
    @classmethod
    def _topk_counts_non_negative(cls, v: dict[str, int]) -> dict[str, int]:
        if any(c < 0 for c in v.values()):
            raise ValueError("top_k counts must be non-negative")
        return v


# ---------------------------------------------------------------------------
# Alerts

class Alert(_Base):
    rule: AlertRule
    severity: AlertSeverity
    message: str


# ---------------------------------------------------------------------------
# Column-level profile

class ColumnProfile(_Base):
    name: str
    logical_type: str          # e.g. "integer", "string", "decimal(10,2)"
    physical_type: str         # e.g. "INT", "STRING", "DECIMAL(10,2)"
    nullable: bool
    null_count: int = Field(ge=0)
    null_pct: float = Field(ge=0.0, le=1.0)
    distinct_count: int = Field(ge=0)
    distinct_pct: float = Field(ge=0.0, le=1.0)
    numeric: Optional[NumericStats] = None
    categorical: Optional[CategoricalStats] = None
    alerts: list[Alert] = Field(default_factory=list)
    stereotypes: list[Stereotype] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_stats_exclusive(self) -> "ColumnProfile":
        if self.numeric is not None and self.categorical is not None:
            raise ValueError(
                f"column {self.name!r}: cannot have both numeric and categorical stats"
            )
        return self


# ---------------------------------------------------------------------------
# Per-dataset (per-side) profile

class DatasetProfile(_Base):
    env_label: str
    connection: str
    catalog: str
    schema_: str = Field(alias="schema")    # `schema` is reserved-ish in some contexts
    table: str
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    duplicate_rows: int = Field(default=0, ge=0)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)

    @property
    def fqn(self) -> str:
        """Fully qualified table name: catalog.schema.table"""
        return f"{self.catalog}.{self.schema_}.{self.table}"

    @model_validator(mode="after")
    def _check_column_count_matches(self) -> "DatasetProfile":
        if self.column_count != len(self.columns):
            raise ValueError(
                f"column_count ({self.column_count}) != len(columns) ({len(self.columns)})"
            )
        return self


# ---------------------------------------------------------------------------
# Cross-side column comparison

class ColumnComparison(_Base):
    """Drift + schema-change verdict for a single column across two sides.

    For columns added on one side and missing on the other, drift metrics are
    None and `schema_change` carries the truth.
    """

    column_name: str
    schema_change: SchemaChange = "unchanged"
    psi: Optional[float] = Field(default=None, ge=0.0)
    ks_stat: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ks_pvalue: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    chi_square: Optional[float] = Field(default=None, ge=0.0)
    js_divergence: Optional[float] = Field(default=None, ge=0.0)
    verdict: Verdict
    stereotypes: list[Stereotype] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_verdict_consistent(self) -> "ColumnComparison":
        if self.schema_change != "unchanged":
            if self.verdict != "schema_change":
                raise ValueError(
                    f"column {self.column_name!r}: schema_change={self.schema_change!r} "
                    f"requires verdict='schema_change', got {self.verdict!r}"
                )
            return self

        if self.psi is None:
            return self  # nothing more to check

        expected = verdict_from_psi(self.psi)
        if self.verdict != expected:
            raise ValueError(
                f"column {self.column_name!r}: verdict={self.verdict!r} inconsistent "
                f"with PSI={self.psi:.4f} (expected {expected!r}; "
                f"thresholds: stable<{PSI_STABLE_MAX}, moderate<{PSI_MODERATE_MAX})"
            )
        return self


def verdict_from_psi(
    psi: Optional[float],
    schema_change: SchemaChange = "unchanged",
) -> Verdict:
    """Canonical PSI → verdict mapping. Use when constructing a ColumnComparison
    so thresholds aren't hard-coded at call sites.
    """
    if schema_change != "unchanged":
        return "schema_change"
    if psi is None:
        return "stable"
    if psi < PSI_STABLE_MAX:
        return "stable"
    if psi < PSI_MODERATE_MAX:
        return "moderate"
    return "significant"


# ---------------------------------------------------------------------------
# Lineage block — references to sibling artifacts in the same run folder

class Lineage(_Base):
    """Pointers to sibling artifacts in the run folder.

    Paths are filenames relative to the run folder (e.g. "test_profile.html"),
    so the metamodel.json can be relocated together with its siblings without
    breaking lineage.
    """

    html_profile_a: Optional[str] = None
    html_profile_b: Optional[str] = None
    html_comparison: Optional[str] = None
    excel_summary: Optional[str] = None
    json_schema: Optional[str] = None
    manifest: Optional[str] = None


# ---------------------------------------------------------------------------
# Root model

class ProfilerRun(_Base):
    metamodel_version: str = METAMODEL_VERSION
    run_id: UUID
    run_label: Optional[str] = None
    created_utc: datetime
    side_a: DatasetProfile
    side_b: DatasetProfile
    comparisons: list[ColumnComparison] = Field(default_factory=list)
    lineage: Lineage = Field(default_factory=Lineage)

    def to_json(self, indent: int = 2) -> str:
        """Canonical JSON serialization. exclude_none keeps the wire format
        compact; by_alias emits 'schema' (not 'schema_').
        """
        return self.model_dump_json(indent=indent, exclude_none=True, by_alias=True)


# ---------------------------------------------------------------------------
# Helpers exported for callers

def schema_for_current_version() -> dict[str, Any]:
    """Return the JSON Schema for ProfilerRun at the current METAMODEL_VERSION.

    Write this to the run folder once per major version as
    `dq-metamodel-v<MAJOR>.schema.json` (see Lineage.json_schema).
    """
    return ProfilerRun.model_json_schema(by_alias=True)


def new_run_id() -> UUID:
    """Generate a UUIDv7 (RFC 9562) — time-sortable + unique + retry-idempotent.

    Layout (128 bits):
        48 bits  unix_ts_ms
         4 bits  version (= 7)
        12 bits  rand_a
         2 bits  variant (= 0b10)
        62 bits  rand_b
    """
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF      # 48 bits
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits

    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0x2 << 62)
        | rand_b
    )
    return UUID(int=value)
