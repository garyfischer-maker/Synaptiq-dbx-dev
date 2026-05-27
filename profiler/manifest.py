"""Run manifest assembly.

A manifest captures everything needed to understand (and one day reproduce) a
run: inputs, parameters, table metadata, timings, library versions, and the
list of output artifacts. Written as manifest.json at the root of the run
folder.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import __version__


@dataclass
class SideSpec:
    env_label: str
    connection: str
    connection_type: str       # "native" | "delta_share"
    catalog: str
    schema: str
    table: str
    row_count: Optional[int] = None


@dataclass
class ComparisonParams:
    depth: str                         # "aggregate_only" | "with_row_level"
    row_keys: List[str] = field(default_factory=list)
    max_sample_mismatches: int = 100
    sampling_mode: str = "full"        # "full" | "sample_n" | "stratified"
    sample_n: Optional[int] = None
    stratify_by: Optional[str] = None


@dataclass
class Manifest:
    run_id: str
    run_label: Optional[str]
    created_utc: str
    side_a: SideSpec
    side_b: SideSpec
    comparison: ComparisonParams
    output_folder: str
    artifacts: List[str] = field(default_factory=list)
    timings_seconds: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    versions: Dict[str, str] = field(default_factory=dict)

    def add_artifact(self, filename: str) -> None:
        if filename not in self.artifacts:
            self.artifacts.append(filename)

    def add_timing(self, stage: str, seconds: float) -> None:
        self.timings_seconds[stage] = round(seconds, 3)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def new_manifest(
    run_id: str,
    run_label: Optional[str],
    side_a: SideSpec,
    side_b: SideSpec,
    comparison: ComparisonParams,
    output_folder: str,
) -> Manifest:
    return Manifest(
        run_id=run_id,
        run_label=run_label,
        created_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        side_a=side_a,
        side_b=side_b,
        comparison=comparison,
        output_folder=output_folder,
        versions={
            "app": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    )
