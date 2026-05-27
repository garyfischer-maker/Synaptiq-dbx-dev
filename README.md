# table-ab-profiler

A Databricks App that profiles two Unity Catalog tables and surfaces the
differences. Built for an **Azure Databricks** deployment with separate
workspaces per environment (TEST, PROD, …), all in the **same Databricks
account**, using **Databricks-to-Databricks (D2D) Delta Sharing** for
cross-environment reads.

**Status:** milestone 1 — UI skeleton + validation + run-folder/manifest
scaffolding. Profiling and comparison are stubbed.

---

## 1. How it's wired

```
TEST workspace            PROD / Profiler Host workspace
(separate metastore)      (Profiler App lives here)
 ┌────────────┐  D2D      ┌────────────────────────┐
 │ catalog:   │ Delta     │ shared catalog:        │
 │ test_main  │  Share    │ test_sh (read-only)    │
 └────────────┘  ───────► │                        │
                          │ local catalog:         │
                          │ prod_main              │
                          │                        │
                          │ ┌── Databricks App ──┐ │
                          │ │ table-ab-profiler  │ │
                          │ │ (Streamlit)        │ │
                          │ │    writes →        │ │
                          │ │  /Volumes/prod_… │ │
                          │ └────────────────────┘ │
                          └────────────────────────┘
```

Env labels in the UI (DEV/TEST/QA/STAGE/PROD/custom) are metadata. Actual
routing comes from `connections.yaml`, which lists each reachable connection
(native or delta_share) and the catalogs it exposes.

---

## 2. Repo layout

```
table-ab-profiler/
├── app.yaml                   Databricks Apps manifest (resources + env)
├── requirements.txt
├── connections.yaml           Connection registry (edit to add new envs)
├── streamlit_app.py           UI
├── profiler/
│   ├── __init__.py
│   ├── catalog.py             UC lookups (real + mock backends)
│   ├── storage.py             Run folder + volume I/O
│   ├── manifest.py            Run manifest
│   ├── profile.py             (stub — milestone 2)
│   ├── compare.py             (stub — milestone 3/6)
│   ├── drift.py               (stub — milestone 4)
│   └── excel.py               (stub — milestone 3)
├── tests/                     (milestone 4+)
└── README.md
```

---

## 3. One-time admin setup (Profiler Host / PROD workspace)

### 3.1 Output volume

Pick a PROD catalog the app can write to, and create:

```sql
CREATE SCHEMA IF NOT EXISTS <prod_catalog>.profiler;
CREATE VOLUME IF NOT EXISTS <prod_catalog>.profiler.ab_runs;
```

Update `app.yaml` → `resources.output-volume` to match.

### 3.2 SQL warehouse

Any serverless warehouse the service principal can use. Put its ID in
`app.yaml` → `resources.sql-warehouse.id`.

### 3.3 Service principal grants

The app runs as an auto-provisioned service principal. Grant it:

| Resource                                   | Privileges |
| ------------------------------------------ | ---------- |
| Every catalog the app should read          | `USE CATALOG` |
| Every schema containing profilable tables  | `USE SCHEMA` |
| The tables themselves                      | `SELECT` |
| `<prod_catalog>.profiler`                  | `USE SCHEMA`, `CREATE VOLUME`, `READ VOLUME`, `WRITE VOLUME` |
| `system.information_schema`                | `USE CATALOG`, `USE SCHEMA`, `SELECT` |
| The SQL warehouse                          | `CAN USE` |

---

## 4. Wiring a source environment (e.g. TEST) via D2D Delta Sharing

Run in **the source (TEST) workspace**:

```sql
-- 1. Register the Profiler Host metastore as a recipient.
CREATE RECIPIENT profiler_host
  USING ID '<profiler-host-metastore-id>';    -- D2D: use the metastore ID, not a token

-- 2. Create a share exposing the catalogs/schemas to profile.
CREATE SHARE test_for_profiler;
ALTER SHARE test_for_profiler
  ADD SCHEMA test_main.sales;                 -- add whatever schemas matter

-- 3. Grant the share to the recipient.
GRANT SELECT ON SHARE test_for_profiler TO RECIPIENT profiler_host;
```

Run in **the Profiler Host (PROD) workspace**:

```sql
-- 4. Mount the share as a read-only catalog.
CREATE CATALOG test_sh USING SHARE <test_provider>.test_for_profiler;

-- 5. Grant the app SP read access.
GRANT USE CATALOG, USE SCHEMA ON CATALOG test_sh TO `<app-sp>`;
GRANT SELECT ON ALL TABLES IN CATALOG test_sh TO `<app-sp>`;
```

Then edit `connections.yaml` and uncomment the `TEST (shared)` block.
Redeploy the app — no code change required.

Repeat for DEV, QA, STAGE as needed.

---

## 5. Local development (no Databricks)

The app has a mock backend for offline UI iteration:

```bash
pip install -r requirements.txt
export PROFILER_RUNTIME=mock
streamlit run streamlit_app.py
```

- Catalog/schema/table/volume dropdowns return a fixed fake dataset.
- Validate skips the row-count query.
- Run still creates a real run folder, but under `./_mock_runs/` instead
  of `/Volumes/...`.

---

## 6. Deploying to Databricks Apps

```bash
# From the table-ab-profiler directory:
databricks apps deploy table-ab-profiler \
  --source-code-path . \
  --target-workspace <profiler-host>
```

Or use the Databricks Apps UI: create app → point at this folder → review
`app.yaml` → deploy.

---

## 7. Roadmap (by milestone)

| # | Scope |
|---|-------|
| 1 | ✅ Skeleton: UI, validation, run folder, manifest. |
| 2 | ydata-profiling Spark backend → `test_profile.html`, `prod_profile.html`. |
| 3 | Schema diff + aggregate stat diff in pure Spark → Excel workbook. |
| 4 | Drift module: PSI / KS / Chi-square / JS divergence with unit tests. |
| 5 | ydata `compare()` → `comparison.html` (side-by-side). |
| 6 | Optional row-level diff via row keys (hash + mismatch sample). |
| 7 | Runs-history table on sidebar, download-as-zip. |
| 8 | Hardening: all-string-table guard, large-table sampling heuristic, error UX. |

---

## 8. Known Azure-specific gotchas

- **Private endpoints / storage firewalls:** D2D Sharing reads the source
  storage account directly from the Profiler Host compute. Networking team
  must allow Profiler Host compute → source storage.
- **Customer-managed keys:** recipient must be able to decrypt shared
  storage; coordinate with whoever owns the CMK.
- **Row-level diff across shared catalogs** is slower than native — push down
  filters (date, partition col) before joining.
test