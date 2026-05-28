# Synaptiq Databricks Table A/B Profiler

A Databricks App for profiling two Unity Catalog tables and producing a
side-by-side comparison (A/B). Produces deep ydata-profiling HTML reports per
side, a comparison HTML, an Excel summary, drift metrics (PSI, KS,
Chi-square, JS divergence), and a UML-inspired Data Quality Metamodel as
machine-readable JSON plus a Delta governance repository.

This file is the project context for any AI coding assistant (Claude Code
in particular) and any new human contributor. Read it before doing anything
non-trivial in the repo.

---

## Quick context

- **Purpose:** A/B compare two Unity Catalog tables (any catalogs, any
  environments — TEST vs PROD, v1 vs v2, blue vs green, etc.) and surface
  the differences for data engineers shipping changes between environments.
- **Frontend:** Streamlit, deployed as a Databricks App.
- **Profiling engine:** ydata-profiling with the Spark backend
  (planned, milestone 2).
- **Comparison engine:** custom PSI / KS / Chi-square / JS divergence with
  schema-diff (planned, milestone 4).
- **Outputs (per run):** per-side HTML profile, comparison HTML, Excel
  summary workbook, `metamodel.json`, schema file, 3 Mermaid diagrams,
  rows appended to Delta governance tables. All in a UC Volume.

---

## Environment topology

- **Cloud:** Azure Databricks.
- **Workspaces:** single workspace; one Unity Catalog metastore.
- **Environments:** `test_main` and `prod_main` are catalogs in the same
  metastore. **No Delta Sharing required** for current scope (Pattern 1
  topology).
- **Profiler Host:** `test_main`. The app's service principal writes
  outputs to `test_main.profiler.ab_runs` (a UC Volume). Intentional —
  keeps tooling artifacts out of PROD.
- **Connections** are declared in `connections.yaml`. Both TEST and PROD
  currently `type: native` since they live in the same metastore. If a
  future env lives in a separate workspace, add a `type: delta_share`
  entry — code supports it without changes (README §4 has the D2D Sharing
  runbook).

---

## Repo layout

```
.
├── CLAUDE.md                     <- this file
├── README.md                     <- human-facing setup + deploy docs
├── app.yaml                      <- Databricks Apps manifest
├── streamlit_app.py              <- UI
├── connections.yaml              <- connection registry (TEST + PROD)
├── requirements.txt              <- runtime deps
├── requirements-dev.txt          <- dev deps (pytest, coverage)
├── profiler/
│   ├── __init__.py
│   ├── catalog.py                <- UC lookups (mock + databricks backends)
│   ├── storage.py                <- run folder + volume I/O
│   ├── manifest.py               <- per-run manifest (inputs/timings)
│   ├── metamodel.py              <- DQ metamodel (Pydantic v2) — single source of truth
│   ├── profile.py                <- (stub, milestone 2: ydata-profiling Spark)
│   ├── compare.py                <- (stub, milestone 3/6: schema + stat + row diff)
│   ├── drift.py                  <- (stub, milestone 4: PSI/KS/Chi-square/JS)
│   └── excel.py                  <- (stub, milestone 3: workbook writer)
├── tests/
│   ├── __init__.py
│   └── test_metamodel.py         <- 44 tests
└── .github/workflows/test.yml    <- CI: pytest on Python 3.10/3.11
```

---

## Current status

| #     | Scope                                                         | Status |
|-------|---------------------------------------------------------------|--------|
| 1     | Skeleton: UI, validation, run folder, manifest                | ✅ Done |
| 2     | ydata-profiling Spark → per-side HTML                         |        |
| 3     | Schema diff + aggregate stat diff → Excel workbook            |        |
| 4     | Drift module: PSI, KS, Chi-square, JS divergence              |        |
| 4.5.1 | DQ metamodel (Pydantic v2) + JSON + JSON Schema export        | ✅ Done |
| 4.5.2 | Mermaid renderer (3 diagrams) + Delta repo ingestion          | 🔜 Next |
| 5     | ydata `compare()` side-by-side HTML                           |        |
| 6     | Optional row-level diff via row keys                          |        |
| 7     | Runs-history sidebar table                                    |        |
| 8     | Hardening: all-string-table guard, sampling heuristic, error UX |      |

---

## Key design decisions (locked in)

These should be respected unless explicitly revisited with the user.

### UML metamodel approach
- `profiler/metamodel.py` is the **single source of truth**. Every other
  output format (Mermaid diagrams, JSON files, Delta tables, future XMI)
  serializes from the same Pydantic object graph.
- **PlantUML and XMI export are deferred** until a concrete external
  consumer (UML tool, governance catalog) appears.
- **Stereotypes are JSON-tag strings** like `["MeasuredAttribute","Drifted"]`,
  not formal UML Profile machinery. The string literal type is in
  `metamodel.Stereotype`.

### Schema versioning
- `METAMODEL_VERSION = "1.0"` constant in `profiler/metamodel.py`.
- Emit `dq-metamodel-v<MAJOR>.schema.json` per major version into the run
  folder (`schema_for_current_version()` returns the schema dict).
- All models use `extra="ignore"` — old code reads new (minor-version)
  payloads without crashing. Major-version bumps are explicit breaks;
  minor versions add fields.

### Run identity
- `run_id` is **UUIDv7** (RFC 9562) — sortable by creation time, globally
  unique, retry-idempotent. Implemented inline in `metamodel.py` to avoid
  the `uuid-utils` dependency.
- Folder names embed the timestamp for humans; the UUID is the primary key
  for Delta MERGEs.

### Histogram storage
- Parallel arrays: `histogram_edges: list[float]`,
  `histogram_counts: list[int]` where `len(edges) == len(counts) + 1`.
- Capped at 50 bins (`HISTOGRAM_MAX_BINS`).
- For tables with >500 columns, histograms may eventually move to a
  separate `column_histograms` Delta table partitioned by run_id.

### Delta governance repo (milestone 4.5.2)
- Five tables: `profiler_runs`, `dataset_profiles`, `column_profiles`,
  `column_alerts`, `column_comparisons`.
- Partition by `created_date`.
- Liquid clustering on `(catalog, schema, table, column_name)`.
- **Direct Delta write from the app**, not Auto Loader — producer/consumer
  are co-located. JSON file is the canonical artifact; Delta tables are
  rebuildable from JSON.
- Retention: 1 year, daily `OPTIMIZE`, weekly `VACUUM`.

### Streamlit Mermaid rendering (milestone 4.5.2)
- Use `st.components.v1.html` with Mermaid.js CDN inline.
- **Do not** depend on `streamlit-mermaid` or `st-mermaid` packages —
  maintenance is uncertain.
- Three diagrams per run, not one:
  1. Side-A schema with per-column alerts and stats
  2. Side-B schema (same shape)
  3. **Drift-only** diagram showing just the columns that moved, with
     cross-side associations stereotyped `<<Drifted>>` and tagged with
     PSI/verdict. This is the most-used view.
- For wide tables (>30 columns), add a "summary mode" that only shows
  columns with alerts or significant drift.

### Verdict semantics
- PSI thresholds (credit-risk convention):
  - `< 0.1` → stable
  - `0.1 ≤ PSI < 0.2` → moderate
  - `≥ 0.2` → significant
- Use `verdict_from_psi(psi, schema_change)` helper — don't hard-code
  thresholds at call sites. A validator enforces consistency between
  stored `verdict` and `psi`.

---

## Runtime modes

The `PROFILER_RUNTIME` environment variable controls behavior:

- **`databricks`** (production): UC lookups via `system.information_schema`
  through `databricks-sql-connector` against a SQL warehouse. Real Volume
  writes.
- **`mock`** (local dev): Returns hard-coded fake catalogs/schemas/tables/
  volumes from `profiler/catalog.py` (`_MOCK_NATIVE`, `_MOCK_SHARED`).
  Volume writes redirect to `./_mock_runs/`. Lets the Streamlit UI run
  locally without a Databricks connection.
- Set in `app.yaml` for production; in PowerShell for local dev:
  `$env:PROFILER_RUNTIME = "mock"`.

The `_MOCK_NATIVE` / `_MOCK_SHARED` split exists to mirror real behavior —
a native auto-discover should not return shared catalogs.

---

## Workflow & conventions

- **Branching:** trunk-based. Feature branches → PR → `main`. No `develop`.
- **Branch names:** `feature/<slug>` (e.g., `feature/4.5.2-mermaid-delta`).
- **Commits:** imperative mood ("Add Mermaid renderer"), milestone prefix
  for substantive changes ("4.5.2: …").
- **CI:** `.github/workflows/test.yml` runs pytest on Python 3.10 + 3.11
  with coverage on every PR. Required to pass before merge.
- **Tests:** add unit tests under `tests/` for every new module. Goal: ≥80%
  coverage on the metamodel and drift modules. Test files import directly
  from the `profiler` package — see `tests/test_metamodel.py` for the
  pattern (builders for synthetic data, then class-grouped tests).
- **Run tests locally:** `pytest tests/ -v` (or
  `pytest tests/ -v --cov=profiler --cov-report=term-missing` for coverage).

---

## Local dev setup

```powershell
cd C:\Users\garyf\synaptiqrepos\Synaptiq-dbx-dev
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

# UI in mock mode
$env:PROFILER_RUNTIME = "mock"
streamlit run streamlit_app.py

# Tests
pytest tests/ -v
```

---

## Databricks deployment

See `README.md` §6 for full steps. Summary:

- Output volume: `test_main.profiler.ab_runs` (must exist; create with the
  SQL in `README.md` §2).
- SQL warehouse: any serverless warehouse the app SP can use.
- Both wired in `app.yaml` → `resources.output-volume` and
  `resources.sql-warehouse`. Adjust catalog/warehouse-id per workspace.
- App SP needs: `USE CATALOG` + `USE SCHEMA` + `SELECT` on every catalog
  the app can read; `WRITE VOLUME` on the output volume; `CAN USE` on the
  warehouse; `SELECT` on `system.information_schema`.

---

## Things to do a certain way

- **Code comments explain *why*, not what.** The code shows the what.
- **Pydantic v2 patterns:** `field_validator`, `model_validator(mode="after")`,
  `ConfigDict`, `model_dump_json(exclude_none=True, by_alias=True)`.
- **Type hints** on all public functions and dataclass-style models.
- Use `from __future__ import annotations` at the top of new modules.
- Prefer explicit imports over wildcards.
- **No emojis** in code, commits, or PR titles.
- **Match the stub style** in `profile.py`/`compare.py`/`drift.py`/`excel.py`:
  short docstring noting the milestone they belong to, then
  `raise NotImplementedError("... — milestone N").` Replace, don't add
  alongside.

---

## Known gotchas

- **ydata-profiling Spark backend** has reduced functionality for
  all-string tables and some correlation types. Mitigation (planned,
  milestone 8): detect all-string tables and auto-disable correlations +
  interactions; flag in manifest.
- **OneDrive caching** can conflict with `.git/index.lock`. Repo lives at
  `C:\Users\garyf\synaptiqrepos\` (outside OneDrive) deliberately.
- **`schema` is a reserved-ish name.** `DatasetProfile` uses `schema_`
  internally with `Field(alias="schema")` so the wire format stays clean.
  Always serialize with `by_alias=True`.
- **Stale `.git/index.lock`** can be left behind when a git operation is
  killed. Safe to delete *only* after closing every git client (VS Code,
  PowerShell, etc.):
  `Remove-Item .git/index.lock -Force`
- **ydata HTML can be large** (several MB for wide tables). Don't load it
  into memory; let Streamlit iframe it directly from the Volume.

---

## Out of scope (do not add unless explicitly asked)

- PlantUML and XMI export
- Auto Loader / streaming ingestion for the Delta repo
- Cross-workspace deployments via Delta Sharing (the path is documented
  in `README.md` §4 for future use, but not active)
- Lakehouse Federation
- A separate web frontend beyond Streamlit
- A REST API
- Multi-tenant or org-level features

---

## How to pick up the next milestone

**4.5.2 next steps:**
1. Branch: `feature/4.5.2-mermaid-delta` off `main`.
2. Create `profiler/mermaid.py` that walks `ProfilerRun` → 3 `.mmd` strings.
3. Update `profiler/storage.py` to write `metamodel.json` and
   `dq-metamodel-v1.schema.json` into the run folder.
4. Create `profiler/delta_repo.py` with table DDL (idempotent CREATE TABLE
   IF NOT EXISTS), a flatten function `(ProfilerRun) -> dict[str, pd.DataFrame]`,
   and a MERGE-based ingestion function. Liquid clustering applied at table
   creation.
5. Update `streamlit_app.py` Run action to call all three at end of run.
6. Tests: `tests/test_mermaid.py` (snapshot-style for diagram strings),
   `tests/test_delta_repo.py` (flatten correctness; ingestion against a
   local SQLite or just check the SQL strings).
7. PR → main; CI green → merge.

See `tests/test_metamodel.py` for the test pattern. Run `pytest` before
every push.
