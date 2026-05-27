"""Output-volume I/O: run folder naming, manifest writes, file listing.

The output volume is wired as a Databricks App resource in app.yaml; it's
available at /Volumes/<catalog>/<schema>/<volume> inside the app container.

Run folder convention:
    <volume>/runs/<YYYY-MM-DD_HHMM>__<envA>-vs-<envB>__<table>[__<label>]/
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .catalog import TableRef, VolumeRef


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(s: str) -> str:
    return _SAFE.sub("-", s.strip()).strip("-").lower() or "x"


@dataclass(frozen=True)
class RunFolder:
    volume: VolumeRef
    run_id: str       # YYYY-MM-DD_HHMM
    folder_name: str  # full run folder basename
    path: str         # absolute /Volumes path


def make_run_folder(
    output: VolumeRef,
    side_a_env: str,
    side_b_env: str,
    table_name_a: str,
    table_name_b: str,
    run_label: Optional[str] = None,
    now: Optional[datetime] = None,
) -> RunFolder:
    now = now or datetime.now(timezone.utc)
    run_id = now.strftime("%Y-%m-%d_%H%M")
    tbl_slug = (
        _slug(table_name_a) if table_name_a == table_name_b
        else f"{_slug(table_name_a)}-vs-{_slug(table_name_b)}"
    )
    parts = [run_id, f"{_slug(side_a_env)}-vs-{_slug(side_b_env)}", tbl_slug]
    if run_label:
        parts.append(_slug(run_label))
    folder_name = "__".join(parts)
    path = f"{output.path}/runs/{folder_name}"
    return RunFolder(volume=output, run_id=run_id, folder_name=folder_name, path=path)


def ensure_run_folder(folder: RunFolder) -> None:
    """Create the run folder. In mock mode, creates under ./_mock_runs."""
    target = _mock_rewrite(folder.path)
    Path(target).mkdir(parents=True, exist_ok=True)


def write_text(folder: RunFolder, filename: str, content: str) -> str:
    path = _mock_rewrite(f"{folder.path}/{filename}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")
    return path


def write_json(folder: RunFolder, filename: str, obj: dict) -> str:
    return write_text(folder, filename, json.dumps(obj, indent=2, default=str))


def list_runs(output: VolumeRef, limit: int = 20) -> list[str]:
    """Return most-recent run folder names (basenames only)."""
    root = _mock_rewrite(f"{output.path}/runs")
    p = Path(root)
    if not p.exists():
        return []
    entries = sorted(
        (e for e in p.iterdir() if e.is_dir()),
        key=lambda e: e.name,
        reverse=True,
    )
    return [e.name for e in entries[:limit]]


def _mock_rewrite(path: str) -> str:
    """When running outside Databricks, redirect /Volumes writes to ./_mock_runs.

    This lets the skeleton be developed locally without a real mount.
    """
    if os.environ.get("PROFILER_RUNTIME", "mock").lower() == "databricks":
        return path
    return path.replace("/Volumes/", "./_mock_runs/")
