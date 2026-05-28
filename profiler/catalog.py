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
# Databricks SQL backend
#
# Uses DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID + the app's auto-injected
# credentials. databricks-sql-connector picks up the service-principal token
# from the app runtime; no explicit auth needed.

def _sql_connect():
    from databricks import sql  # imported lazily so `mock` runs without the lib

    host = os.environ["DATABRICKS_HOST"].replace("https://", "").rstrip("/")
    warehouse_id = os.environ["DATABRICKS_WAREHOUSE_ID"]
    http_path = f"/sql/1.0/warehouses/{warehouse_id}"
    # In the Databricks App runtime, access_token is provided via env too;
    # the connector auto-discovers it when run inside an app.
    return sql.connect(server_hostname=host, http_path=http_path)


def _sql_query(q: str) -> List[tuple]:
    with _sql_connect() as cx, cx.cursor() as cur:
        cur.execute(q)
        return cur.fetchall()


@lru_cache(maxsize=1)
def _dbx_list_catalogs_all() -> List[str]:
    rows = _sql_query(
        "SELECT catalog_name FROM system.information_schema.catalogs "
        "ORDER BY catalog_name"
    )
    return [r[0] for r in rows]


def _dbx_list_catalogs(conn: Connection) -> List[str]:
    if conn.catalogs:
        return sorted(conn.catalogs)
    return _dbx_list_catalogs_all()


def _dbx_list_schemas(catalog: str) -> List[str]:
    rows = _sql_query(
        f"SELECT schema_name FROM `{catalog}`.information_schema.schemata "
        "WHERE schema_name <> 'information_schema' ORDER BY schema_name"
    )
    return [r[0] for r in rows]


def _dbx_list_tables(catalog: str, schema: str) -> List[str]:
    rows = _sql_query(
        f"SELECT table_name FROM `{catalog}`.information_schema.tables "
        f"WHERE table_schema = '{schema}' ORDER BY table_name"
    )
    return [r[0] for r in rows]


def _dbx_list_volumes(catalog: str, schema: str) -> List[str]:
    rows = _sql_query(
        f"SELECT volume_name FROM `{catalog}`.information_schema.volumes "
        f"WHERE volume_schema = '{schema}' ORDER BY volume_name"
    )
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Mock backend — enough variety to exercise the cascading UI.
#
# POC topology: single `dev` catalog. Environments are simulated by schema
# name prefix (test_main_*, prod_main_*, qa_main_*, stage_main_*, dev_*).
# _volumes keys are internal; they are not returned as table names.

_MOCK = {
    "dev": {
        "test_main_profiler":  {"_volumes": ["ab_runs"]},
        "test_main_sales":     ["orders", "order_items", "customers", "products"],
        "test_main_finance":   ["invoices", "payments", "ledger"],
        "test_main_inventory": ["items", "warehouses", "stock_levels"],
        "prod_main_sales":     ["orders", "order_items", "customers", "products"],
        "prod_main_finance":   ["invoices", "payments", "ledger"],
        "prod_main_inventory": ["items", "warehouses", "stock_levels"],
        "qa_main_sales":       ["orders", "customers"],
        "qa_main_finance":     ["invoices", "payments"],
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
    """Return row count (best-effort, exact). Used by the Validate step.

    Returns None in mock mode.
    """
    if _runtime() == "mock":
        return None
    rows = _sql_query(f"SELECT COUNT(*) FROM {ref.fqn}")
    return int(rows[0][0]) if rows else None
