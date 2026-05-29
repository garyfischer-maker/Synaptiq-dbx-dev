"""Output-volume I/O: run folder naming, manifest writes, file listing.

Storage backend selection:
    databricks — Databricks Files API (SDK).  Writes directly to UC Volumes
                 without requiring a FUSE mount.  Handles create, upload, list,
                 and download operations.
    mock       — Local filesystem under ./_mock_runs/ for offline development.

Run folder convention:
    /Volumes/<catalog>/<schema>/<volume>/runs/<YYYY-MM-DD_HHMM>__<envA>-vs-<envB>__<table>[__<label>]/
"""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .catalog import TableRef, VolumeRef

if TYPE_CHECKING:
    from .metamodel import ProfilerRun


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(s: str) -> str:
    return _SAFE.sub("-", s.strip()).strip("-").lower() or "x"


def _runtime() -> str:
    return os.environ.get("PROFILER_RUNTIME", "mock").lower()


@lru_cache(maxsize=1)
def _wc():
    """Cached WorkspaceClient — shared across all storage calls."""
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


# ---------------------------------------------------------------------------
# RunFolder


@dataclass(frozen=True)
class RunFolder:
    volume: VolumeRef
    run_id: str       # YYYY-MM-DD_HHMM
    folder_name: str  # full run folder basename
    path: str         # /Volumes/... path (or _mock_runs/... in mock mode)


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


# ---------------------------------------------------------------------------
# Write operations


def ensure_run_folder(folder: RunFolder) -> None:
    """Create the run folder.

    Databricks mode: creates a directory via the Files API (no FUSE needed).
    Mock mode: creates under ./_mock_runs/ on the local filesystem.
    """
    if _runtime() == "databricks":
        try:
            _wc().files.create_directory(folder.path)
        except Exception:
            pass  # already exists or will be created on first upload
        return
    Path(_local(folder.path)).mkdir(parents=True, exist_ok=True)


def write_text(folder: RunFolder, filename: str, content: str) -> str:
    """Write a UTF-8 text file. Returns the canonical /Volumes/... path."""
    path = f"{folder.path}/{filename}"
    if _runtime() == "databricks":
        _wc().files.upload(
            file_path=path,
            contents=io.BytesIO(content.encode("utf-8")),
            overwrite=True,
        )
        return path
    local = _local(path)
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    Path(local).write_text(content, encoding="utf-8")
    return local


def write_bytes(folder: RunFolder, filename: str, data: bytes) -> str:
    """Write raw bytes. Returns the canonical /Volumes/... path."""
    path = f"{folder.path}/{filename}"
    if _runtime() == "databricks":
        _wc().files.upload(
            file_path=path,
            contents=io.BytesIO(data),
            overwrite=True,
        )
        return path
    local = _local(path)
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    Path(local).write_bytes(data)
    return local


def write_json(folder: RunFolder, filename: str, obj: dict) -> str:
    return write_text(folder, filename, json.dumps(obj, indent=2, default=str))


def read_text(path: str) -> str:
    """Read a text file from a /Volumes/... path or local equivalent."""
    if _runtime() == "databricks":
        response = _wc().files.download(file_path=path)
        return response.contents.read().decode("utf-8")
    return Path(_local(path)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Run listing


def list_runs(output: VolumeRef, limit: int = 20) -> list[str]:
    """Return the most-recent run folder names (basenames only)."""
    runs_path = f"{output.path}/runs"
    if _runtime() == "databricks":
        try:
            entries = list(_wc().files.list_directory_contents(runs_path))
            dirs = sorted(
                [e.name for e in entries if e.is_directory],
                reverse=True,
            )
            return dirs[:limit]
        except Exception:
            return []
    local = Path(_local(runs_path))
    if not local.exists():
        return []
    dirs = sorted(
        (e for e in local.iterdir() if e.is_dir()),
        key=lambda e: e.name,
        reverse=True,
    )
    return [e.name for e in dirs[:limit]]


# ---------------------------------------------------------------------------
# Higher-level writers


def write_metamodel(folder: RunFolder, run: "ProfilerRun") -> str:
    return write_text(folder, "metamodel.json", run.to_json())


def write_json_schema(folder: RunFolder) -> str:
    from .metamodel import METAMODEL_VERSION, schema_for_current_version
    major = METAMODEL_VERSION.split(".")[0]
    filename = f"dq-metamodel-v{major}.schema.json"
    return write_json(folder, filename, schema_for_current_version())


def write_mermaid_diagrams(folder: RunFolder, run: "ProfilerRun") -> tuple[str, str, str]:
    from .mermaid import render_all
    a_mmd, b_mmd, drift_mmd = render_all(run)
    return (
        write_text(folder, "schema_a.mmd", a_mmd),
        write_text(folder, "schema_b.mmd", b_mmd),
        write_text(folder, "drift.mmd", drift_mmd),
    )


# ---------------------------------------------------------------------------
# Internal helpers


def _local(path: str) -> str:
    """Rewrite /Volumes/... to ./_mock_runs/... for local development."""
    return path.replace("/Volumes/", "./_mock_runs/")


# Keep _mock_rewrite as an alias so existing callers outside this module work.
def _mock_rewrite(path: str) -> str:
    if _runtime() == "databricks":
        return path
    return _local(path)
