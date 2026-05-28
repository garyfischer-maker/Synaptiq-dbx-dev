"""Streamlit UI — Synaptiq Data Quality Platform.

The UI is complete: symmetric Side A / Side B inputs, env label + connection
dropdowns, cascading catalog/schema/table pickers, comparison-depth radio
(aggregate-only vs. with row-level diff), sampling controls, output volume
picker, run label, runs-history sidebar.

Milestone 4.5.2: Run action also writes metamodel.json, JSON Schema,
three Mermaid diagrams, and persists rows to the Delta governance repo.
Profiling (column stats) is milestone 2+.
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone
from typing import List, Optional

import streamlit as st

from profiler.catalog import (
    Connection,
    TableRef,
    VolumeRef,
    describe_table,
    list_catalogs,
    list_schemas,
    list_tables,
    list_volumes,
    load_connections,
    load_env_labels,
)
from profiler.manifest import ComparisonParams, SideSpec, new_manifest
from profiler.metamodel import DatasetProfile, Lineage, ProfilerRun, new_run_id
from profiler.profile import profile_table
from profiler.storage import (
    ensure_run_folder,
    list_runs,
    make_run_folder,
    write_json,
    write_json_schema,
    write_metamodel,
    write_mermaid_diagrams,
)


# ---------------------------------------------------------------------------
# Page config + config loading

st.set_page_config(
    page_title="Synaptiq Data Quality Platform",
    page_icon="⚖️",
    layout="wide",
)


@st.cache_data(ttl=60)
def _connections() -> List[Connection]:
    return load_connections()


@st.cache_data(ttl=300)
def _env_labels() -> List[str]:
    return load_env_labels()


def _connection_by_name(name: str) -> Optional[Connection]:
    for c in _connections():
        if c.name == name:
            return c
    return None


# ---------------------------------------------------------------------------
# Side picker widget — returns a (connection, catalog, schema, table, env_label)

def _side_picker(side_key: str, default_env_idx: int) -> dict:
    st.markdown(f"### Side {side_key}")
    env_label = st.selectbox(
        "Env label",
        options=_env_labels(),
        index=default_env_idx,
        key=f"{side_key}_env",
        help="Metadata only — used in report titles and run folder names.",
    )
    conn_names = [c.name for c in _connections()]
    conn_name = st.selectbox(
        "Connection",
        options=conn_names,
        key=f"{side_key}_conn",
        help="Native = local workspace catalog. Shared = mounted via Delta Sharing.",
    )
    conn = _connection_by_name(conn_name)

    catalogs = list_catalogs(conn) if conn else []
    catalog = st.selectbox(
        "Catalog", options=catalogs, key=f"{side_key}_catalog",
        index=0 if catalogs else None,
    )

    schemas = list_schemas(conn, catalog) if (conn and catalog) else []
    schema = st.selectbox(
        "Schema", options=schemas, key=f"{side_key}_schema",
        index=0 if schemas else None,
    )

    tables = list_tables(conn, catalog, schema) if (conn and catalog and schema) else []
    table = st.selectbox(
        "Table", options=tables, key=f"{side_key}_table",
        index=0 if tables else None,
    )

    return {
        "env_label": env_label,
        "connection": conn,
        "catalog": catalog,
        "schema": schema,
        "table": table,
    }


# ---------------------------------------------------------------------------
# Sidebar — run history

def _sidebar():
    st.sidebar.title("Synaptiq DQ Platform")
    st.sidebar.caption("Milestone 4.5.2 — metamodel + Mermaid + Delta repo")
    st.sidebar.divider()

    with st.sidebar.expander("Recent runs", expanded=False):
        # We can't list runs until the user picks an output volume,
        # so this stays quiet until something is selected in the main pane.
        vol = st.session_state.get("output_volume")
        if vol:
            names = list_runs(vol)
            if not names:
                st.caption("_No runs yet._")
            for name in names:
                st.caption(name)
        else:
            st.caption("_Select an output volume to see run history._")


# ---------------------------------------------------------------------------
# Main layout

_sidebar()

st.title("⚖️ Synaptiq Data Quality Platform")
st.caption(
    "Profile and compare Unity Catalog tables. Outputs land in a Unity Catalog "
    "volume as metamodel JSON, Mermaid diagrams, HTML profiles, and Excel — "
    "with each run persisted to the Delta governance repository."
)

st.divider()
st.subheader("1. Pick the two sides")

col_a, col_b = st.columns(2, gap="large")
with col_a:
    # Default Side A to PROD if present in labels list
    labels = _env_labels()
    idx_a = labels.index("PROD") if "PROD" in labels else 0
    side_a = _side_picker("A", default_env_idx=idx_a)
with col_b:
    idx_b = labels.index("TEST") if "TEST" in labels else (1 if len(labels) > 1 else 0)
    side_b = _side_picker("B", default_env_idx=idx_b)


st.divider()
st.subheader("2. Comparison settings")

depth = st.radio(
    "Comparison depth",
    options=[
        "Aggregate + distributions + shape only",
        "Include row-level diff (requires row key)",
    ],
    index=0,
    horizontal=False,
    help=(
        "Aggregate-only: fast, schema + column metrics + distribution drift. "
        "Row-level: also samples mismatched rows using the keys you pick below. "
        "Requires the key to be unique on both sides."
    ),
)
with_row_level = depth.startswith("Include row-level")

row_keys: List[str] = []
max_mismatches = 100
if with_row_level:
    col_rk, col_rm = st.columns([2, 1])
    with col_rk:
        # Keys must exist in both schemas — in milestone 1 we free-text them.
        # Milestone 3 will fetch column lists and turn this into a multi-select.
        keys_str = st.text_input(
            "Row key column(s) — comma-separated",
            value="",
            placeholder="e.g. order_id  or  order_id,line_num",
            help="Must be present and unique on both sides.",
        )
        row_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
    with col_rm:
        max_mismatches = st.number_input(
            "Max sample mismatches",
            min_value=10, max_value=10_000, value=100, step=10,
        )

st.markdown("**Sampling**")
col_s1, col_s2, col_s3 = st.columns([1, 1, 1])
with col_s1:
    sampling_mode = st.selectbox(
        "Mode",
        options=["Full table", "Sample N rows", "Stratified by column"],
        index=0,
    )
with col_s2:
    sample_n = st.number_input(
        "N rows", min_value=1_000, max_value=100_000_000, value=1_000_000, step=1_000,
        disabled=(sampling_mode != "Sample N rows"),
    )
with col_s3:
    stratify_by = st.text_input(
        "Stratify column",
        value="",
        placeholder="e.g. region",
        disabled=(sampling_mode != "Stratified by column"),
    )


st.divider()
st.subheader("3. Output destination")

col_ov1, col_ov2, col_ov3 = st.columns(3)
# For the output volume, we list local catalogs only (shared catalogs can't be written to).
local_conns = [c for c in _connections() if c.type == "native"]
local_conn = local_conns[0] if local_conns else None
local_catalogs = list_catalogs(local_conn) if local_conn else []


def _first_path_with_volume(conn) -> tuple[Optional[str], Optional[str]]:
    """Return the first (catalog, schema) under `conn` that has at least one
    volume. Falls back to the first catalog/schema if none have volumes.
    Used to set sensible defaults so the volume dropdown isn't empty on first
    render."""
    if not conn:
        return None, None
    cats = list_catalogs(conn)
    for cat in cats:
        for sch in list_schemas(conn, cat):
            if list_volumes(cat, sch):
                return cat, sch
    if not cats:
        return None, None
    schs = list_schemas(conn, cats[0])
    return cats[0], (schs[0] if schs else None)


def _idx(options, value) -> int:
    return options.index(value) if (value is not None and value in options) else 0


_def_cat, _def_sch = _first_path_with_volume(local_conn)

with col_ov1:
    out_catalog = st.selectbox(
        "Output catalog", options=local_catalogs,
        index=_idx(local_catalogs, _def_cat) if local_catalogs else None,
        key="out_cat",
    )
with col_ov2:
    out_schemas = list_schemas(local_conn, out_catalog) if (local_conn and out_catalog) else []
    out_schema = st.selectbox(
        "Output schema", options=out_schemas,
        index=_idx(out_schemas, _def_sch) if out_schemas else None,
        key="out_sch",
    )
with col_ov3:
    out_vols = list_volumes(out_catalog, out_schema) if (out_catalog and out_schema) else []
    out_volume = st.selectbox(
        "Output volume", options=out_vols,
        index=0 if out_vols else None,
        key="out_vol",
    )

# Inline UX hint when the volume dropdown is empty — tells the user *why*
# they can't pick a volume and what to do about it.
if out_catalog and out_schema and not out_vols:
    st.warning(
        f"No volumes found in `{out_catalog}.{out_schema}`. "
        "Pick a different schema, or create one in Databricks with: "
        f"`CREATE VOLUME {out_catalog}.{out_schema}.<volume_name>;`"
    )

run_label = st.text_input(
    "Run label (optional)", value="",
    help="Appended to the run folder name; helpful for marking e.g. 'pre-migration'.",
)

if out_catalog and out_schema and out_volume:
    vol_ref = VolumeRef(catalog=out_catalog, schema=out_schema, volume=out_volume)
    st.session_state["output_volume"] = vol_ref
else:
    vol_ref = None


st.divider()
st.subheader("4. Run")

col_btn_v, col_btn_r, _ = st.columns([1, 1, 3])
validate = col_btn_v.button("Validate inputs", type="secondary")
run_now = col_btn_r.button(
    "Run profile & compare",
    type="primary",
    disabled=not st.session_state.get("validated", False),
    help="Enabled after successful validation.",
)

status = st.container()


# ---------------------------------------------------------------------------
# Validation

def _validate() -> tuple[bool, List[str]]:
    msgs: List[str] = []
    ok = True

    for side, label in [(side_a, "A"), (side_b, "B")]:
        if not all([side["connection"], side["catalog"], side["schema"], side["table"]]):
            msgs.append(f"❌ Side {label}: catalog/schema/table not fully selected.")
            ok = False

    if vol_ref is None:
        # Be specific about *why* the volume isn't selected so the user
        # can act on it without re-reading the form.
        if not out_catalog:
            msgs.append("❌ No output catalog available — service principal may lack USE CATALOG on any local catalog.")
        elif not out_schema:
            msgs.append(f"❌ No schemas in `{out_catalog}` — pick another catalog or grant USE SCHEMA.")
        elif not out_vols:
            msgs.append(
                f"❌ No volumes in `{out_catalog}.{out_schema}`. "
                f"Pick another schema, or create one: `CREATE VOLUME {out_catalog}.{out_schema}.ab_runs;`"
            )
        else:
            msgs.append("❌ Output volume not selected.")
        ok = False

    if with_row_level and not row_keys:
        msgs.append("❌ Row-level diff selected but no key columns provided.")
        ok = False

    if not ok:
        return ok, msgs

    # Reachability + row counts.
    for side, label in [(side_a, "A"), (side_b, "B")]:
        ref = TableRef(
            connection=side["connection"].name,
            catalog=side["catalog"], schema=side["schema"], table=side["table"],
        )
        try:
            rc = describe_table(ref)
            msgs.append(
                f"✅ Side {label}: `{ref.fqn}` reachable"
                + (f" — {rc:,} rows" if rc is not None else " (row count skipped in mock)")
            )
            side["_row_count"] = rc
        except Exception as exc:  # noqa: BLE001
            ok = False
            msgs.append(f"❌ Side {label}: cannot read `{ref.fqn}` — {exc}")

    return ok, msgs


if validate:
    with status:
        with st.spinner("Validating…"):
            ok, msgs = _validate()
        for m in msgs:
            st.markdown(m)
        st.session_state["validated"] = ok
        if ok:
            st.success("All checks passed. You can Run now.")
        else:
            st.error("Fix the issues above, then re-validate.")


# ---------------------------------------------------------------------------
# Run

if run_now and st.session_state.get("validated", False):
    with status:
        t0 = time.time()
        try:
            folder = make_run_folder(
                output=vol_ref,
                side_a_env=side_a["env_label"],
                side_b_env=side_b["env_label"],
                table_name_a=side_a["table"],
                table_name_b=side_b["table"],
                run_label=run_label or None,
            )
            ensure_run_folder(folder)

            _sample_n = int(sample_n) if sampling_mode == "Sample N rows" else None

            # Phase 1: profile both sides
            t_profile = time.time()
            with st.spinner(f"Profiling Side A — {side_a['catalog']}.{side_a['schema']}.{side_a['table']} …"):
                dataset_a = profile_table(
                    ref=TableRef(
                        connection=side_a["connection"].name,
                        catalog=side_a["catalog"],
                        schema=side_a["schema"],
                        table=side_a["table"],
                    ),
                    env_label=side_a["env_label"],
                    folder=folder,
                    html_filename="profile_a.html",
                    sample_n=_sample_n,
                )

            with st.spinner(f"Profiling Side B — {side_b['catalog']}.{side_b['schema']}.{side_b['table']} …"):
                dataset_b = profile_table(
                    ref=TableRef(
                        connection=side_b["connection"].name,
                        catalog=side_b["catalog"],
                        schema=side_b["schema"],
                        table=side_b["table"],
                    ),
                    env_label=side_b["env_label"],
                    folder=folder,
                    html_filename="profile_b.html",
                    sample_n=_sample_n,
                )

            t_profiled = time.time() - t_profile

            # Phase 2: manifest
            manifest = new_manifest(
                run_id=folder.run_id,
                run_label=run_label or None,
                side_a=SideSpec(
                    env_label=side_a["env_label"],
                    connection=side_a["connection"].name,
                    connection_type=side_a["connection"].type,
                    catalog=side_a["catalog"], schema=side_a["schema"], table=side_a["table"],
                    row_count=dataset_a.row_count or side_a.get("_row_count"),
                ),
                side_b=SideSpec(
                    env_label=side_b["env_label"],
                    connection=side_b["connection"].name,
                    connection_type=side_b["connection"].type,
                    catalog=side_b["catalog"], schema=side_b["schema"], table=side_b["table"],
                    row_count=dataset_b.row_count or side_b.get("_row_count"),
                ),
                comparison=ComparisonParams(
                    depth="with_row_level" if with_row_level else "aggregate_only",
                    row_keys=row_keys,
                    max_sample_mismatches=int(max_mismatches),
                    sampling_mode={
                        "Full table": "full",
                        "Sample N rows": "sample_n",
                        "Stratified by column": "stratified",
                    }[sampling_mode],
                    sample_n=_sample_n,
                    stratify_by=stratify_by or None if sampling_mode == "Stratified by column" else None,
                ),
                output_folder=folder.path,
            )
            manifest.add_timing("setup", time.time() - t0 - t_profiled)
            manifest.add_timing("profiling", t_profiled)
            manifest.add_artifact("profile_a.html")
            manifest.add_artifact("profile_b.html")

            manifest_path = write_json(folder, "manifest.json", manifest.to_dict())

            # Phase 3: metamodel, Mermaid, Delta repo
            profiler_run = ProfilerRun(
                run_id=new_run_id(),
                run_label=run_label or None,
                created_utc=datetime.now(timezone.utc),
                side_a=dataset_a,
                side_b=dataset_b,
                lineage=Lineage(
                    manifest="manifest.json",
                    html_profile_a="profile_a.html",
                    html_profile_b="profile_b.html",
                ),
            )

            write_metamodel(folder, profiler_run)
            write_json_schema(folder)
            write_mermaid_diagrams(folder, profiler_run)

            # Delta governance repo — requires SparkSession (Databricks only).
            if os.environ.get("PROFILER_RUNTIME", "mock").lower() == "databricks":
                try:
                    from profiler import delta_repo
                    delta_repo.ensure_tables(out_catalog, out_schema)
                    delta_repo.ingest(profiler_run, out_catalog, out_schema)
                    st.info("Run persisted to Delta governance tables.")
                except Exception as exc:  # noqa: BLE001
                    st.warning(f"Delta repo ingest skipped: {exc}")

            st.success(f"Run complete: `{folder.folder_name}`")
            st.markdown(
                f"- Side A: **{dataset_a.row_count:,}** rows, "
                f"**{dataset_a.column_count}** columns, "
                f"**{sum(len(c.alerts) for c in dataset_a.columns)}** alerts  \n"
                f"- Side B: **{dataset_b.row_count:,}** rows, "
                f"**{dataset_b.column_count}** columns, "
                f"**{sum(len(c.alerts) for c in dataset_b.columns)}** alerts  \n"
                f"- Profiling time: **{t_profiled:.1f}s**"
            )
            st.code(manifest_path, language="text")

        except Exception:  # noqa: BLE001
            st.error("Run failed — see traceback below.")
            st.code(traceback.format_exc(), language="python")
