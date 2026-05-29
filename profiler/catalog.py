"""Unity Catalog lookups for the cascading Catalog/Schema/Table/Volume pickers.

Two runtime modes, chosen by the PROFILER_RUNTIME env var:

    databricks  — query information_schema via a SQL warehouse using the app's
                  service principal (via databricks-sql-connector). This is the
                  only mode that should run in production.

    mock        — return hard-coded fake catalogs/schemas/tables/volumes. Used
                  for local development outside a Databricks workspace, so the
                  Streamlit UI can be exercised without connectivity.

Only read-only metadata queries live here. Actual table reads happen through
Spark in profile.py (milestone 2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

import yaml


# ---------------------------------------------------------------------------
# Types

@dataclass(frozen=True)
class Connection:
    name: str
    type: str              # "native" | "delta_share"
    env_label: str         # DEV/TEST/QA/STAGE/PROD/...
    catalogs: List[str]    # empty list on native = auto-discover

    @property
    def is_shared(self) -> bool:
        return self.type == "delta_share"


@dataclass(frozen=True)
class TableRef:
    connection: str
    catalog: str
    schema: str
    table: str

    @property
    def fqn(self) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{self.table}`"


@dataclass(frozen=True)
class VolumeRef:
    catalog: str
    schema: str
    volume: str

    @property
    def path(self) -> str:
        return f"/Volumes/{self.catalog}/{self.schema}/{self.volume}"


# ---------------------------------------------------------------------------
# Config loading

def load_connections(path: str = "connections.yaml") -> List[Connection]:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return [
        Connection(
            name=c["name"],
            type=c["type"],
            env_label=c.get("env_label", ""),
            catalogs=list(c.get("catalogs") or []),
        )
        for c in cfg.get("connections", [])
    ]


def load_env_labels(path: str = "connections.yaml") -> List[str]:
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    return list(cfg.get("env_labels") or ["DEV", "TEST", "QA", "STAGE", "PROD"])


# ---------------------------------------------------------------------------
# Runtime dispatch

def _runtime() -> str:
    return os.environ.get("PROFILER_RUNTIME", "mock").lower()


def list_catalogs(conn: Connection) -> List[str]:
    if _runtime() == "databricks":
        return _dbx_list_catalogs(conn)
    return _mock_list_catalogs(conn)


def list_schemas(conn: Connection, catalog: str) -> List[str]:
    if _runtime() == "databricks":
        return _dbx_list_schemas(catalog)
    return _mock_list_schemas(catalog)


def list_tables(conn: Connection, catalog: str, schema: str) -> List[str]:
    if _runtime() == "databricks":
        return _dbx_list_tables(catalog, schema)
    return _mock_list_tables(catalog, schema)


def list_volumes(catalog: str, schema: str) -> List[str]:
    if _runtime() == "databricks":
        return _dbx_list_volumes(catalog, schema)
    return _mock_list_volumes(catalog, schema)


# ---------------------------------------------------------------------------
# Databricks Unity Catalog REST API backend (metadata lookups)
#
# Uses the Databricks SDK (WorkspaceClient) for catalog/schema/table/volume
# discovery. The SDK calls the Unity Catalog REST API directly — no SQL
# warehouse needed, no startup wait, no blocking.
#
# The SQL warehouse (_sql_connect / _sql_query) is kept ONLY for actual
# data queries during profile runs (profile.py). Never use it for UI dropdowns.

@lru_cache(maxsize=1)
def _workspace_client():
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient()


def _dbx_list_catalogs(conn: Connection) -> List[str]:
    if conn.catalogs:
        return sorted(conn.catalogs)
    w = _workspace_client()
    return sorted(c.name for c in w.catalogs.list() if c.name)


def _dbx_list_schemas(catalog: str) -> List[str]:
    w = _workspace_client()
    return sorted(
        s.name for s in w.schemas.list(catalog_name=catalog)
        if s.name and s.name != "information_schema"
    )


def _dbx_list_tables(catalog: str, schema: str) -> List[str]:
    w = _workspace_client()
    return sorted(
        t.name for t in w.tables.list(catalog_name=catalog, schema_name=schema)
        if t.name
    )


def _dbx_list_volumes(catalog: str, schema: str) -> List[str]:
    w = _workspace_client()
    return sorted(
        v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema)
        if v.name
    )


# SQL connector — used ONLY by profile.py for actual data reads.
# Never call this from the UI dropdown code paths.
def _sql_connect():
    from databricks import sql

    host = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    http_path = f"/sql/1.0/warehouses/{warehouse_id}"

    # Use PAT token if set, otherwise rely on app runtime auto-auth.
    token = os.environ.get("DATABRICKS_TOKEN") or os.environ.get("DATABRICKS_ACCESS_TOKEN")
    kwargs: dict = dict(server_hostname=host, http_path=http_path)
    if token:
        kwargs["access_token"] = token
    return sql.connect(**kwargs)


def _sql_query(q: str) -> List[tuple]:
    with _sql_connect() as cx, cx.cursor() as cur:
        cur.execute(q)
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Mock backend — enough variety to exercise the cascading UI.
#
# POC topology: single `dev` catalog. Environments are simulated by schema
# name prefix (test_main_*, prod_main_*, qa_main_*, stage_main_*, dev_*).
# _volumes keys are internal; they are not returned as table names.

_MOCK = {
    "dev": {
        # Profiler output volume
        "test_main_profiler":  {"_volumes": ["ab_runs"]},
        # Tuva input layer — claims domain
        "test_main_claims":    ["medical_claim", "pharmacy_claim", "provider_attribution", "location", "practitioner"],
        "prod_main_claims":    ["medical_claim", "pharmacy_claim", "provider_attribution", "location", "practitioner"],
        # Tuva input layer — clinical domain
        "test_main_clinical":  ["encounter", "condition", "procedure", "lab_result", "observation", "medication", "immunization", "appointment", "location", "practitioner"],
        "prod_main_clinical":  ["encounter", "condition", "procedure", "lab_result", "observation", "medication", "immunization", "appointment", "location", "practitioner"],
        # Tuva input layer — members domain
        "test_main_members":   ["eligibility", "patient", "location", "practitioner"],
        "prod_main_members":   ["eligibility", "patient", "location", "practitioner"],
        # Other domain schemas (non-Tuva)
        "test_main_sales":     ["orders", "order_items", "customers", "products"],
        "test_main_finance":   ["invoices", "payments", "ledger"],
        "prod_main_sales":     ["orders", "order_items", "customers", "products"],
        "prod_main_finance":   ["invoices", "payments", "ledger"],
        "qa_main_sales":       ["orders", "customers"],
        "stage_main_sales":    ["orders", "customers", "products"],
        "dev_sandbox":         ["experiment_a", "experiment_b", "scratch"],
    }
}


def _mock_list_catalogs(conn: Connection) -> List[str]:
    if conn.catalogs:
        return sorted(conn.catalogs)
    return sorted(_MOCK.keys())


def _mock_list_schemas(catalog: str) -> List[str]:
    return sorted(s for s in _MOCK.get(catalog, {}).keys())


def _mock_list_tables(catalog: str, schema: str) -> List[str]:
    node = _MOCK.get(catalog, {}).get(schema, [])
    if isinstance(node, list):
        return sorted(node)
    return sorted(t for t in node if not t.startswith("_"))


def _mock_list_volumes(catalog: str, schema: str) -> List[str]:
    node = _MOCK.get(catalog, {}).get(schema, {})
    if isinstance(node, dict):
        return sorted(node.get("_volumes", []))
    return []


# ---------------------------------------------------------------------------
# Helpers used by the UI

def describe_table(ref: TableRef) -> Optional[int]:
    """Verify a table is accessible and return its row count if available.

    Uses the Unity Catalog REST API (no warehouse needed) to confirm the
    table exists and the SP can read it. Row count is returned from UC
    table statistics when available, otherwise None.
    """
    if _runtime() == "mock":
        return None
    w = _workspace_client()
    info = w.tables.get(full_name=f"{ref.catalog}.{ref.schema}.{ref.table}")
    # UC may cache row count in table properties; use it if present.
    try:
        if info.properties and "numRows" in info.properties:
            return int(info.properties["numRows"])
    except Exception:
        pass
    return None
