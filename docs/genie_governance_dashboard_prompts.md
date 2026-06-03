# Databricks AI/BI Genie Dashboard — DQ Governance Analytics
# Designed using the /genie-dashboard-design skill (3-gate workflow)

Source catalog: `dev.test_main_profiler`
Tables: `profiler_runs`, `dataset_profiles`, `column_profiles`, `column_alerts`, `column_comparisons`

---

## Gate 1 — Plan

### Dashboard Purpose
Provide data engineers and analysts with a self-service view into the Synaptiq Data Profiling
Tool's governance output — schema stability over time, column-level drift metrics, and
side-by-side run comparisons. Primary audience: data engineers shipping table changes between
environments; secondary audience: data quality analysts monitoring alert trends.

### Pages & Layout

| Page | Purpose | Key Metrics |
|---|---|---|
| Schema Drift | Table-level: which schemas are changing, how often, what type | Change counts, change type breakdown, worst tables |
| Column Drift | Column-level: PSI, KS, alert heatmap, null% shifts | PSI distribution, verdict breakdown, alert severity |
| Run Comparison | Single-run drill-in: A vs B side-by-side | Row counts, per-column stats, verdict, alert detail |

### Global Filters (cascade to all pages)
- **Catalog** — derived from `split(side_a_fqn, '.')[0]`; cardinality ~1; default All
- **Schema** — derived from `split(side_a_fqn, '.')[1]`; cardinality ~10; default All
- **Table** — derived from `split(side_a_fqn, '.')[2]`; cardinality ~15; default All
- **Load date range** — `created_date` in `profiler_runs`; default Last 30 days
- **Run ID** — searchable dropdown; default most recent

### Data Quality Notes
- `psi`, `ks_stat`, `js_divergence`, `chi_square` are NULL for schema-changed columns — always filter `WHERE schema_change = 'unchanged'` before computing drift averages
- `column_profiles.null_pct` and `distinct_pct` are fractions (0.0–1.0), not percentages — multiply by 100 for display
- `column_comparisons` has one row per column per run — join to `profiler_runs` on `run_id` to get `created_date`
- `column_alerts` has one row per alert per column per side per run — a column can have multiple alerts

---

## Gate 2 — Design Review / Wireframes

### Page 1 — Schema Drift

```
┌─────────────────────────────────────────────────────────────────────┐
│  KPI: Total Runs │ KPI: Schema Changes │ KPI: Tables Changed │ KPI: Most Common Type │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────┬──────────────────────────┐
│  Line: Schema Changes Over Time          │  Donut: Change Type      │
│  (lines per change type, x=date)         │  Distribution            │
└──────────────────────────────────────────┴──────────────────────────┘

┌──────────────────────────────────────────┬──────────────────────────┐
│  Horiz Bar: Top 15 Tables by Change Count│  Stacked Bar: Added vs   │
│  (sorted desc, colour by type)           │  Removed Per Run         │
└──────────────────────────────────────────┴──────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Table: Schema Change Log                                           │
│  Cols: run_id(8), table, column, change_type, verdict, date         │
└─────────────────────────────────────────────────────────────────────┘
```

### Page 2 — Column Drift

```
┌──────────────────────────────────────────────────────────────────┐
│ KPI: Cols Compared │ KPI: Significant │ KPI: Moderate │ KPI: Alerts │
└──────────────────────────────────────────────────────────────────┘

┌────────────────────────────────┬────────────────┬────────────────┐
│ Donut: Verdict Distribution    │ Line: Avg PSI   │ Donut: Alert   │
│ (stable/moderate/significant)  │ Trend Over Time │ Severity Split │
└────────────────────────────────┴────────────────┴────────────────┘

┌──────────────────────────────────┬───────────────────────────────┐
│ Horiz Bar: Top 20 Drifted Cols   │ Table: Alert Heatmap          │
│ (avg PSI, colour by verdict)     │ (column × rule × count)       │
└──────────────────────────────────┴───────────────────────────────┘

┌──────────────────────────────────┬───────────────────────────────┐
│ Grouped Bar: Null% A vs B        │ Horiz Bar: KS Stat by Column  │
│ (top 20 largest delta)           │ (numeric cols only)           │
└──────────────────────────────────┴───────────────────────────────┘
```

### Page 3 — Run Comparison

```
┌────────────────────────────────────────────────────────────────────┐
│ KPI: Side A Rows │ KPI: Side B Rows │ KPI: Cols A │ KPI: Drifted │ KPI: Changes │ KPI: Alerts │
└────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────┬────────────────┬────────────────┐
│ Card: Run Metadata              │ Donut: Verdict │ Donut: Alert   │
│ (run_id, labels, dates, FQNs)   │ This Run       │ This Run       │
└─────────────────────────────────┴────────────────┴────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ Table: Column A vs B (main detail — conditional row colours)       │
│ Cols: column, type, schema_change, verdict, PSI, KS, JS,           │
│       null%A, null%B, distinct%A, distinct%B, meanA, meanB, alerts │
└────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────┬───────────────────────────────┐
│ Table: Alerts This Run           │ Horiz Bar: PSI Ranking        │
│ (side, column, rule, severity,   │ (all cols, colour by verdict) │
│  message — sorted crit first)    │                               │
└──────────────────────────────────┴───────────────────────────────┘
```

---

## Gate 3 — Genie Prompts

### Setup Instructions

1. Databricks workspace → **SQL** → **Dashboards** → **Create AI/BI Dashboard**
2. Name it: **Synaptiq DQ Governance Analytics**
3. Set data source to the `dev` catalog
4. Create 3 pages: `Schema Drift` | `Column Drift` | `Run Comparison`
5. Add global filter widgets (instructions below each page)
6. Paste each prompt into a new widget — one prompt = one widget

---

### Global Filter Widgets

```
Filter 1 — Catalog
  Widget: Dropdown, single-select
  Query:
    SELECT DISTINCT split(side_a_fqn, '.')[0] AS catalog
    FROM dev.test_main_profiler.profiler_runs
    WHERE side_a_fqn IS NOT NULL
    ORDER BY 1
  Default: All

Filter 2 — Schema (Side A)
  Widget: Dropdown, multi-select
  Query:
    SELECT DISTINCT split(side_a_fqn, '.')[1] AS schema_name
    FROM dev.test_main_profiler.profiler_runs
    WHERE side_a_fqn IS NOT NULL
    ORDER BY 1
  Default: All

Filter 3 — Table Name
  Widget: Dropdown, multi-select
  Query:
    SELECT DISTINCT split(side_a_fqn, '.')[2] AS table_name
    FROM dev.test_main_profiler.profiler_runs
    WHERE side_a_fqn IS NOT NULL
    ORDER BY 1
  Default: All

Filter 4 — Load Date Range
  Widget: Date range picker
  Column: created_date in dev.test_main_profiler.profiler_runs
  Default: Last 30 days

Filter 5 — Run ID
  Widget: Dropdown, single-select, searchable
  Query:
    SELECT
      run_id || ' | ' || DATE(created_utc) || ' | '
        || split(side_a_fqn, '.')[2] || ' vs '
        || split(side_b_fqn, '.')[2] AS label,
      run_id
    FROM dev.test_main_profiler.profiler_runs
    WHERE created_utc IS NOT NULL
    ORDER BY created_utc DESC
    LIMIT 100
  Default: (most recent run)
```

---

## Page 1 — Schema Drift

### KPI Row

**Widget 1 — Total Profile Runs**
```
Show count of distinct run_id from dev.test_main_profiler.profiler_runs
where created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
and split(side_a_fqn, '.')[0] = {{catalog_filter}} (skip if All)
and split(side_a_fqn, '.')[1] IN ({{schema_filter}}) (skip if All)
as a counter KPI titled "Total Profile Runs".
```

**Widget 2 — Total Schema Changes**
```
Show count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
and split(pr.side_a_fqn, '.')[2] IN ({{table_filter}}) (skip if All)
as a counter KPI titled "Schema Changes Detected".
```

**Widget 3 — Tables with Any Schema Change**
```
Show count of distinct concat(cc.catalog_a, '.', cc.schema_a, '.', cc.table_a)
from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Tables with Schema Changes".
```

**Widget 4 — Most Common Change Type**
```
Show the schema_change value with the highest row count
from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Most Common Change Type".
Display the value as text (e.g. "type_changed").
```

### Row 2 — Trend + Breakdown

**Widget 5 — Schema Changes Over Time**
```
Show a line chart of count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by pr.created_date (x-axis, date granularity = day) and cc.schema_change.
Draw a separate line per schema_change value.
Colour: added=green, removed=red, type_changed=orange, nullability_changed=yellow.
Title it "Schema Changes Over Time".
```

**Widget 6 — Change Type Distribution**
```
Show a donut chart of count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by cc.schema_change.
Colour: added=green, removed=red, type_changed=orange, nullability_changed=yellow.
Title it "Change Type Distribution".
```

**Widget 7 — Top 15 Tables by Schema Change Count**
```
Show a horizontal bar chart of count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by concat(cc.catalog_a, '.', cc.schema_a, '.', cc.table_a) as table_fqn.
Sort descending by count. Show top 15 rows.
Title it "Most Schema Changes by Table".
```

### Row 3 — Detail

**Widget 8 — Columns Added vs Removed Per Run**
```
Show a stacked bar chart from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change IN ('added', 'removed')
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
with pr.created_date on the x-axis (date granularity = day)
and stacked bars for count of cc.column_name where schema_change = 'added' (green)
and count of cc.column_name where schema_change = 'removed' (red).
Title it "Columns Added vs Removed Per Run".
```

**Widget 9 — Schema Change Log**
```
Show a table of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change != 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
and split(pr.side_a_fqn, '.')[2] IN ({{table_filter}}) (skip if All).
Display columns:
  SUBSTR(cc.run_id, 1, 8) AS run_id_short,
  cc.table_a AS table_name,
  cc.column_name,
  cc.schema_change,
  cc.verdict,
  DATE(pr.created_utc) AS run_date.
Sort by pr.created_utc DESC, cc.schema_change ASC.
Limit 200 rows.
Title it "Schema Change Log".
```

---

## Page 2 — Column Drift

### KPI Row

**Widget 1 — Columns Compared (unchanged schema only)**
```
Show count of distinct cc.column_name from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change = 'unchanged'
and cc.psi IS NOT NULL
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Columns Compared (drift eligible)".
Note: only columns with schema_change = 'unchanged' have PSI computed.
```

**Widget 2 — Significant Drift Columns (PSI ≥ 0.2)**
```
Show count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.verdict = 'significant'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Significant Drift" with red colour.
Definition: verdict = 'significant' means PSI >= 0.20.
```

**Widget 3 — Moderate Drift Columns (PSI 0.1–0.2)**
```
Show count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.verdict = 'moderate'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Moderate Drift" with amber colour.
Definition: verdict = 'moderate' means 0.10 <= PSI < 0.20.
```

**Widget 4 — Total Alerts**
```
Show count of rows from dev.test_main_profiler.column_alerts ca
joined to dev.test_main_profiler.profiler_runs pr on ca.run_id = pr.run_id
where pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
as a counter KPI titled "Total Alerts Fired".
Note: one row = one alert; a column can have multiple alerts per run.
```

### Row 2 — Distributions + Trend

**Widget 5 — Drift Verdict Distribution**
```
Show a donut chart of count of rows from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.schema_change = 'unchanged'
and cc.verdict IS NOT NULL
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by cc.verdict.
Colour: stable=green (#27ae60), moderate=amber (#f39c12), significant=red (#e74c3c),
        schema_change=grey (#95a5a6).
Title it "Drift Verdict Distribution".
```

**Widget 6 — Average PSI Trend Over Time**
```
Show a line chart of average(cc.psi) from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.psi IS NOT NULL
and cc.schema_change = 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by pr.created_date on the x-axis (date granularity = day).
Round average PSI to 4 decimal places.
Add reference lines at y=0.10 labelled "Moderate threshold"
and y=0.20 labelled "Significant threshold".
Title it "Avg PSI Trend Over Time".
```

**Widget 7 — Alert Severity Distribution**
```
Show a donut chart of count of rows from dev.test_main_profiler.column_alerts ca
joined to dev.test_main_profiler.profiler_runs pr on ca.run_id = pr.run_id
where pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by ca.severity.
Colour: critical=red (#e74c3c), warn=amber (#f39c12), info=blue (#3498db).
Title it "Alert Severity Distribution".
```

### Row 3 — Ranked Views + Heatmap

**Widget 8 — Top 20 Most Drifted Columns by Average PSI**
```
Show a horizontal bar chart of average(cc.psi) per cc.column_name
from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.psi IS NOT NULL
and cc.schema_change = 'unchanged'
and cc.verdict IN ('moderate', 'significant')
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by cc.column_name.
Sort descending by average PSI. Show top 20 rows.
Colour bars red if average PSI >= 0.20, amber if >= 0.10.
Add reference lines at x=0.10 and x=0.20.
Title it "Most Drifted Columns (avg PSI)".
```

**Widget 9 — Alert Count by Column and Rule**
```
Show a table of count of rows from dev.test_main_profiler.column_alerts ca
joined to dev.test_main_profiler.profiler_runs pr on ca.run_id = pr.run_id
where pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}
grouped by ca.column_name, ca.rule, ca.severity.
Display columns: column_name, rule, severity, count of rows AS alert_count.
Sort by alert_count descending. Limit 30 rows.
Title it "Alert Count by Column and Rule".
```

### Row 4 — Column-Level Detail

**Widget 10 — Null % Change: Side A vs Side B**
```
Show a grouped bar chart comparing null percentage for Side A vs Side B
for the top 20 columns by largest absolute difference in null_pct.

From dev.test_main_profiler.column_profiles cp_a (where cp_a.side = 'A')
joined to dev.test_main_profiler.column_profiles cp_b
  on cp_a.run_id = cp_b.run_id AND cp_a.column_name = cp_b.column_name
  and cp_b.side = 'B'
joined to dev.test_main_profiler.profiler_runs pr on cp_a.run_id = pr.run_id
where pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}.

Compute: null_pct_a = cp_a.null_pct * 100 (percentage, 1 decimal place)
         null_pct_b = cp_b.null_pct * 100 (percentage, 1 decimal place)
         delta = ABS(cp_a.null_pct - cp_b.null_pct) * 100

Group by cp_a.column_name. Sort by delta descending. Show top 20 rows.
Plot Side A bar (blue) and Side B bar (orange) side by side per column.
Title it "Null % Change: Side A vs Side B (top 20 largest shift)".
```

**Widget 11 — KS Statistic by Column (numeric columns)**
```
Show a horizontal bar chart of average(cc.ks_stat) per cc.column_name
from dev.test_main_profiler.column_comparisons cc
joined to dev.test_main_profiler.profiler_runs pr on cc.run_id = pr.run_id
where cc.ks_stat IS NOT NULL
and cc.schema_change = 'unchanged'
and pr.created_date BETWEEN {{load_date_from}} AND {{load_date_to}}.
Round ks_stat to 4 decimal places.
Sort descending. Show top 15 rows.
Add a reference line at x=0.05 labelled "Significance threshold (p<0.05 typical)".
Title it "KS Statistic by Column (numeric columns only)".
```

---

## Page 3 — Run Comparison

*Primary control: {{run_id_filter}} global filter selects the run to inspect.*

### KPI Row

**Widget 1 — Side A Row Count**
```
Show row_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'A'
as a counter KPI titled "Side A — Rows".
Format as integer with thousands separator (e.g. 12,486).
```

**Widget 2 — Side B Row Count**
```
Show row_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'B'
as a counter KPI titled "Side B — Rows".
Format as integer with thousands separator.
```

**Widget 3 — Side A Column Count**
```
Show column_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'A'
as a counter KPI titled "Side A — Columns".
```

**Widget 4 — Drifted Columns This Run**
```
Show count of distinct cc.column_name from dev.test_main_profiler.column_comparisons cc
where cc.run_id = {{run_id_filter}}
and cc.verdict IN ('moderate', 'significant')
as a counter KPI titled "Drifted Columns" with red colour when value > 0.
Definition: drifted = verdict of 'moderate' (PSI 0.10–0.20) or 'significant' (PSI >= 0.20).
```

**Widget 5 — Schema Changes This Run**
```
Show count of distinct cc.column_name from dev.test_main_profiler.column_comparisons cc
where cc.run_id = {{run_id_filter}}
and cc.schema_change != 'unchanged'
as a counter KPI titled "Schema Changes".
```

**Widget 6 — Alerts This Run**
```
Show count of rows from dev.test_main_profiler.column_alerts
where run_id = {{run_id_filter}}
as a counter KPI titled "Alerts Fired".
```

### Row 2 — Run Metadata + Summaries

**Widget 7 — Run Details Card**
```
Show a table with one row from dev.test_main_profiler.profiler_runs
where run_id = {{run_id_filter}}.
Display columns:
  SUBSTR(run_id, 1, 8) AS run_id_short,
  run_label,
  DATE(created_utc) AS run_date,
  created_utc AS run_timestamp,
  side_a_fqn AS side_a_table,
  side_b_fqn AS side_b_table.
Title it "Run Details".
```

**Widget 8 — Verdict Breakdown This Run**
```
Show a donut chart of count of distinct cc.column_name per cc.verdict
from dev.test_main_profiler.column_comparisons cc
where cc.run_id = {{run_id_filter}}
and cc.schema_change = 'unchanged'
and cc.verdict IS NOT NULL.
Colour: stable=green (#27ae60), moderate=amber (#f39c12),
        significant=red (#e74c3c), schema_change=grey (#95a5a6).
Title it "Drift Verdict — This Run".
```

**Widget 9 — Alert Severity This Run**
```
Show a donut chart of count of rows per ca.severity
from dev.test_main_profiler.column_alerts ca
where ca.run_id = {{run_id_filter}}.
Colour: critical=red (#e74c3c), warn=amber (#f39c12), info=blue (#3498db).
Title it "Alert Severity — This Run".
```

### Row 3 — Main Column Comparison Table

**Widget 10 — Column A vs B Comparison (full detail)**
```
Show a table joining:
  dev.test_main_profiler.column_comparisons cc
  LEFT JOIN dev.test_main_profiler.column_profiles cp_a
    ON cc.run_id = cp_a.run_id AND cc.column_name = cp_a.column_name AND cp_a.side = 'A'
  LEFT JOIN dev.test_main_profiler.column_profiles cp_b
    ON cc.run_id = cp_b.run_id AND cc.column_name = cp_b.column_name AND cp_b.side = 'B'
  LEFT JOIN (
    SELECT run_id, column_name, COUNT(*) AS alert_count
    FROM dev.test_main_profiler.column_alerts
    WHERE run_id = {{run_id_filter}}
    GROUP BY run_id, column_name
  ) al ON cc.run_id = al.run_id AND cc.column_name = al.column_name
where cc.run_id = {{run_id_filter}}.

Display these columns (in this order):
  cc.column_name,
  cp_a.logical_type AS data_type,
  cc.schema_change,
  cc.verdict,
  ROUND(cc.psi, 4) AS psi,
  ROUND(cc.ks_stat, 4) AS ks_stat,
  ROUND(cc.js_divergence, 4) AS js_divergence,
  ROUND(cc.chi_square, 2) AS chi_square,
  ROUND(cp_a.null_pct * 100, 1) AS null_pct_a,
  ROUND(cp_b.null_pct * 100, 1) AS null_pct_b,
  ROUND(cp_a.distinct_pct * 100, 1) AS distinct_pct_a,
  ROUND(cp_b.distinct_pct * 100, 1) AS distinct_pct_b,
  ROUND(cp_a.numeric_mean, 4) AS mean_a,
  ROUND(cp_b.numeric_mean, 4) AS mean_b,
  ROUND(cp_a.numeric_p50, 4) AS median_a,
  ROUND(cp_b.numeric_p50, 4) AS median_b,
  COALESCE(al.alert_count, 0) AS alerts.

Sort by: psi DESC NULLS LAST, cc.schema_change ASC.

Apply row colour coding:
  - Red background: verdict = 'significant'
  - Amber background: verdict = 'moderate'
  - Light green background: verdict = 'stable' AND schema_change = 'unchanged'
  - Grey background: schema_change != 'unchanged'

Title it "Column-Level A vs B Comparison — This Run".
```

### Row 4 — Alert Detail + PSI Ranking

**Widget 11 — All Alerts This Run**
```
Show a table of all rows from dev.test_main_profiler.column_alerts
where run_id = {{run_id_filter}}.
Display columns: side, column_name, rule, severity, message.
Sort by: severity (critical first — use CASE WHEN severity='critical' THEN 1
                              WHEN severity='warn' THEN 2 ELSE 3 END),
         then column_name ASC.
Apply colour to severity column: critical=red, warn=amber, info=blue.
Title it "All Alerts — This Run".
```

**Widget 12 — PSI Ranking This Run**
```
Show a horizontal bar chart of ROUND(cc.psi, 4) per cc.column_name
from dev.test_main_profiler.column_comparisons cc
where cc.run_id = {{run_id_filter}}
and cc.psi IS NOT NULL
and cc.schema_change = 'unchanged'.
Sort descending by psi. Show all rows (no limit — there are typically 20–60 columns).
Colour bars: red if psi >= 0.20, amber if psi >= 0.10 and < 0.20, green if psi < 0.10.
Add reference lines at x=0.10 labelled "Moderate" and x=0.20 labelled "Significant".
Title it "PSI Ranking — All Columns — This Run".
```

**Widget 13 — Numeric Mean Comparison**
```
Show a paired horizontal bar chart comparing numeric_mean for Side A vs Side B
for the top 20 columns by largest absolute difference in numeric_mean.

From dev.test_main_profiler.column_profiles cp_a (where cp_a.side = 'A')
joined to dev.test_main_profiler.column_profiles cp_b
  on cp_a.run_id = cp_b.run_id AND cp_a.column_name = cp_b.column_name
  and cp_b.side = 'B'
where cp_a.run_id = {{run_id_filter}}
and cp_a.numeric_mean IS NOT NULL
and cp_b.numeric_mean IS NOT NULL.

Compute: abs(cp_a.numeric_mean - cp_b.numeric_mean) AS mean_delta.
Sort by mean_delta descending. Show top 20 rows.
Plot Side A bar (Synaptiq blue #8BA4BD) and Side B bar (Synaptiq amber #C8956A)
side by side per column. Round means to 4 decimal places.
Title it "Numeric Mean: Side A vs Side B (top 20 largest shift)".
```

---

## Build Tips

### Prompt Checklist (apply to each widget before deploying)
- [x] Full table path used (`dev.test_main_profiler.*`)
- [x] Exact column names used (no aliases unless computed)
- [x] Aggregation logic is explicit (distinct count, average, count of rows)
- [x] Null/zero handling stated (IS NOT NULL, schema_change = 'unchanged')
- [x] Formatting specified (round 4 decimal, * 100 for percentages, thousands separator)
- [x] Sort and limit are clear (top N, DESC NULLS LAST)
- [x] Join keys are explicit (on run_id, on run_id AND column_name AND side)
- [x] No ambiguous pronouns (all tables/columns fully named)

### Colour Palette (use consistently)
| Meaning | Hex | Use |
|---|---|---|
| Significant drift / Critical alert | `#e74c3c` | Red |
| Moderate drift / Warn alert | `#f39c12` | Amber |
| Stable / Info | `#27ae60` | Green |
| Info alert | `#3498db` | Blue |
| Schema changed | `#95a5a6` | Grey |
| Side A | `#8BA4BD` | Synaptiq Blue |
| Side B | `#C8956A` | Synaptiq Amber |

### PSI Reference Lines (add to all PSI charts)
- `y = 0.10` → label "Moderate (PSI ≥ 0.10)"
- `y = 0.20` → label "Significant (PSI ≥ 0.20)"

### Common Genie Tips
- For KPI tiles: add "display as a counter/number tile, not a chart"
- For the left nav: enable "Multi-page" in dashboard settings
- If Genie misreads a column name, quote it: `"the column is named exactly \`psi\`"`
- To compare PROD vs TEST in one chart, add `UNION ALL` with a literal side label column
