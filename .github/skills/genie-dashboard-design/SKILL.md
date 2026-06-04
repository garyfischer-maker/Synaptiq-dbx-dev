---
name: genie-dashboard-design
description: 'Design and build Databricks AI/BI Genie dashboards through three gates: Plan (context & architecture), Design Review (mockup & approval), Build Prompts (Genie queries). Use when creating new healthcare analytics dashboards, migrating existing dashboards, or iterating on dashboard layouts.'
argument-hint: 'Describe your dashboard vision (e.g., "Members & claims dashboard for healthcare analytics")'
---

# Genie Dashboard Design Workflow

A three-gate workflow for designing and building Databricks AI/BI Genie dashboards. Each gate ensures alignment before moving forward.

---

## Overview

| Gate | Deliverable | Decision |
|------|-------------|----------|
| **1. Plan** | Context analysis, dashboard architecture (pages, sections, layout, style) | Proceed to design? |
| **2. Design Review** | Wireframe/mockup, design decisions, global filters | Approve design? |
| **3. Build Prompts** | Databricks AI/BI Genie prompts & instructions, ready to deploy | Deploy dashboard |

---

## Gate 1: Planning & Context Analysis

**Goal:** Gather requirements, understand data sources, and propose dashboard architecture.

### Step 1.1 — Gather Context

I will ask you for:
- **Dashboard purpose**: What business problem does it solve?
- **Target audience**: Who uses this? (analysts, clinicians, operations, finance, etc.)
- **Data sources**: Schemas, tables, join keys (e.g., `dev.prod_main_members.eligibility`)
- **Existing references**: Any dashboards to model after, style guides, brand colors
- **KPIs & metrics**: Primary metrics to track (e.g., total members, denial rates, claims volume)
- **Filters & drill-downs**: Global filters (date range, member cohort, payer type) and page-level drill-downs

### Step 1.2 — Analyze & Design

Based on your input, I will propose:
- **Number of pages** and logical grouping (e.g., Members | Claims | Conditions)
- **Sections per page** (KPI row, trends, distributions, drill-down tables)
- **Global filter architecture** (time period, geography, cohort, etc.)
- **Layout style**: top nav, left sidebar, full-width cards
- **Visual hierarchy**: Counter KPIs, line/bar charts, heatmaps, tables
- **Responsive design notes**: Mobile/tablet considerations
- **Integration points**: Any external data or manual lookups

### Step 1.3 — Communicate Plan

I will present:
1. **Executive summary** (1–2 paragraphs on the dashboard's purpose and structure)
2. **Page layout** (list of pages with 2–3 sentences per page)
3. **Widget inventory** (table: page, row, widget type, metric, data source)
4. **Global filters** (list with expected cardinality and default values)
5. **Design reference** (layout pattern, color palette if applicable)
6. **Data quality notes** (any null/missing data handling, data source reliability)

### Step 1.4 — Get Approval to Proceed

Once you review the plan, confirm **"Proceed to Gate 2"** or request changes.

---

## Gate 2: Design Review & Approval

**Goal:** Validate layout, filters, and design decisions via wireframe before building prompts.

### Step 2.1 — Communicate Mockup

I will provide:
- **ASCII wireframe** (quick layout of each page showing widget positions and types)
- **Design rationale** (why KPIs are grouped this way, why specific chart types)
- **Filter flow** (how global filters cascade through pages)
- **Navigation pattern** (how users move between pages)
- **Accessibility notes** (color contrast, alt text, keyboard nav considerations)

### Step 2.2 — Design Decisions

I will highlight:
- **Layout choices**: Why left nav vs. top nav, why full-width vs. grid
- **Metric aggregation**: How double-counting is avoided (e.g., max per claim_id for charge amounts)
- **Time series granularity**: Month vs. week vs. day
- **Drill-down strategy**: Which widgets drill into which detail pages
- **Data source selection**: Why specific tables (e.g., `medical_claim` vs. view)

### Step 2.3 — Request Approval

Confirm **"Design approved, proceed to Gate 3"** or request revisions (e.g., "swap the bar chart for a heatmap", "add a KPI for denial rate", "move filters here").

---

## Gate 3: Build Prompts & Deploy

**Goal:** Generate Databricks AI/BI Genie prompts and setup instructions.

### Step 3.1 — Generate Prompts

For each widget, I will create a Genie prompt:
```
Show [metric] from [table] where [filters]
grouped by [dimension] as a [chart type]
titled "[Widget Title]".
[formatting: currency, percentage, rounding, sorting]
```

Prompts include:
- **Exact table paths** and join logic
- **Field names** matching your schema exactly
- **Aggregation logic** (distinct counts, sums, averages, percentiles)
- **Null/zero handling** (exclude nulls, interpret 0 as paid, etc.)
- **Formatting** (currency $M, percentage %, decimals, date format)

### Step 3.2 — Setup Instructions

I will provide:
- **Prerequisites** (table existence, permissions, warehouse assignment)
- **Step-by-step dashboard creation** (create pages, add widgets, paste prompts)
- **Testing checklist** (verify each widget loads, check data)
- **Troubleshooting guide** (common errors and fixes)

### Step 3.3 — Deliver Artifacts

Final deliverables:
- **Prompt catalog** (organized by page and widget)
- **Dashboard setup guide** (copy-paste instructions)
- **Metadata file** (tables, columns, data quality notes)
- **Change log** (what's new vs. any prior versions)

---

## Example Workflow

**Your request:**
> "Create a healthcare dashboard for members and claims. Users are data analysts. Data is in Tuva input layer tables."

### Gate 1 Output (Plan)
```
Pages:
  1. Members Overview
     - KPI: total, active, avg duration, employer groups
     - Distribution: payer mix, age bands, gender, race
     - Geography: top states, enrollment trend
  
  2. Claims & Utilization
     - KPI: total claims, billed, paid, denial rate
     - Trends: monthly billed vs paid
     - Top diagnoses (by volume, by paid amount)
  
Global Filters: date range, payer type, state
```

### Gate 2 Output (Design)
```
Members page wireframe:

  ┌─────────────────────────────────────────┐
  │ TOTAL MEMBERS    ACTIVE    AVG MONTHS   │
  │    2.1M           1.8M        18.5       │
  └─────────────────────────────────────────┘
  
  ┌──────────────────────┬──────────────────┐
  │ Payer Mix (donut)    │ Age Bands (bar)  │
  │                      │                  │
  └──────────────────────┴──────────────────┘
  
  ┌─────────────────────────────────────────┐
  │ Members by State (bar, scrollable)      │
  └─────────────────────────────────────────┘
```

### Gate 3 Output (Prompts)
```
Widget: Members by Payer Type

Prompt:
  Show a donut chart of member count by payer_type 
  from dev.tuva_input_layer.eligibility.
  Title it "Enrollment by Payer Type". 
  Use distinct person_id count per payer_type.
```

---

## Reference Materials

See [existing Genie prompts](./references/genie-dashboard-prompts.md) for a completed example dashboard (Members, Claims & Charges, Clinical Conditions) based on Tuva synthetic data.

---

## Key Principles

1. **Clarity first**: Every prompt must be unambiguous. Include exact table paths, join logic, and data types.
2. **Avoid double-counting**: Specify whether aggregating by distinct ID, max per claim, or distinct dates.
3. **Metric definitions are explicit**: "Denial rate = (paid = 0 or null) / total claims", not just "% denied".
4. **Design for the user**: KPIs first (executives glance), drill-downs second (analysts dig deep).
5. **Backwards compatible**: If iterating on an existing dashboard, clearly mark what's new/changed.

---

## Getting Started

1. **Invoke this skill**: Type `/genie-dashboard-design` and describe your dashboard vision.
2. **Complete Gate 1**: Share context; I'll propose a plan.
3. **Review & approve**: Confirm the plan, or request changes.
4. **Gate 2**: I'll share a wireframe and design rationale.
5. **Gate 3**: I'll generate Genie prompts ready to paste into Databricks.

---

## Common Customizations

- **Add a row for drill-down tables**: Include in wireframe (Gate 2) before prompts (Gate 3).
- **Global filters that cascade**: Define in Gate 1; specify filter logic per widget in Gate 3.
- **Multiple data sources (e.g., TEST vs PROD)**: Note in Gate 1; use `switch` logic in prompts if needed.
- **Scheduled refreshes & alerts**: Defer to post-deployment; mention in Gate 3 if desired.
