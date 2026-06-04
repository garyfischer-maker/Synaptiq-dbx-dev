# Dashboard Design Template

Use this template during **Gate 2 (Design Review)** to structure your wireframe and design decisions.

---

## Design Checklist

Before requesting user approval, confirm:

- [ ] **Page count & names** are clearly listed
- [ ] **Sections per page** are organized logically (KPI row, then drill-down rows)
- [ ] **Widget count** is reasonable (<12 per page to avoid scrolling fatigue)
- [ ] **Chart types** match the metric (time series → line, comparison → bar, parts of whole → donut)
- [ ] **Global filters** are defined and their impact on each widget is documented
- [ ] **Data sources** are verified (tables exist, fields exist, join logic is clear)
- [ ] **No double-counting** in aggregations (e.g., max per claim, distinct person_id)
- [ ] **Formatting rules** are specified (currency, percentage, decimal places, date format)
- [ ] **Accessibility** is considered (color contrast, alt text, keyboard nav)
- [ ] **Mobile/responsive** layout is noted (if applicable)

---

## ASCII Wireframe Template

### Page: [Page Name]

Purpose: [1 sentence on what analysts do here]

```
┌─────────────────────────────────────────────────────────────────┐
│                    GLOBAL FILTERS (sticky)                      │
│  Date Range: [Jan 1 - Now] | Payer: [Dropdown] | State: [...]  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ KPI ROW (Counter tiles)                                         │
├─────────┬─────────┬──────────┬──────────┐
│ Metric1 │ Metric2 │ Metric3  │ Metric4  │
│ 2.1M    │ 1.8M    │ 18.5 mo  │ 145      │
├─────────┴─────────┴──────────┴──────────┤
│ [subtitle, units, trend indicator]      │
└─────────────────────────────────────────┘

┌────────────────────────┬────────────────────────┐
│ Chart 1 (Donut)        │ Chart 2 (Bar)          │
│ Payer Mix              │ Age Distribution       │
│ [Visual placeholder]   │ [Visual placeholder]   │
└────────────────────────┴────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Chart 3 (Line)                                                  │
│ Enrollment Trend Over Time                                      │
│ [Visual placeholder]                                            │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ Chart 4 (Table, scrollable)                                     │
│ Top 15 States by Member Count                                  │
│ [Columns: Rank, State, Count, % Total]                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Design Decision Template

For each major choice, document **Why** and **Trade-offs**:

### Example: Why KPIs First?

**Decision:** Place counter KPIs at the top of every page.

**Why:**
- Executives and analysts scan the top first (F-pattern eye movement)
- "At a glance" answers for busy stakeholders
- Establishes context (e.g., "2.1M total members in view")

**Trade-offs:**
- Takes up vertical real estate (one row ≈ 80–120px)
- If only 3–4 KPIs, consider arranging as 2x2 grid vs. 1x4 row

**Alternative considered:** Place KPIs in a sidebar. *Rejected because:* narrows main content area; sidebar space is precious; single-column layout is more responsive.

---

## Global Filter Design

Document how filters propagate and which widgets they affect.

### Example: Date Range Filter

| Filter | Type | Default | Affects | Notes |
|--------|------|---------|---------|-------|
| Date Range | Date picker (startDate, endDate) | Last 12 months | All time-series charts, KPIs | Granularity: day level; wildcard matching on claims start_date |
| Payer Type | Multi-select dropdown | Commercial, Medicaid | Members page (donut, age bar); Claims page (all) | Case-insensitive; excludes null |
| State | Single-select or multi-select | (none) | Members page geography; Claims page top diagnoses | Only applies to enrollment table |

---

## Data Source Verification

Before finalizing design, confirm:

| Table | Schema | Purpose | Join Key | Row Count (approx.) |
|-------|--------|---------|----------|-------------------|
| eligibility | dev.prod_main_members | Members & enrollment | person_id, group_id | 2.1M members, 4.5M enrollments |
| patient | dev.prod_main_members | Demographics (age, gender, race) | person_id | 2.1M persons |
| medical_claim | dev.prod_main_claims | Claims & charges | claim_id, person_id | 18.5M claim lines |
| encounter | dev.prod_main_clinical | Clinical visits | encounter_id, person_id | 5.2M encounters |

---

## Accessibility & Responsive Design

### Color & Contrast

- Use a colorblind-friendly palette (e.g., avoid red-green combos)
- Contrast ratio ≥ 4.5:1 for text on backgrounds
- Donut charts: label each slice with text, not just color

### Keyboard Navigation

- Tab order: left-to-right, top-to-bottom
- All filters keyboard-accessible
- Charts have alt text (e.g., "Bar chart: Member count by payer type; Commercial 1.2M, Medicaid 0.6M, Medicare 0.3M")

### Mobile / Responsive

- **Desktop (1200px+):** 2–4 widgets per row
- **Tablet (768–1199px):** 1–2 widgets per row
- **Mobile (<768px):** 1 widget per row; stack filters vertically
- Note: Dashboards often aren't mobile-optimized; document this assumption

---

## Example: Completed Design Decision

**Dashboard:** Synaptiq Healthcare Analytics (Members Page)

**Layout rationale:**
1. **Top:** KPI row (4 counters) — executive summary
2. **Middle:** 2-column grid (Payer donut + Age bar) — demographics snapshot
3. **Bottom:** Full-width line chart (enrollment trend) + scrollable state table

**Why this layout?**
- Executives glance at KPIs → drill down to demographics → ask "What states drive growth?" (state table)
- Three distinct analytical questions per section
- Filters at page top apply to all widgets
- Responsive: KPI row stays 1 row on mobile; charts stack vertically

**Data sources:** Two tables (eligibility + patient); one left join on person_id

**Known gaps:** No drill-down to individual member records (defer to next milestone)

