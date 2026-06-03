# Databricks AI/BI Genie Dashboard — DQ Governance Analytics

Source catalog: `dev.test_main_profiler`  
Tables: `profiler_runs`, `dataset_profiles`, `column_profiles`, `column_alerts`, `column_comparisons`

Style reference: column-level profiling cards (valid/mismatched/empty counts + histograms),
KPI tile row + trend line + donut breakdown, schema table with size/column metadata.

---

## Setup Instructions

1. Databricks workspace → **SQL** → **Dashboards** → **Create AI/BI Dashboard**
2. Name: **Synaptiq DQ Governance Analytics**
3. Connect data source to the `dev` catalog
4. Create **3 pages** in the left nav:
   - `Schema Drift`
   - `Column Drift`
   - `Run Comparison`
5. For each page, enter the Genie prompts below — one prompt per widget
6. Add **Global Filters** (instructions at end of this document)

---

## Global Filters (Dashboard-level, apply to all pages)

Configure these as dashboard-level filter widgets before building pages:

```
Filter 1 — Catalog
  Parameter name: catalog_filter
  Widget: Dropdown, single-select
  Query: SELECT DISTINCT split(side_a_fqn, '\\.')[0] AS catalog
         FROM dev.test_main_profiler.profiler_runs ORDER BY 1
  Default: All

Filter 2 — Schema (Side A)
  Parameter name: schema_filter
  Widget: Dropdown, multi-select
  Query: SELECT DISTINCT split(side_a_fqn, '\\.')[1] AS schema_name
         FROM dev.test_main_profiler.profiler_runs ORDER BY 1
  Default: All

Filter 3 — Table Name
  Parameter name: table_filter
  Widget: Dropdown, multi-select
  Query: SELECT DISTINCT split(side_a_fqn, '\\.')[2] AS table_name
         FROM dev.test_main_profiler.profiler_runs ORDER BY 1
  Default: All

Filter 4 — Load Date Range
  Parameter name: load_date_from, load_date_to
  Widget: Date range picker
  Source: dev.test_main_profiler.profiler_runs.created_date
  Default: Last 30 days

Filter 5 — Run ID
  Parameter name: run_id_filter
  Widget: Dropdown, single-select, searchable
  Query: SELECT run_id || ' — ' || side_a_fqn || ' vs ' ||
                split(side_b_fqn, '\\.')[1] AS label, run_id
         FROM dev.test_main_profiler.profiler_runs
         ORDER BY created_utc DESC LIMIT 100
  Default: (most recent)
```

---

## Page 1 — Schema Drift

*Goal: table-level view of schema stability across profiler runs.
Style: KPI row + trend line (like slide 2) + horizontal bars for worst tables.*

---

### KPI Row (4 counter tiles)

**Prompt 1 — Total runs**
```
Show count of distinct run_id from dev.test_main_profiler.profiler_runs
as a KPI counter titled "Total Profile Runs".
Apply filter: created_date BETWEEN {{load_date_from}} AND {{load_date_to}}.
```

**Prompt 2 — Total schema changes detected**
```
Show total count of rows from dev.test_main_profiler.column_comparisons
where schema_change != 'unchanged'
as a KPI counter titled "Schema Changes Detected".
Apply run_id filter from profiler_runs where created_date is in the selected date range.
```

**Prompt 3 — Tables with any schema change**
```
Show count of distinct concat(catalog_a, '.', schema_a, '.', table_a)
from dev.test_main_profiler.column_comparisons
where schema_change != 'unchanged'
as a KPI counter titled "Tables with Schema Changes".
```

**Prompt 4 — Most common change type**
```
Show the schema_change value with the highest count
from dev.test_main_profiler.column_comparisons
where schema_change != 'unchanged'
as a KPI counter titled "Most Common Change".
```

---

### Row 2 — Schema change trend + breakdown

**Prompt 5 — Schema changes by run date (line chart)**
```
Show a line chart of schema change count per day from
dev.test_main_profiler.column_comparisons joined to dev.test_main_profiler.profiler_runs
on run_id, using created_date on the x-axis.
Group by created_date and schema_change type. Use separate lines per change type:
added (green), removed (red), type_changed (orange), nullability_changed (yellow).
Title it "Schema Changes Over Time".
Only include changes where schema_change != 'unchanged'.
```

**Prompt 6 — Change type breakdown (donut)**
```
Show a donut chart of count of schema changes by schema_change type
from dev.test_main_profiler.column_comparisons
where schema_change != 'unchanged'.
Title it "Change Type Distribution".
Colour map: added=green, removed=red, type_changed=orange, nullability_changed=yellow.
```

**Prompt 7 — Schema change counts by table (horizontal bar)**
```
Show a horizontal bar chart of the top 15 tables by total schema change count
from dev.test_main_profiler.column_comparisons
where schema_change != 'unchanged'.
Construct the table name as concat(catalog_a, '.', schema_a, '.', table_a).
Sort descending by count. Title it "Most Schema Changes by Table".
```

---

### Row 3 — Schema change detail table

**Prompt 8 — Schema change log table**
```
Show a table with columns: run_id (first 8 chars), table_a, column_name,
schema_change, verdict, created_date
from dev.test_main_profiler.column_comparisons
joined to dev.test_main_profiler.profiler_runs on run_id
where schema_change != 'unchanged'
order by created_date desc, schema_change.
Apply all global filters (catalog, schema, table, date range).
Title it "Schema Change Log".
Limit 200 rows.
```

**Prompt 9 — Columns added vs removed per run (stacked bar)**
```
Show a stacked bar chart of added column count and removed column count per run
from dev.test_main_profiler.column_comparisons
joined to dev.test_main_profiler.profiler_runs on run_id.
Use created_date on the x-axis. Stack: added (green), removed (red).
Title it "Columns Added vs Removed Per Run".
```

---

## Page 2 — Column Drift

*Goal: column-level drift metrics — PSI, KS, verdicts, alerts.
Style: per-column profiling cards (slide 1 style) + alert heatmap + ranked tables.*

---

### KPI Row

**Prompt 1 — Total columns compared**
```
Show count of distinct column_name from dev.test_main_profiler.column_comparisons
where schema_change = 'unchanged'
as a KPI titled "Columns Compared".
Apply date range filter via profiler_runs join.
```

**Prompt 2 — Columns with significant drift (PSI ≥ 0.2)**
```
Show count of rows from dev.test_main_profiler.column_comparisons
where verdict = 'significant'
as a KPI titled "Significant Drift" with red colouring.
Apply date range filter.
```

**Prompt 3 — Columns with moderate drift (PSI 0.1–0.2)**
```
Show count of rows from dev.test_main_profiler.column_comparisons
where verdict = 'moderate'
as a KPI titled "Moderate Drift" with amber colouring.
```

**Prompt 4 — Total alerts fired**
```
Show count of rows from dev.test_main_profiler.column_alerts
joined to dev.test_main_profiler.profiler_runs on run_id
where created_date is in the selected date range
as a KPI titled "Total Alerts".
```

---

### Row 2 — Drift verdict distribution + PSI trend

**Prompt 5 — Verdict distribution (donut)**
```
Show a donut chart of column count by verdict
from dev.test_main_profiler.column_comparisons
where schema_change = 'unchanged' and verdict is not null.
Colour map: stable=green, moderate=amber, significant=red, schema_change=grey.
Title it "Drift Verdict Distribution".
```

**Prompt 6 — Average PSI trend over time (line chart)**
```
Show a line chart of average PSI per run date
from dev.test_main_profiler.column_comparisons
joined to dev.test_main_profiler.profiler_runs on run_id.
Use created_date on x-axis. Title it "Avg PSI Trend Over Time".
Only include rows where psi is not null.
Add a reference line at PSI = 0.1 (moderate threshold) and 0.2 (significant threshold).
```

**Prompt 7 — Alert severity breakdown (donut)**
```
Show a donut chart of alert count by severity (critical, warn, info)
from dev.test_main_profiler.column_alerts
joined to dev.test_main_profiler.profiler_runs on run_id
where created_date is in the selected date range.
Colour: critical=red, warn=amber, info=blue.
Title it "Alert Severity Distribution".
```

---

### Row 3 — Top drifted columns + alert heatmap

**Prompt 8 — Top 20 columns by PSI (horizontal bar)**
```
Show a horizontal bar chart of the top 20 column_name values by average PSI
from dev.test_main_profiler.column_comparisons
where verdict in ('moderate', 'significant') and psi is not null.
Colour bars red for significant (avg PSI >= 0.2), amber for moderate.
Sort descending. Title it "Most Drifted Columns (avg PSI)".
```

**Prompt 9 — Alert count by column and rule (heatmap table)**
```
Show a table of alert counts grouped by column_name and rule
from dev.test_main_profiler.column_alerts
joined to dev.test_main_profiler.profiler_runs on run_id
where created_date is in the selected date range.
Show columns: column_name, rule, severity, count.
Sort by count descending, limit 30.
Title it "Alert Heatmap by Column & Rule".
```

---

### Row 4 — Column profile detail (like slide 1 style)

**Prompt 10 — Null% comparison A vs B (grouped bar)**
```
Show a grouped bar chart comparing null_pct for Side A vs Side B
for the top 20 columns by highest null_pct difference.
Join dev.test_main_profiler.column_profiles on run_id and column_name,
filtering side = 'A' for Side A values and side = 'B' for Side B.
Compute abs(null_pct_a - null_pct_b) as the sort key.
Title it "Null % Change: Side A vs Side B".
Apply all global filters.
```

**Prompt 11 — Distinct % change (scatter plot)**
```
Show a scatter plot of columns with distinct_pct on x-axis (Side A)
and distinct_pct on y-axis (Side B), coloured by verdict from column_comparisons.
Colour: stable=grey, moderate=amber, significant=red.
Point label = column_name. Title it "Distinct % : Side A vs Side B".
Apply run_id and table filters.
```

**Prompt 12 — KS statistic by column (horizontal bar, numeric cols)**
```
Show a horizontal bar chart of the top 15 column_name values by ks_stat
from dev.test_main_profiler.column_comparisons
where ks_stat is not null.
Sort descending. Add a reference line at ks_stat = 0.05 (significance threshold).
Title it "KS Statistic by Column (numeric)".
```

---

## Page 3 — Run Comparison

*Goal: user selects one specific run_id and sees full A vs B side-by-side.
Style: slide 2 KPI row + per-column detail table matching slide 1.*

---

### KPI Row (driven by selected run_id)

**Prompt 1 — Side A row count**
```
Show row_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'A'
as a KPI titled "Side A — Rows".
```

**Prompt 2 — Side B row count**
```
Show row_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'B'
as a KPI titled "Side B — Rows".
```

**Prompt 3 — Column count (Side A)**
```
Show column_count from dev.test_main_profiler.dataset_profiles
where run_id = {{run_id_filter}} and side = 'A'
as a KPI titled "Side A — Columns".
```

**Prompt 4 — Drifted columns**
```
Show count of column_name from dev.test_main_profiler.column_comparisons
where run_id = {{run_id_filter}} and verdict in ('moderate','significant')
as a KPI titled "Drifted Columns" with red colouring when > 0.
```

**Prompt 5 — Schema changes in this run**
```
Show count of column_name from dev.test_main_profiler.column_comparisons
where run_id = {{run_id_filter}} and schema_change != 'unchanged'
as a KPI titled "Schema Changes".
```

**Prompt 6 — Total alerts this run**
```
Show count of rows from dev.test_main_profiler.column_alerts
where run_id = {{run_id_filter}}
as a KPI titled "Alerts Fired".
```

---

### Row 2 — Run metadata + verdict summary

**Prompt 7 — Run metadata card**
```
Show a table with these fields for the selected run:
run_id, run_label, created_utc, side_a_fqn, side_b_fqn
from dev.test_main_profiler.profiler_runs
where run_id = {{run_id_filter}}.
Title it "Run Details".
```

**Prompt 8 — Drift verdict donut for this run**
```
Show a donut chart of column count by verdict
from dev.test_main_profiler.column_comparisons
where run_id = {{run_id_filter}} and schema_change = 'unchanged'.
Colour: stable=green, moderate=amber, significant=red.
Title it "Verdict Breakdown — This Run".
```

**Prompt 9 — Alert severity donut for this run**
```
Show a donut chart of alert count by severity
from dev.test_main_profiler.column_alerts
where run_id = {{run_id_filter}}.
Colour: critical=red, warn=amber, info=blue.
Title it "Alert Severity — This Run".
```

---

### Row 3 — Per-column comparison table (main detail view)

**Prompt 10 — Full column comparison table (slide 1 style)**
```
Show a detailed table for all columns in the selected run.
Join dev.test_main_profiler.column_comparisons (cc)
with dev.test_main_profiler.column_profiles on run_id and column_name,
using side = 'A' for Side A stats and side = 'B' for Side B stats.

Display columns:
  column_name, logical_type (from Side A),
  schema_change, verdict,
  psi (round 4 decimals), ks_stat (round 4), js_divergence (round 4), chi_square (round 2),
  null_pct_a (Side A null_pct * 100 formatted as %), null_pct_b (Side B null_pct * 100 as %),
  distinct_pct_a (Side A distinct_pct * 100 as %), distinct_pct_b (Side B),
  numeric_mean_a (Side A numeric_mean), numeric_mean_b (Side B),
  numeric_p50_a, numeric_p50_b,
  alert_count (count of alerts for this column in column_alerts)

Filter: run_id = {{run_id_filter}}
Sort: psi desc nulls last, verdict asc
Highlight rows: red background for verdict='significant',
               amber for verdict='moderate',
               light green for verdict='stable',
               grey for schema_change != 'unchanged'

Title it "Column-Level A vs B Comparison".
```

---

### Row 4 — Alerts for this run + numeric mean comparison

**Prompt 11 — Alert detail table for this run**
```
Show a table of all alerts for the selected run from dev.test_main_profiler.column_alerts
where run_id = {{run_id_filter}}.
Display: side, column_name, rule, severity, message.
Colour severity: critical=red, warn=amber, info=blue.
Sort by severity (critical first), then column_name.
Title it "All Alerts — This Run".
```

**Prompt 12 — Numeric mean comparison (horizontal paired bar)**
```
Show a paired horizontal bar chart comparing numeric_mean for Side A vs Side B
for all numeric columns in the selected run.
Join dev.test_main_profiler.column_profiles where run_id = {{run_id_filter}}
and numeric_mean is not null.
Plot Side A mean (blue bar) and Side B mean (orange bar) side by side per column.
Sort by abs(mean_a - mean_b) descending, top 20.
Title it "Numeric Mean: Side A vs Side B".
```

**Prompt 13 — PSI ranking for this run (horizontal bar)**
```
Show a horizontal bar chart of PSI values per column for the selected run.
From dev.test_main_profiler.column_comparisons
where run_id = {{run_id_filter}} and psi is not null.
Colour bars: red if psi >= 0.2, amber if psi >= 0.1, green if psi < 0.1.
Add reference lines at 0.1 and 0.2.
Sort descending. Title it "PSI by Column — This Run".
```

---

## Dashboard Build Tips

- **Run ID selector (Page 3)**: Use the global `run_id_filter` dropdown as the primary control.
  Build it as: `run_id || ' | ' || DATE(created_utc) || ' | ' || split(side_a_fqn,'.')[2] || ' vs ' || split(side_b_fqn,'.')[2]`
  so users can identify runs at a glance.

- **Colour coding consistency**: Use these colours throughout all pages:
  - `significant` drift / `critical` alert → red `#e74c3c`
  - `moderate` drift / `warn` alert → amber `#f39c12`
  - `stable` / `info` → green `#27ae60` / blue `#3498db`
  - `schema_change` → grey `#95a5a6`

- **PSI reference lines**: Always add reference lines at 0.1 and 0.2 on PSI charts —
  label them "Moderate threshold" and "Significant threshold".

- **For KPI tiles**: Tell Genie "display as a counter/number tile, not a chart".

- **For the left navigation sidebar**: Enable "Multi-page" in dashboard settings.
  Databricks renders the page list as a left nav automatically.

- **Column profile cards (slide 1 style)**: Genie can approximate this as a wide table
  with conditional formatting. Ask for "a table with colour-coded cells where null_pct > 50%
  shows red background, distinct_pct = 100% shows blue, and numeric stats in grey."
