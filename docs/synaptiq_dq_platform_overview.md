# Synaptiq Data Profiling Tool
## Product Overview & Technical Reference

**Version:** 1.0 POC &nbsp;|&nbsp; **Platform:** Azure Databricks &nbsp;|&nbsp; **Date:** June 2026  
**Author:** Synaptiq Engineering

---

# SECTION 1 — EXECUTIVE SUMMARY
### *For Business & Clinical Stakeholders*

---

## What Is It?

The **Synaptiq Data Profiling Tool** is an intelligent data quality platform built on Azure Databricks. It automatically profiles healthcare data tables, detects anomalies and statistical drift between environments, and provides a conversational AI interface that lets users ask plain-English questions about their data — without writing SQL.

It was purpose-built to solve a critical challenge facing healthcare data teams: **how do you know your data is safe to use before it reaches clinical decision-making, reporting, or downstream analytics?**

---

## The Problem It Solves

Healthcare data moves through multiple environments — source systems, ETL pipelines, test environments, and production — and changes constantly. Without automated quality checks, teams face:

| Problem | Impact |
|---|---|
| Schema changes go undetected | Broken pipelines, silent data loss |
| Distribution shifts between environments | Wrong analytics, incorrect clinical insights |
| Manual data validation | Hours of engineer time per release |
| No audit trail of data quality | Compliance risk, no root-cause history |
| Data quality hidden in notebooks | Not accessible to analysts or business users |

The Synaptiq Data Profiling Tool eliminates all of these through automated profiling, drift detection, and a persistent quality record.

---

## What It Does

The platform operates in two modes and surfaces results through three integrated experiences:

### Mode 1 — Compare Two Tables (A/B Profiling)
A data engineer selects two versions of the same table — for example, the TEST environment versus PROD, or this month's load versus last month's. The tool:
- Profiles both sides (row counts, column statistics, null rates, distributions)
- Detects schema changes (added, removed, or type-changed columns)
- Computes statistical drift metrics (PSI, KS test, Chi-square, Jensen-Shannon divergence)
- Assigns a **verdict** to every column: **stable**, **moderate drift**, or **significant drift**
- Fires alerts for anomalous patterns (extreme nulls, constant columns, imbalanced distributions)
- Writes all results to a persistent Delta governance repository

### Mode 2 — Profile One or More Tables
Engineers can profile any table independently for a point-in-time quality snapshot — without comparison.

---

## Key Outputs Per Run

| Output | Format | Where |
|---|---|---|
| HTML profile report | Interactive web page | UC Volume |
| Excel workbook | 5-sheet summary (Schema Diff, Column Metrics, Alerts, Drift Scores) | UC Volume |
| Schema diagrams | Mermaid diagrams (Side A, Side B, Drift-only view) | UC Volume |
| Metamodel JSON | Machine-readable quality record | UC Volume |
| Governance records | 5 Delta tables with full history | Unity Catalog |

---

## The Governance Dashboard

All profiling history is queryable through a **Databricks AI/BI Dashboard** with three pages:

- **Schema Drift** — which tables changed over time and how
- **Column Drift** — PSI trends, alert heatmaps, null% shifts across all runs
- **Run Comparison** — drill into any single A/B run for full column-level detail

---

## Conversational AI — Ask Genie

Users can ask the **Genie AI assistant** questions about their data quality history in plain English:

> *"Which columns have significant drift this month?"*  
> *"Show me all critical alerts from the last 7 days."*  
> *"Compare average PSI for medical claims vs lab results."*

Genie returns a natural-language answer, the SQL it ran for transparency, and the result table — making data quality insights accessible to analysts, clinical informatics staff, and business stakeholders without any SQL knowledge.

---

## Who Uses It

| User | How They Use It |
|---|---|
| **Data Engineers** | Run A/B comparisons before promoting code to production |
| **Data Quality Analysts** | Monitor alert trends and drift signals across the data estate |
| **Clinical Informatics** | Ask Genie about data quality before trusting analytics |
| **Data Governance / Compliance** | Audit trail of all quality runs in the governance Delta tables |

---

## High-Level Topology

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          SYNAPTIQ DATA PROFILING TOOL                           │
│                              Azure Databricks Platform                           │
└─────────────────────────────────────────────────────────────────────────────────┘

  USERS                     DATABRICKS APP                  UNITY CATALOG
  ─────                     ──────────────                  ─────────────
                            ┌──────────────────┐
  Data Engineers   ────────▶│                  │──────────▶  dev.prod_main_claims
  Data Analysts    ────────▶│   Streamlit UI   │──────────▶  dev.test_main_claims
  Business Users   ────────▶│  (Synaptiq DQ    │──────────▶  dev.prod_main_clinical
                            │   Platform)      │──────────▶  dev.test_main_clinical
                            │                  │──────────▶  dev.prod_main_members
                            └──────┬───────────┘──────────▶  dev.test_main_members
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼               ▼
             ┌──────────┐  ┌────────────┐  ┌─────────────┐
             │SQL        │  │UC Volume   │  │Governance   │
             │Warehouse  │  │ab_runs/    │  │Delta Tables │
             │(profiling │  │├─ HTML     │  │├─profiler   │
             │ queries)  │  │├─ Excel    │  ││  _runs     │
             └──────────┘  │├─ Mermaid  │  │├─dataset    │
                           │└─ JSON     │  ││  _profiles │
                           └────────────┘  │├─column     │
                                           ││  _profiles │
                                           │├─column     │
                                           ││  _alerts   │
                                           │└─column     │
                                           │  _comparisons│
                                           └─────────────┘
                                                  │
                                    ┌─────────────┴──────────────┐
                                    ▼                            ▼
                           ┌─────────────────┐        ┌──────────────────┐
                           │ AI/BI Dashboard  │        │  Genie AI Space  │
                           │ ├─ Schema Drift  │        │  (Conversational │
                           │ ├─ Column Drift  │        │   Q&A over       │
                           │ └─ Run Compare   │        │   governance     │
                           └─────────────────┘        │   tables)        │
                                                       └──────────────────┘
```

---

---

# SECTION 2 — TECHNICAL REFERENCE
### *For Data Engineers & Platform Teams*

---

## Architecture Overview

The platform is a **Databricks App** (containerised Streamlit web application) running on Azure Databricks with serverless compute. It has no external dependencies beyond Azure Databricks — no additional cloud services, no external databases.

### Technology Stack

| Layer | Technology |
|---|---|
| UI Framework | Streamlit (Python) |
| Hosting | Databricks Apps (containerised, serverless) |
| Catalog & Security | Unity Catalog (row/column-level security enforced at UC layer) |
| Data Storage | UC Volumes (artifact files) + Delta Lake (governance tables) |
| SQL Execution | Databricks Statement Execution API (no JDBC connector) |
| Metadata Lookups | Databricks SDK `WorkspaceClient` — UC REST API |
| Drift Metrics | Python: SciPy (KS test, Chi-square), NumPy (PSI, JS divergence) |
| Conversational AI | Databricks AI/BI Genie Conversation API |
| Identity | OAuth M2M (app service principal); UC enforces per-table grants |

---

## Application Modules

```
profiler/
├── profile.py        — Data fetch (Statement Execution API) + column statistics
├── compare.py        — Schema diff + stat_diff (PSI/KS/Chi-sq/JS dispatch)
├── drift.py          — Pure drift metric calculations (PSI, KS, Chi-sq, JS divergence)
├── row_diff.py       — Row-level diff via key columns (hash-based change detection)
├── excel.py          — 5-sheet Excel workbook generator (openpyxl)
├── mermaid.py        — 3 Mermaid classDiagram renderers (schema + drift views)
├── delta_repo.py     — Governance table DDL + Statement API ingestion
├── storage.py        — UC Files API read/write (no FUSE mount required)
├── catalog.py        — UC REST API: schema/table/volume discovery
├── metamodel.py      — Pydantic v2 DQ metamodel (single source of truth)
├── manifest.py       — Run manifest (inputs, timings, artifacts)
└── genie_chat.py     — Databricks Genie Conversation API wrapper
```

---

## Profiling Pipeline (per run)

```
Step 1  Fetch data via Statement Execution API
        SELECT * FROM {table} [WHERE DATE(META_Load_DTTM) = '{date}'] LIMIT 50,000

Step 2  Compute per-column statistics (pandas)
        Numeric:     mean, stddev, min/max, 7 percentiles, skewness, kurtosis, histogram
        Categorical: top-20 frequencies, Shannon entropy
        All columns: null count/%, distinct count/%, DQ alerts, stereotypes

Step 3  Generate HTML profile report (custom pandas renderer — no ydata-profiling)

Step 4  Schema diff (compare column lists A vs B)
        Detects: added | removed | type_changed | nullability_changed | unchanged

Step 5  Drift metrics (pure Python, from pre-computed histograms)
        PSI       — Population Stability Index (thresholds: 0.10 moderate, 0.20 significant)
        KS stat   — Kolmogorov-Smirnov test (numeric columns)
        Chi-sq    — Chi-square goodness-of-fit (categorical columns)
        JS div    — Jensen-Shannon divergence [0–1] (all column types)
        Verdict   — stable / moderate / significant / schema_change

Step 6  Row-level diff (optional, key-column based)
        Uses hash(row) via CTE to detect changed rows; counts + samples

Step 7  Write artifacts (UC Files API)
        profile_a.html, profile_b.html, ab_summary.xlsx,
        schema_a.mmd, schema_b.mmd, drift.mmd,
        metamodel.json, dq-metamodel-v1.schema.json, manifest.json

Step 8  Ingest governance records (Statement Execution API)
        CREATE TABLE IF NOT EXISTS (once) + DELETE + batch INSERT per run
```

---

## Governance Delta Schema

**Catalog:** `dev` &nbsp;|&nbsp; **Schema:** `test_main_profiler`

```
profiler_runs              (1 row per run)
  run_id UUID PK, run_label, metamodel_version,
  created_utc, created_date,
  side_a_fqn, side_b_fqn, lineage_json

dataset_profiles           (2 rows per run — one per side)
  run_id, side (A|B), env_label, connection,
  catalog, schema, table,
  row_count, column_count, duplicate_rows

column_profiles            (1 row per column per side per run)
  run_id, side, catalog, schema, table, column_name,
  logical_type, physical_type, nullable,
  null_count, null_pct, distinct_count, distinct_pct,
  stereotypes,
  numeric_mean, numeric_stddev, numeric_p50, numeric_min, numeric_max,
  cat_entropy, cat_top_k_json

column_alerts              (1 row per alert per column per side per run)
  run_id, side, catalog, schema, table, column_name,
  rule, severity (critical|warn|info), message

column_comparisons         (1 row per column comparison per run)
  run_id, catalog_a/b, schema_a/b, table_a/b, column_name,
  schema_change (added|removed|type_changed|nullability_changed|unchanged),
  psi, ks_stat, ks_pvalue, chi_square, js_divergence,
  verdict (stable|moderate|significant|schema_change),
  stereotypes
```

**All tables:** Liquid Clustering on `(catalog, schema, table, column_name)`, Delta auto-optimize enabled.

---

## Synthetic POC Dataset

To validate the platform, a full Tuva Project Input Layer synthetic dataset was generated:

| Layer | Schemas | Tables | Rows (each) |
|---|---|---|---|
| Claims | `prod_main_claims`, `test_main_claims` | medical_claim (58 cols), pharmacy_claim, provider_attribution | 50,000 |
| Clinical | `prod_main_clinical`, `test_main_clinical` | encounter, condition, procedure, lab_result, observation, medication, immunization, appointment | 25,000 |
| Members | `prod_main_members`, `test_main_members` | eligibility, patient | 5,000 |

**Intentional drift baked in for POC demonstration:**

| Signal | PROD | TEST | Expected PSI |
|---|---|---|---|
| `charge_amount` | lognormal(μ=6.9) → mean ~$1,400 | lognormal(μ=7.2) → mean ~$1,900 | ~0.13 moderate |
| Denial rate | 15% | 20% | stable |
| HbA1c mean | 7.2 | 8.1 | moderate |
| BMI mean | 27.5 | 29.2 | stable |

A 28-day nightly load simulation (`notebooks/simulate_nightly_loads.py`) appends incremental records with `META_Load_DTTM` timestamps, enabling time-series profiling and load-date filtering.

---

## Genie Conversational AI Integration

**Space ID:** `01f15f8e849e1c31a47c5785d07751bf`  
**Auth pattern:** Service principal (OAuth M2M) — SP has `CAN RUN` on the space  
**API:** Databricks AI/BI Genie Conversation API (Public Preview)

```
Conversation flow:
  POST /api/2.0/genie/spaces/{space_id}/start-conversation   → conv_id, msg_id
  POST /api/2.0/genie/spaces/{space_id}/conversations/{id}/messages → msg_id
  GET  /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}
       → poll until status = COMPLETED
  GET  /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}/query-result
       → SQL, column schema, row data

Response parsed from:
  attachments[].text.content    → natural-language answer
  attachments[].query.query     → generated SQL (shown in expander)
  query-result endpoint          → result rows (shown as dataframe)
```

**Rate limit note:** Genie API allows ~5 questions/minute per workspace in Public Preview. For higher concurrency, provisioned capacity should be evaluated before production go-live.

---

## Deployment

| Component | Value |
|---|---|
| App name | `synaptiq-dq-platform` |
| App URL | `https://synaptiq-dq-platform-7405619521761591.11.azuredatabricks.net/` |
| SQL warehouse | `2ad65b4df5cd3a9e` (serverless) |
| Output volume | `dev.test_main_profiler.ab_runs` |
| App service principal | `39ee93a7-c623-4614-90a8-c3798bb5b329` |
| Source repo | `github.com/garyfischer-maker/Synaptiq-dbx-dev` (branch: `main`) |
| Databricks dashboard | `/dashboardsv3/01f15f7d18171dac85cdc247e47d48a5` |

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| SQL execution | Statement Execution API (not JDBC) | JDBC `sql.connect()` hangs indefinitely in Databricks Apps container; REST API uses same OAuth path as catalog lookups |
| File I/O | UC Files API (not FUSE mount) | FUSE mount not available in Apps container; Files API works without any mount setup |
| Profiling engine | Custom pandas stats (not ydata-profiling) | ydata-profiling adds 2–5 min startup even on tiny tables; pandas completes in seconds |
| Drift metrics | Pure Python from histograms | No Spark re-read needed; all stats pre-computed during profiling phase |
| Governance write | DELETE + batch INSERT (not Spark MERGE) | No SparkSession in Apps container; Statement API handles DDL + DML |
| Schema discovery | UC REST API (not SQL warehouse) | Warehouse cold-start blocks UI; UC REST returns in <1 second |

---

*Document generated from `Synaptiq-dbx-dev` repository — June 2026*
