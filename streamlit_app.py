"""Streamlit UI — Synaptiq Data Quality Platform.

Two top-level modes:
    Compare   — profile two tables side-by-side, compute schema diff + drift metrics
    Profile   — profile one or more tables independently (no comparison)

Each mode has its own validate / run flow. Outputs (HTML profiles, Excel
workbook, Mermaid diagrams, metamodel JSON) are written to a UC Volume and
displayed inline after a successful run.
"""

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timezone
from typing import List, Optional

import streamlit as st
import streamlit.components.v1 as components

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
from profiler.compare import compare_tables, schema_change_counts
from profiler.excel import write_workbook
from profiler.manifest import ComparisonParams, SideSpec, new_manifest
from profiler.metamodel import DatasetProfile, Lineage, ProfilerRun, new_run_id
from profiler.profile import profile_table
from profiler.storage import (
    ensure_run_folder,
    list_runs,
    make_run_folder,
    read_text,
    write_json,
    write_json_schema,
    write_metamodel,
    write_mermaid_diagrams,
    write_text,
)


# ---------------------------------------------------------------------------
# Page config

st.set_page_config(
    page_title="Synaptiq Data Profiling Tool",
    page_icon="🔬",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Synaptiq brand styling
# Palette extracted from AIQ deck (April 2022):
#   Synaptiq Blue  #8BA4BD  — steel blue, dominant brand colour
#   Synaptiq Amber #C8956A  — warm copper-orange accent
#   Charcoal       #2D3748  — primary text
#   Off-white      #EEF3F8  — secondary backgrounds

st.markdown("""
<style>
/* ── Brand header bar ─────────────────────────────────────────── */
.synaptiq-header {
    background: linear-gradient(135deg, #8BA4BD 0%, #6B8EAD 100%);
    padding: 1.1rem 2rem 0.9rem 2rem;
    border-radius: 8px;
    margin-bottom: 1.2rem;
    display: flex;
    align-items: center;
    gap: 1rem;
}
.synaptiq-logo-mark {
    width: 38px; height: 38px;
    border: 2.5px solid rgba(200,149,106,0.9);
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; color: #C8956A;
    flex-shrink: 0;
}
.synaptiq-wordmark {
    color: #FFFFFF;
    font-size: 1.35rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1;
}
.synaptiq-tagline {
    color: rgba(255,255,255,0.72);
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-top: 2px;
}
.synaptiq-product {
    margin-left: auto;
    text-align: right;
}
.synaptiq-product-name {
    color: #FFFFFF;
    font-size: 0.85rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

/* ── Tab styling ──────────────────────────────────────────────── */
div[data-testid="stTabs"] button[role="tab"] {
    font-weight: 600;
    font-size: 0.92rem;
    letter-spacing: 0.03em;
    color: #6B8EAD;
}
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: #C8956A !important;
    border-bottom: 3px solid #C8956A;
}

/* ── Metric tiles ─────────────────────────────────────────────── */
div[data-testid="metric-container"] {
    background: #EEF3F8;
    border-left: 4px solid #8BA4BD;
    border-radius: 6px;
    padding: 0.6rem 0.8rem;
}
div[data-testid="metric-container"] label {
    color: #6B8EAD;
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
div[data-testid="metric-container"] div[data-testid="metric-value"] {
    color: #2D3748;
    font-weight: 700;
}

/* ── Sidebar ──────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #8BA4BD 0%, #7A96B0 100%);
}
section[data-testid="stSidebar"] * {
    color: #FFFFFF !important;
}
section[data-testid="stSidebar"] .streamlit-expanderHeader {
    color: rgba(255,255,255,0.85) !important;
}
/* Input fields inside sidebar need dark text so it's readable */
section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea {
    color: #2D3748 !important;
    background: #FFFFFF !important;
}
section[data-testid="stSidebar"] input::placeholder,
section[data-testid="stSidebar"] textarea::placeholder {
    color: #9AA5B4 !important;
}

/* ── Buttons ──────────────────────────────────────────────────── */
div[data-testid="stButton"] > button[kind="primary"] {
    background: #C8956A;
    border: none;
    color: white;
    font-weight: 600;
    border-radius: 6px;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    background: #B8845A;
    border: none;
}

/* ── Section subheaders ───────────────────────────────────────── */
h3 { color: #8BA4BD; }
h4 { color: #6B8EAD; }

/* ── Divider accent ───────────────────────────────────────────── */
hr { border-top: 1px solid #C8956A33; }

/* ── Success / info boxes ─────────────────────────────────────── */
div[data-testid="stAlert"][data-type="success"] {
    border-left: 4px solid #C8956A;
    background: #FDF5EE;
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Cached data loaders

@st.cache_data(ttl=60)
def _connections() -> List[Connection]:
    return load_connections()


@st.cache_data(ttl=300)
def _env_labels() -> List[str]:
    return load_env_labels()


# Cache catalog/schema/table lookups so the warehouse is only hit once per
# TTL window, not on every Streamlit re-run (which happens on every widget
# interaction). This prevents the UI from blocking while the warehouse wakes.
@st.cache_data(ttl=120)
def _cached_schemas(catalog: str) -> List[str]:
    conn = _connections()[0] if _connections() else None
    if conn is None:
        return []
    return list_schemas(conn, catalog)


@st.cache_data(ttl=120)
def _cached_tables(catalog: str, schema: str) -> List[str]:
    conn = _connections()[0] if _connections() else None
    if conn is None:
        return []
    return list_tables(conn, catalog, schema)


def _connection_by_name(name: str) -> Optional[Connection]:
    for c in _connections():
        if c.name == name:
            return c
    return None


# ---------------------------------------------------------------------------
# Reusable widget sections


def _table_picker(key: str, title: str = "", default_env_idx: int = 0) -> dict:
    """Cascading catalog → schema → table picker. Returns a side dict.

    Every SQL call is wrapped in try/except so a warehouse error in one
    picker never prevents the other side from rendering.
    """
    if title:
        st.markdown(f"**{title}**")

    conn_names = [c.name for c in _connections()]
    if len(conn_names) == 1:
        conn_name = conn_names[0]
        conn = _connection_by_name(conn_name)
    else:
        conn_name = st.selectbox("Connection", options=conn_names, key=f"{key}_conn")
        conn = _connection_by_name(conn_name)

    env_label = st.selectbox(
        "Env label",
        options=_env_labels(),
        index=default_env_idx,
        key=f"{key}_env",
        help="Label used in report titles and run folder names.",
    )

    # ── Catalog ──────────────────────────────────────────────────
    try:
        catalogs = list_catalogs(conn) if conn else []
    except Exception as exc:  # noqa: BLE001
        st.error(f"Cannot load catalogs: {exc}")
        catalogs = []

    catalog = st.selectbox(
        "Catalog", options=catalogs, key=f"{key}_catalog",
        index=0 if catalogs else None,
    )

    # ── Schema ───────────────────────────────────────────────────
    schemas: List[str] = []
    if catalog:
        try:
            schemas = _cached_schemas(catalog)
        except Exception as exc:  # noqa: BLE001
            st.warning(
                f"Cannot load schemas for `{catalog}` — {exc}\n\n"
                f"Run in Databricks SQL:  "
                f"`GRANT USE CATALOG ON CATALOG {catalog} TO <app-sp>;`  \n"
                f"`GRANT USE SCHEMA, SELECT ON ALL SCHEMAS IN CATALOG {catalog} TO <app-sp>;`"
            )

    if catalog and not schemas:
        st.caption(f"No schemas visible in `{catalog}` — check service principal grants.")

    schema = st.selectbox(
        "Schema", options=schemas, key=f"{key}_schema",
        index=0 if schemas else None,
        placeholder="— pick a schema —",
    )

    # ── Table ────────────────────────────────────────────────────
    tables: List[str] = []
    if catalog and schema:
        try:
            tables = _cached_tables(catalog, schema)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Cannot load tables for `{catalog}.{schema}` — {exc}")

    if schema and not tables:
        st.caption(f"No tables in `{catalog}.{schema}`.")

    table = st.selectbox(
        "Table", options=tables, key=f"{key}_table",
        index=0 if tables else None,
        placeholder="— pick a table —",
    )

    return {
        "env_label": env_label,
        "connection": conn,
        "catalog": catalog,
        "schema": schema,
        "table": table,
    }


def _sampling_section(key: str) -> tuple[str, int, str]:
    """Sampling controls. Returns (sampling_mode, sample_n, stratify_by)."""
    with st.expander("Sampling options", expanded=False):
        col1, col2, col3 = st.columns([1, 1, 1])
        with col1:
            sampling_mode = st.selectbox(
                "Mode",
                options=["Full table", "Sample N rows", "Stratified by column"],
                index=0, key=f"{key}_sampling_mode",
            )
        with col2:
            sample_n = st.number_input(
                "N rows", min_value=1_000, max_value=100_000_000, value=1_000_000, step=1_000,
                disabled=(sampling_mode != "Sample N rows"),
                key=f"{key}_sample_n",
            )
        with col3:
            stratify_by = st.text_input(
                "Stratify column", value="", placeholder="e.g. region",
                disabled=(sampling_mode != "Stratified by column"),
                key=f"{key}_stratify",
            )
    return sampling_mode, int(sample_n), stratify_by


def _output_section(key: str) -> tuple[Optional[VolumeRef], str, str]:
    """Output volume picker. Returns (VolumeRef | None, catalog, schema)."""
    st.markdown("**Output destination**")
    local_conns = [c for c in _connections() if c.type == "native"]
    local_conn = local_conns[0] if local_conns else None
    local_cats = list_catalogs(local_conn) if local_conn else []

    # Pre-select the first catalog/schema that has a volume.
    def _first_with_vol():
        if not local_conn:
            return None, None
        for cat in list_catalogs(local_conn):
            for sch in list_schemas(local_conn, cat):
                if list_volumes(cat, sch):
                    return cat, sch
        cats = list_catalogs(local_conn)
        if cats:
            schs = list_schemas(local_conn, cats[0])
            return cats[0], (schs[0] if schs else None)
        return None, None

    def _idx(opts, val):
        return opts.index(val) if (val is not None and val in opts) else 0

    def_cat, def_sch = _first_with_vol()

    c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
    with c1:
        out_cat = st.selectbox("Catalog", options=local_cats,
                               index=_idx(local_cats, def_cat) if local_cats else None,
                               key=f"{key}_out_cat")
    with c2:
        out_schs = list_schemas(local_conn, out_cat) if (local_conn and out_cat) else []
        out_sch = st.selectbox("Schema", options=out_schs,
                               index=_idx(out_schs, def_sch) if out_schs else None,
                               key=f"{key}_out_sch")
    with c3:
        out_vols = list_volumes(out_cat, out_sch) if (out_cat and out_sch) else []
        out_vol = st.selectbox("Volume", options=out_vols,
                               index=0 if out_vols else None,
                               key=f"{key}_out_vol")
    with c4:
        run_label = st.text_input("Run label (optional)", value="",
                                  key=f"{key}_run_label",
                                  help="Appended to run folder name.")

    if out_cat and out_sch and not out_vols:
        st.warning(
            f"No volumes in `{out_cat}.{out_sch}`. "
            f"Create one: `CREATE VOLUME {out_cat}.{out_sch}.ab_runs;`"
        )

    if out_cat and out_sch and out_vol:
        vol_ref = VolumeRef(catalog=out_cat, schema=out_sch, volume=out_vol)
        st.session_state["output_volume"] = vol_ref
    else:
        vol_ref = None

    return vol_ref, out_cat, out_sch, run_label


def _render_run_outputs(folder, profiler_run: ProfilerRun, mode: str) -> None:
    """Display run results inline: summary stats, Mermaid diagrams, HTML iframes."""
    st.divider()
    st.subheader("Run outputs")

    # Summary metrics
    a = profiler_run.side_a
    b = profiler_run.side_b if mode == "compare" else None

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Side A rows", f"{a.row_count:,}")
    col1.metric("Side A columns", a.column_count)
    if b:
        col2.metric("Side B rows", f"{b.row_count:,}")
        col2.metric("Side B columns", b.column_count)

    total_alerts_a = sum(len(c.alerts) for c in a.columns)
    col3.metric("Side A alerts", total_alerts_a)
    if b:
        total_alerts_b = sum(len(c.alerts) for c in b.columns)
        col3.metric("Side B alerts", total_alerts_b)

    if mode == "compare" and profiler_run.comparisons:
        changes = schema_change_counts(profiler_run.comparisons)
        n_drifted = sum(
            1 for c in profiler_run.comparisons
            if c.verdict in ("moderate", "significant")
        )
        n_schema_changed = sum(v for k, v in changes.items() if k != "unchanged")
        col4.metric("Schema changes", n_schema_changed)
        col4.metric("Drifted columns", n_drifted)

    # Mermaid diagrams
    st.markdown("#### Schema diagrams")
    mmd_tab_labels = ["Side A schema", "Side B schema", "Drift view"] if mode == "compare" else ["Schema"]
    mmd_files = (["schema_a.mmd", "schema_b.mmd", "drift.mmd"]
                 if mode == "compare" else ["schema_a.mmd"])

    import html as _html
    mmd_tabs = st.tabs(mmd_tab_labels)
    for tab, fname in zip(mmd_tabs, mmd_files):
        with tab:
            path = f"{folder.path}/{fname}"
            try:
                mmd_src = read_text(path)
                mmd_escaped = _html.escape(mmd_src)
                components.html(f"""<!DOCTYPE html>
<html><head>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.0/dist/mermaid.min.js"></script>
<style>
  body{{margin:0;background:white;overflow:hidden}}
  #ctrl{{position:sticky;top:0;z-index:100;background:rgba(255,255,255,.95);
         padding:4px 8px;display:flex;gap:4px;align-items:center;
         border-bottom:1px solid #e0e8f0}}
  #ctrl button{{background:#8BA4BD;color:white;border:none;border-radius:4px;
                padding:3px 10px;font-size:12px;cursor:pointer;font-weight:600}}
  #ctrl button:hover{{background:#6B8EAD}}
  #ctrl span{{font-size:12px;color:#666;min-width:38px;text-align:center}}
  #outer{{overflow:auto;width:100%;height:calc(100vh - 36px);padding:8px;box-sizing:border-box}}
  #inner{{transform-origin:top left;display:inline-block;min-width:100%}}
  #err{{color:#c0392b;padding:8px;font-size:12px}}
  pre.src{{font-size:10px;color:#888;white-space:pre-wrap;word-break:break-all}}
</style>
</head><body>
<div id="ctrl">
  <button onclick="z(0.25)">＋ Zoom in</button>
  <button onclick="z(-0.25)">－ Zoom out</button>
  <button onclick="fit()">⊡ Fit</button>
  <button onclick="reset()">1:1</button>
  <span id="pct">100%</span>
</div>
<div id="outer"><div id="inner">
  <pre class="mermaid">{mmd_escaped}</pre>
</div></div>
<script>
var sc=1;
function applyScale(){{
  var inner=document.getElementById('inner');
  inner.style.transform='scale('+sc+')';
  inner.style.width=(100/sc)+'%';
  document.getElementById('pct').textContent=Math.round(sc*100)+'%';
}}
function z(d){{sc=Math.max(0.1,Math.min(5,sc+d));applyScale();}}
function reset(){{sc=1;applyScale();}}
function fit(){{
  var outer=document.getElementById('outer');
  var svg=outer.querySelector('svg');
  if(svg){{sc=Math.min(1,(outer.clientWidth-20)/svg.getBoundingClientRect().width);applyScale();}}
}}
mermaid.initialize({{startOnLoad:false,theme:'default',securityLevel:'loose',
  er:{{useMaxWidth:false}},flowchart:{{useMaxWidth:false}}}});
document.addEventListener('DOMContentLoaded',async function(){{
  try{{
    await mermaid.run({{querySelector:'.mermaid'}});
    setTimeout(fit,200);
  }}catch(e){{
    document.getElementById('inner').innerHTML=
      '<div id="err">Render error: '+e.message+'</div>'+
      '<details><summary style="cursor:pointer;color:#666;font-size:11px">Show source</summary>'+
      '<pre class="src">'+{repr(mmd_escaped)}+'</pre></details>';
  }}
}});
</script>
</body></html>""",
                    height=580, scrolling=False,
                )
            except Exception as exc:
                st.warning(f"`{fname}` — {exc}")

    # HTML profile reports (iframe)
    st.markdown("#### Profile reports")
    html_files = (
        [("Side A", "profile_a.html"), ("Side B", "profile_b.html")]
        if mode == "compare" else [("Profile", "profile_a.html")]
    )
    html_tabs = st.tabs([label for label, _ in html_files])
    for tab, (label, fname) in zip(html_tabs, html_files):
        with tab:
            path = f"{folder.path}/{fname}"
            try:
                html_content = read_text(path)
                components.html(html_content, height=800, scrolling=True)
            except Exception:
                st.info(f"`{fname}` not yet available — profile may still be generating.")

    # Artifact paths
    with st.expander("Output file locations", expanded=False):
        st.code(folder.path, language="text")
        for art in ["manifest.json", "metamodel.json", "ab_summary.xlsx",
                    "profile_a.html", "profile_b.html",
                    "schema_a.mmd", "schema_b.mmd", "drift.mmd"]:
            st.caption(f"`{folder.path}/{art}`")


# ---------------------------------------------------------------------------
# Sidebar

# ---------------------------------------------------------------------------
# Suggestions helpers


def _exec_suggestion_sql(statement: str) -> any:
    """Run a SQL statement for suggestions via Statement Execution API."""
    import os as _os, time as _time
    from profiler.catalog import _workspace_client
    w = _workspace_client()
    wid = _os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    result = w.statement_execution.execute_statement(
        warehouse_id=wid, statement=statement, wait_timeout="50s"
    )
    deadline = _time.time() + 120
    while True:
        state = str(result.status.state).upper() if result.status else "UNKNOWN"
        if "SUCCEEDED" in state:
            return result
        if any(s in state for s in ("FAILED", "CANCELLED", "CLOSED")):
            err = (result.status.error.message
                   if (result.status and result.status.error) else state)
            raise RuntimeError(err)
        if _time.time() > deadline:
            raise TimeoutError("Timed out after 2 minutes")
        _time.sleep(2)
        result = w.statement_execution.get_statement(result.statement_id)


def _submit_suggestion(name: str, suggestion: str) -> str:
    """Insert a suggestion row. Returns '' on success, error string on failure."""
    import uuid
    from datetime import datetime, timezone
    from profiler.catalog import _runtime
    if _runtime() != "databricks":
        return "Suggestions are only stored in Databricks mode."
    sid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    name_e = (name or "Anonymous").replace("'", "''")
    sugg_e = suggestion.replace("'", "''")
    try:
        _exec_suggestion_sql("""
            CREATE TABLE IF NOT EXISTS `dev`.`test_main_profiler`.`user_suggestions` (
                suggestion_id STRING NOT NULL, submitted_by STRING,
                suggestion STRING NOT NULL, submitted_at TIMESTAMP NOT NULL
            ) USING DELTA TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
        """)
        _exec_suggestion_sql(
            f"INSERT INTO `dev`.`test_main_profiler`.`user_suggestions` "
            f"(suggestion_id, submitted_by, suggestion, submitted_at) "
            f"VALUES ('{sid}', '{name_e}', '{sugg_e}', TIMESTAMP '{now}')"
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _load_suggestions() -> list:
    """Return up to 20 most-recent suggestions."""
    from profiler.catalog import _runtime
    if _runtime() != "databricks":
        return []
    try:
        result = _exec_suggestion_sql("""
            SELECT submitted_by, suggestion, submitted_at
            FROM `dev`.`test_main_profiler`.`user_suggestions`
            ORDER BY submitted_at DESC LIMIT 20
        """)
        if result.result and result.result.data_array:
            return result.result.data_array
        return []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Sidebar


def _sidebar():
    st.sidebar.markdown("""
<div style='text-align:center; padding: 0.5rem 0 0.8rem 0;'>
  <div style='font-size:1.1rem; font-weight:700; letter-spacing:0.05em;
              color:#FFFFFF;'>Synaptiq</div>
  <div style='font-size:0.62rem; letter-spacing:0.14em; text-transform:uppercase;
              color:rgba(255,255,255,0.65); margin-top:2px;'>Data Profiling Tool</div>
</div>
""", unsafe_allow_html=True)
    st.sidebar.divider()

    with st.sidebar.expander("Recent runs", expanded=False):
        vol = st.session_state.get("output_volume")
        if vol:
            names = list_runs(vol)
            if not names:
                st.caption("_No runs yet._")
            for name in names:
                st.caption(name)
        else:
            st.caption("_Select an output volume to see run history._")

    st.sidebar.divider()

    # ── Suggestions ──────────────────────────────────────────────────────────
    with st.sidebar.expander("💡 Suggestions", expanded=False):
        st.markdown(
            "<div style='color:white;font-size:0.85rem;margin-bottom:10px;'>"
            "Have an idea or improvement? Let us know!</div>",
            unsafe_allow_html=True,
        )
        with st.form("suggestion_form", clear_on_submit=True):
            s_name = st.text_input("Your name", placeholder="e.g. Gary Fischer")
            s_text = st.text_area(
                "Suggestion",
                placeholder="Describe the feature or improvement you'd like to see…",
                height=120,
            )
            s_submitted = st.form_submit_button("Submit", type="primary")

        if s_submitted:
            if not s_text.strip():
                st.warning("Please enter a suggestion.")
            else:
                err = _submit_suggestion(s_name.strip(), s_text.strip())
                if err:
                    st.error(f"Could not save: {err}")
                else:
                    st.success("Thanks! Suggestion saved.")

        if st.button("Show suggestion history", key="load_suggestions_btn"):
            st.session_state["show_suggestions"] = True

        if st.session_state.get("show_suggestions"):
            history = _load_suggestions()
            if not history:
                st.caption("_No suggestions yet._")
            else:
                st.markdown(
                    "<div style='color:rgba(255,255,255,0.5);font-size:0.75rem;"
                    "text-transform:uppercase;letter-spacing:0.08em;"
                    "margin:6px 0 4px 0;'>Recent suggestions</div>",
                    unsafe_allow_html=True,
                )
                for row in history:
                    who = row[0] or "Anonymous"
                    text = row[1] or ""
                    when = str(row[2])[:16] if row[2] else ""
                    st.markdown(
                        f"<div style='background:rgba(255,255,255,0.1);"
                        f"border-radius:4px;padding:6px 8px;margin-bottom:6px;"
                        f"font-size:0.8rem;color:white;'>"
                        f"<b>{who}</b>&nbsp;"
                        f"<span style='opacity:0.6;font-size:0.72rem;'>{when}</span>"
                        f"<br>{text}</div>",
                        unsafe_allow_html=True,
                    )


_sidebar()

# ---------------------------------------------------------------------------
# Main layout

st.markdown("""
<div class="synaptiq-header">
  <div class="synaptiq-logo-mark">&#9678;</div>
  <div>
    <div class="synaptiq-wordmark">Synaptiq</div>
    <div class="synaptiq-tagline">The Humankind of AI</div>
  </div>
  <div class="synaptiq-product">
    <div class="synaptiq-product-name">Data Profiling Tool</div>
  </div>
</div>
""", unsafe_allow_html=True)

st.caption(
    "Profile Unity Catalog tables and surface data quality issues. "
    "Compare tables side-by-side to detect schema drift and statistical distribution shift."
)

# ---------------------------------------------------------------------------
# Compute warm-up banner

_runtime_mode = os.environ.get("PROFILER_RUNTIME", "mock").lower()
if _runtime_mode == "databricks":
    with st.container():
        col_warm, col_status = st.columns([1, 4])
        with col_warm:
            warm_clicked = st.button(
                "⚡ Initialize Compute",
                type="secondary",
                help="Start the SQL warehouse before running a profile.",
            )
        with col_status:
            if warm_clicked:
                _warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
                if not _warehouse_id:
                    st.error("DATABRICKS_WAREHOUSE_ID not set.")
                else:
                    with st.spinner(f"Starting warehouse `{_warehouse_id}` …"):
                        import time as _time
                        from datetime import timedelta as _td
                        _t0 = _time.time()
                        try:
                            from profiler.catalog import _workspace_client
                            _w = _workspace_client()
                            # Check current state (requires CAN USE or CAN MANAGE).
                            try:
                                _wh = _w.warehouses.get(id=_warehouse_id)
                                _state = str(_wh.state).upper() if _wh.state else "UNKNOWN"
                            except Exception:
                                _state = "UNKNOWN"

                            if "RUNNING" in _state:
                                st.success(f"✅ Warehouse already RUNNING — ready immediately.")
                                st.session_state["compute_warmed"] = True
                            else:
                                st.caption(f"Warehouse state: **{_state}**. Attempting to start …")
                                try:
                                    _w.warehouses.start(id=_warehouse_id)
                                    _w.warehouses.wait_get_warehouse_running(
                                        id=_warehouse_id,
                                        timeout=_td(minutes=10),
                                    )
                                    _elapsed = _time.time() - _t0
                                    st.success(f"✅ Warehouse RUNNING — took {_elapsed:.1f}s. Run your comparison now.")
                                    st.session_state["compute_warmed"] = True
                                except Exception as _start_exc:
                                    st.warning(
                                        f"Could not start warehouse automatically: {_start_exc}\n\n"
                                        f"Ask your admin to grant **Can manage** on warehouse "
                                        f"`{_warehouse_id}` to SP `{_warehouse_id}`.\n\n"
                                        f"Alternatively, manually start the warehouse in **SQL → "
                                        f"Warehouses** before running a profile."
                                    )
                        except Exception as _exc:  # noqa: BLE001
                            st.error(
                                f"Cannot reach warehouse `{_warehouse_id}`: {_exc}\n\n"
                                f"Your admin needs to run:\n"
                                f"```sql\n"
                                f"GRANT CAN USE ON SQL WAREHOUSE `{_warehouse_id}`\n"
                                f"  TO `39ee93a7-c623-4614-90a8-c3798bb5b329`;\n"
                                f"```"
                            )
            if not warm_clicked and "compute_warmed" not in st.session_state:
                st.caption(
                    "💡 Click **Initialize Compute** to start the SQL warehouse "
                    "before running a profile — avoids the cold-start wait."
                )

st.divider()
tab_compare, tab_profile = st.tabs(["⚖️  Compare two tables", "🔍  Profile table(s)"])


# ===========================================================================
# TAB 1 — COMPARE
# ===========================================================================

with tab_compare:
    st.markdown(
        "Profile two tables side-by-side and compute schema diff + "
        "drift metrics (PSI, KS, Chi-square, JS divergence)."
    )
    st.divider()

    # ---- Table pickers ----
    st.subheader("1. Pick the two tables")
    col_a, col_b = st.columns(2, gap="large")

    _cmp_labels = _env_labels()
    _default_a: dict = {}
    _default_b: dict = {}

    with col_a:
        try:
            idx_a = _cmp_labels.index("PROD") if "PROD" in _cmp_labels else 0
            _default_a = _table_picker("cmp_a", title="Side A — baseline", default_env_idx=idx_a)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Side A error: {exc}")
            st.code(traceback.format_exc(), language="python")

    with col_b:
        try:
            idx_b = _cmp_labels.index("TEST") if "TEST" in _cmp_labels else min(1, len(_cmp_labels) - 1)
            _default_b = _table_picker("cmp_b", title="Side B — candidate / TEST", default_env_idx=idx_b)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Side B error: {exc}")
            st.code(traceback.format_exc(), language="python")

    side_a = _default_a
    side_b = _default_b

    st.divider()

    # ---- Settings ----
    st.subheader("2. Settings")
    with st.expander("Comparison depth", expanded=False):
        depth = st.radio(
            "Depth",
            options=[
                "Aggregate + distributions + schema diff",
                "Include row-level diff (requires row key — milestone 6)",
            ],
            index=0, horizontal=False, key="cmp_depth",
        )
        with_row_level = depth.startswith("Include row-level")
        row_keys: List[str] = []
        max_mismatches = 100
        if with_row_level:
            ck1, ck2 = st.columns([2, 1])
            with ck1:
                keys_str = st.text_input(
                    "Row key column(s) — comma-separated",
                    placeholder="e.g. claim_id,claim_line_number",
                    key="cmp_row_keys",
                )
                row_keys = [k.strip() for k in keys_str.split(",") if k.strip()]
            with ck2:
                max_mismatches = st.number_input(
                    "Max sample mismatches", min_value=10, max_value=10_000,
                    value=100, step=10, key="cmp_max_mm",
                )

    cmp_sampling_mode, cmp_sample_n, cmp_stratify = _sampling_section("cmp")

    st.divider()

    # ---- Output ----
    st.subheader("3. Output destination")
    cmp_vol_ref, cmp_out_cat, cmp_out_sch, cmp_run_label = _output_section("cmp")

    st.divider()

    # ---- Validate + Run ----
    st.subheader("4. Run")
    cmp_col_v, cmp_col_r, _ = st.columns([1, 1, 3])
    cmp_validate = cmp_col_v.button("Validate", type="secondary", key="cmp_validate_btn")
    cmp_run = cmp_col_r.button(
        "Run compare",
        type="primary",
        disabled=not st.session_state.get("cmp_validated", False),
        key="cmp_run_btn",
    )
    cmp_status = st.container()

    # -- Validation --
    def _validate_compare() -> tuple[bool, List[str]]:
        msgs: List[str] = []
        ok = True
        for side, label in [(side_a, "A"), (side_b, "B")]:
            if not all([side["connection"], side["catalog"], side["schema"], side["table"]]):
                msgs.append(f"❌ Side {label}: catalog/schema/table not fully selected.")
                ok = False
        if cmp_vol_ref is None:
            msgs.append("❌ Output volume not selected.")
            ok = False
        if with_row_level and not row_keys:
            msgs.append("❌ Row-level diff selected but no key columns provided.")
            ok = False
        if not ok:
            return ok, msgs
        for side, label in [(side_a, "A"), (side_b, "B")]:
            ref = TableRef(connection=side["connection"].name,
                           catalog=side["catalog"], schema=side["schema"], table=side["table"])
            try:
                rc = describe_table(ref)
                msgs.append(
                    f"✅ Side {label}: `{ref.fqn}` reachable"
                    + (f" — {rc:,} rows" if rc is not None else "")
                )
                side["_row_count"] = rc
            except Exception as exc:  # noqa: BLE001
                ok = False
                msgs.append(f"❌ Side {label}: `{ref.fqn}` — {exc}")
        return ok, msgs

    if cmp_validate:
        with cmp_status:
            with st.spinner("Validating…"):
                ok, msgs = _validate_compare()
            for m in msgs:
                st.markdown(m)
            st.session_state["cmp_validated"] = ok
            if ok:
                st.success("Ready to run.")
            else:
                st.error("Fix issues above.")

    # -- Run --
    if cmp_run and st.session_state.get("cmp_validated", False):
        with cmp_status:
            t0 = time.time()
            try:
                folder = make_run_folder(
                    output=cmp_vol_ref,
                    side_a_env=side_a["env_label"],
                    side_b_env=side_b["env_label"],
                    table_name_a=side_a["table"],
                    table_name_b=side_b["table"],
                    run_label=cmp_run_label or None,
                )
                ensure_run_folder(folder)

                # Immediate write test — confirms volume is writable before
                # spending minutes on profiling.
                try:
                    write_text(folder, "_write_test.txt", "ok")
                    st.caption(f"✅ Volume write OK → `{folder.path}`")
                except Exception as exc:
                    st.error(f"❌ Cannot write to volume: {exc}")
                    raise

                _sample_n = cmp_sample_n if cmp_sampling_mode == "Sample N rows" else None

                t_profile = time.time()
                st.caption("⏳ Step 1/5: profiling Side A …")
                try:
                    dataset_a = profile_table(
                        ref=TableRef(connection=side_a["connection"].name,
                                     catalog=side_a["catalog"], schema=side_a["schema"],
                                     table=side_a["table"]),
                        env_label=side_a["env_label"], folder=folder,
                        html_filename="profile_a.html", sample_n=_sample_n,
                    )
                    st.caption(f"✅ Step 1/5: Side A — {dataset_a.row_count:,} rows, {dataset_a.column_count} cols")
                except Exception as exc:
                    st.error(f"❌ Step 1/5 failed — {exc}")
                    st.code(traceback.format_exc(), language="python")
                    raise

                st.caption("⏳ Step 2/5: profiling Side B …")
                try:
                    dataset_b = profile_table(
                        ref=TableRef(connection=side_b["connection"].name,
                                     catalog=side_b["catalog"], schema=side_b["schema"],
                                     table=side_b["table"]),
                        env_label=side_b["env_label"], folder=folder,
                        html_filename="profile_b.html", sample_n=_sample_n,
                    )
                    st.caption(f"✅ Step 2/5: Side B — {dataset_b.row_count:,} rows, {dataset_b.column_count} cols")
                except Exception as exc:
                    st.error(f"❌ Step 2/5 failed — {exc}")
                    st.code(traceback.format_exc(), language="python")
                    raise

                t_profiled = time.time() - t_profile

                with st.spinner("Computing schema diff and drift metrics …"):
                    comparisons = compare_tables(dataset_a, dataset_b)

                profiler_run = ProfilerRun(
                    run_id=new_run_id(), run_label=cmp_run_label or None,
                    created_utc=datetime.now(timezone.utc),
                    side_a=dataset_a, side_b=dataset_b, comparisons=comparisons,
                    lineage=Lineage(
                        manifest="manifest.json",
                        html_profile_a="profile_a.html",
                        html_profile_b="profile_b.html",
                        excel_summary="ab_summary.xlsx",
                    ),
                )

                with st.spinner("Writing Excel workbook, metamodel, and Mermaid diagrams …"):
                    write_workbook(folder, profiler_run)
                    write_metamodel(folder, profiler_run)
                    write_json_schema(folder)
                    write_mermaid_diagrams(folder, profiler_run)
                    st.caption(f"✅ Artifacts written to `{folder.path}`")

                manifest = new_manifest(
                    run_id=folder.run_id, run_label=cmp_run_label or None,
                    side_a=SideSpec(env_label=side_a["env_label"],
                                    connection=side_a["connection"].name,
                                    connection_type=side_a["connection"].type,
                                    catalog=side_a["catalog"], schema=side_a["schema"],
                                    table=side_a["table"],
                                    row_count=dataset_a.row_count),
                    side_b=SideSpec(env_label=side_b["env_label"],
                                    connection=side_b["connection"].name,
                                    connection_type=side_b["connection"].type,
                                    catalog=side_b["catalog"], schema=side_b["schema"],
                                    table=side_b["table"],
                                    row_count=dataset_b.row_count),
                    comparison=ComparisonParams(
                        depth="with_row_level" if with_row_level else "aggregate_only",
                        row_keys=row_keys,
                        max_sample_mismatches=int(max_mismatches),
                        sampling_mode={"Full table": "full",
                                       "Sample N rows": "sample_n",
                                       "Stratified by column": "stratified"}[cmp_sampling_mode],
                        sample_n=_sample_n,
                        stratify_by=cmp_stratify or None,
                    ),
                    output_folder=folder.path,
                )
                manifest.add_timing("setup", time.time() - t0 - t_profiled)
                manifest.add_timing("profiling", t_profiled)
                for art in ["profile_a.html", "profile_b.html", "ab_summary.xlsx",
                            "metamodel.json", "schema_a.mmd", "schema_b.mmd", "drift.mmd"]:
                    manifest.add_artifact(art)
                write_json(folder, "manifest.json", manifest.to_dict())

                if os.environ.get("PROFILER_RUNTIME", "mock").lower() == "databricks":
                    try:
                        from profiler import delta_repo
                        delta_repo.ensure_tables(cmp_out_cat, cmp_out_sch)
                        delta_repo.ingest(profiler_run, cmp_out_cat, cmp_out_sch)
                        st.caption(f"✅ Governance tables updated in `{cmp_out_cat}.{cmp_out_sch}`")
                    except RuntimeError as exc:
                        # Grant errors surface here — ingest may have succeeded
                        msg = str(exc)
                        if "GRANT" in msg.upper() or "grant" in msg:
                            st.warning(f"⚠️ Tables written but grants need admin help:\n{msg}")
                        else:
                            st.warning(f"Delta repo issue — {exc}")
                    except Exception as exc:  # noqa: BLE001
                        st.warning(f"Delta repo ingest skipped — {exc}")
                else:
                    st.caption(
                        f"⚠️ PROFILER_RUNTIME={os.environ.get('PROFILER_RUNTIME','NOT SET')} "
                        f"— Delta tables skipped (expected 'databricks')"
                    )

                st.success(f"Compare run complete in {time.time() - t0:.1f}s — `{folder.folder_name}`")
                _render_run_outputs(folder, profiler_run, mode="compare")

            except Exception:  # noqa: BLE001
                st.error("Run failed.")
                st.code(traceback.format_exc(), language="python")


# ===========================================================================
# TAB 2 — PROFILE
# ===========================================================================

with tab_profile:
    st.markdown(
        "Profile one or more tables independently. "
        "Produces HTML reports, column-level DQ stats, alerts, and metamodel JSON. "
        "No cross-table comparison."
    )
    st.divider()

    # ---- Dynamic table list ----
    st.subheader("1. Pick tables to profile")

    if "n_profile_tables" not in st.session_state:
        st.session_state["n_profile_tables"] = 1

    n_tables = st.session_state["n_profile_tables"]
    profile_sides: List[dict] = []

    labels = _env_labels()
    for i in range(n_tables):
        default_idx = min(i, len(labels) - 1)
        with st.expander(f"Table {i + 1}", expanded=True):
            profile_sides.append(_table_picker(f"prf_{i}", default_env_idx=default_idx))

    btn_col1, btn_col2, _ = st.columns([1, 1, 4])
    if btn_col1.button("+ Add table", key="prf_add"):
        st.session_state["n_profile_tables"] += 1
        st.rerun()
    if btn_col2.button("− Remove last", key="prf_rem",
                       disabled=st.session_state["n_profile_tables"] <= 1):
        st.session_state["n_profile_tables"] -= 1
        st.rerun()

    st.divider()

    # ---- Settings ----
    st.subheader("2. Settings")
    prf_sampling_mode, prf_sample_n, prf_stratify = _sampling_section("prf")

    st.divider()

    # ---- Output ----
    st.subheader("3. Output destination")
    prf_vol_ref, prf_out_cat, prf_out_sch, prf_run_label = _output_section("prf")

    st.divider()

    # ---- Validate + Run ----
    st.subheader("4. Run")
    prf_col_v, prf_col_r, _ = st.columns([1, 1, 3])
    prf_validate = prf_col_v.button("Validate", type="secondary", key="prf_validate_btn")
    prf_run = prf_col_r.button(
        "Run profile",
        type="primary",
        disabled=not st.session_state.get("prf_validated", False),
        key="prf_run_btn",
    )
    prf_status = st.container()

    # -- Validation --
    def _validate_profile() -> tuple[bool, List[str]]:
        msgs: List[str] = []
        ok = True
        for i, side in enumerate(profile_sides):
            if not all([side["connection"], side["catalog"], side["schema"], side["table"]]):
                msgs.append(f"❌ Table {i + 1}: not fully selected.")
                ok = False
        if prf_vol_ref is None:
            msgs.append("❌ Output volume not selected.")
            ok = False
        if not ok:
            return ok, msgs
        for i, side in enumerate(profile_sides):
            ref = TableRef(connection=side["connection"].name,
                           catalog=side["catalog"], schema=side["schema"], table=side["table"])
            try:
                rc = describe_table(ref)
                msgs.append(
                    f"✅ Table {i + 1}: `{ref.fqn}` reachable"
                    + (f" — {rc:,} rows" if rc is not None else "")
                )
                side["_row_count"] = rc
            except Exception as exc:  # noqa: BLE001
                ok = False
                msgs.append(f"❌ Table {i + 1}: `{ref.fqn}` — {exc}")
        return ok, msgs

    if prf_validate:
        with prf_status:
            with st.spinner("Validating…"):
                ok, msgs = _validate_profile()
            for m in msgs:
                st.markdown(m)
            st.session_state["prf_validated"] = ok
            if ok:
                st.success("Ready to run.")
            else:
                st.error("Fix issues above.")

    # -- Run --
    if prf_run and st.session_state.get("prf_validated", False):
        with prf_status:
            t0 = time.time()
            try:
                _sample_n = prf_sample_n if prf_sampling_mode == "Sample N rows" else None

                for i, side in enumerate(profile_sides):
                    tbl_label = f"{side['catalog']}.{side['schema']}.{side['table']}"
                    st.markdown(f"---\n**Table {i + 1} of {n_tables}: `{tbl_label}`**")

                    folder = make_run_folder(
                        output=prf_vol_ref,
                        side_a_env=side["env_label"],
                        side_b_env=side["env_label"],
                        table_name_a=side["table"],
                        table_name_b=side["table"],
                        run_label=prf_run_label or None,
                    )
                    ensure_run_folder(folder)

                    with st.spinner(f"Profiling `{tbl_label}` …"):
                        dataset = profile_table(
                            ref=TableRef(connection=side["connection"].name,
                                         catalog=side["catalog"], schema=side["schema"],
                                         table=side["table"]),
                            env_label=side["env_label"], folder=folder,
                            html_filename="profile_a.html", sample_n=_sample_n,
                        )

                    # Build a single-side ProfilerRun (side_b mirrors side_a,
                    # comparisons empty — no cross-table diff in profile mode).
                    profiler_run = ProfilerRun(
                        run_id=new_run_id(), run_label=prf_run_label or None,
                        created_utc=datetime.now(timezone.utc),
                        side_a=dataset, side_b=dataset,
                        comparisons=[],
                        lineage=Lineage(
                            manifest="manifest.json",
                            html_profile_a="profile_a.html",
                        ),
                    )

                    with st.spinner("Writing metamodel and schema diagram …"):
                        write_metamodel(folder, profiler_run)
                        write_json_schema(folder)
                        # Only the Side-A schema diagram is meaningful in profile mode
                        from profiler.mermaid import render_side_schema
                        schema_mmd = render_side_schema(dataset)
                        write_text(folder, "schema_a.mmd", schema_mmd)

                    if os.environ.get("PROFILER_RUNTIME", "mock").lower() == "databricks":
                        try:
                            from profiler import delta_repo
                            delta_repo.ensure_tables(prf_out_cat, prf_out_sch)
                            delta_repo.ingest(profiler_run, prf_out_cat, prf_out_sch)
                        except Exception as exc:  # noqa: BLE001
                            st.warning(f"Delta repo ingest skipped: {exc}")

                    st.success(
                        f"Table {i + 1} profiled — "
                        f"{dataset.row_count:,} rows, {dataset.column_count} columns, "
                        f"{sum(len(c.alerts) for c in dataset.columns)} alerts"
                    )
                    _render_run_outputs(folder, profiler_run, mode="profile")

                st.success(f"All {n_tables} table(s) profiled in {time.time() - t0:.1f}s.")

            except Exception:  # noqa: BLE001
                st.error("Run failed.")
                st.code(traceback.format_exc(), language="python")
