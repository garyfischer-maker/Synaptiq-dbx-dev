# Genie Prompt Style Guide

Use this during **Gate 3 (Build Prompts)** to ensure all Genie prompts are clear, unambiguous, and ready to deploy.

---

## Prompt Structure

Every Genie prompt should follow this template:

```
Show [visualization type] of [metric] from [table]
[where clause if applicable]
[grouped by / aggregated by dimensions]
titled "[Widget Title]".
[formatting: currency, percentage, rounding]
[sorting / limiting: top N, sort order]
```

### Example

```
Show a donut chart of member count by payer_type 
from dev.prod_main_members.eligibility.
Title it "Enrollment by Payer Type". 
Use distinct person_id count per payer_type.
Sort descending.
```

---

## Specificity Rules

### ✅ DO: Be Explicit

| Rule | Correct | Incorrect |
|------|---------|-----------|
| Full table path | `from dev.prod_main_claims.medical_claim` | `from medical_claim` |
| Exact column names | `charge_amount`, `paid_amount` | `amount`, `cost` |
| Distinct vs. count | `count of distinct claim_id` | `count of claims` |
| Join logic | `joined to dev.prod_main_members.patient on person_id` | `join patient table` |
| Null handling | `where paid_amount > 0`, `exclude null values` | `ignore missing data` |
| Data types | `cast result to decimal` | `numeric result` |

### ✅ DO: Disambiguate Aggregations

Genie can misinterpret whether you want:
- **Per-row aggregation:** "for each claim_id, take max(charge_amount)"
- **Group-level aggregation:** "sum charge_amount per month"
- **Distinct count:** "count of distinct person_id per payer_type"

**Bad prompt:**
```
Show total billed amount from medical_claim as a KPI.
```

**Good prompt:**
```
Show sum of charge_amount from dev.prod_main_claims.medical_claim
where claim_id is not null
formatted as currency (e.g. $12.4M) as a KPI titled "Total Billed".
Compute one row per claim_id using max(charge_amount) to avoid line double-counting.
```

---

## Visualization Type Rules

| Type | Use When | Avoid If |
|------|----------|----------|
| **Counter/KPI** | Single metric for executives | More than 4 KPIs per row (cognitive overload) |
| **Donut** | Parts of a whole; max 5–6 segments | More than 10 categories; temporal trends |
| **Bar (vertical)** | Comparing categories | Time series (use line) |
| **Bar (horizontal)** | Comparing many categories (>5) | Small screen widths (hard to read labels) |
| **Line** | Time series, trends | Comparing disparate categories without temporal meaning |
| **Heatmap** | 2D matrix (time × category) or correlation | Sparse data; >20 categories per axis |
| **Scatter** | Correlation, outlier detection | Sparse data or >10K points (overplotting) |
| **Histogram** | Distribution of continuous values | Categorical data (use bar) |
| **Table** | Precise row-level data, drill-down | More than 8–10 columns (user scrolls horizontally) |

---

## Formatting Rules

### Currency

**Bad:**
```
Show sum of charge_amount as a KPI.
```

**Good:**
```
Show sum of charge_amount formatted as currency (e.g. $12.4M) as a KPI titled "Total Billed".
```

Genie respects common abbreviations: `$M`, `$K`, `$B` for millions, thousands, billions.

### Percentage

**Bad:**
```
Show denial rate as a percentage.
```

**Good:**
```
Show the percentage of claim lines where paid_amount = 0 or paid_amount is null
formatted as a percentage (e.g. 18.5%) as a KPI titled "Denial Rate".
```

Include the definition in parentheses. Genie may not infer the denominator correctly.

### Rounding & Decimals

**Bad:**
```
Show average enrollment duration in months.
```

**Good:**
```
Show average number of months between enrollment_start_date and enrollment_end_date
rounded to 1 decimal place as a KPI titled "Avg Enrollment Months".
```

Explicit rounding avoids floating-point surprises.

### Dates

**Bad:**
```
Show monthly trend starting from last year.
```

**Good:**
```
Show a line chart using claim_start_date truncated to month on the x-axis.
Title it "Monthly Billed vs Paid". Only include 2024 onwards.
```

Specify the exact date column and granularity. Avoid relative dates ("last year") unless Genie is context-aware about current_date().

---

## Common Pitfalls & Fixes

### Double-Counting in Line Items

**Problem:** Claims table has one row per claim **line item**, so summing charge_amount counts each line, not each claim.

**Fix:** Explicitly deduplicate.

```
Bad:
  Show sum of charge_amount from dev.prod_main_claims.medical_claim.

Good:
  Show sum of charge_amount from dev.prod_main_claims.medical_claim
  where one row per claim_id using max(charge_amount).
```

### Null Confusion

**Problem:** Genie treats `NULL` and `0` differently; prompt must be clear.

**Fix:** Specify null handling.

```
Bad:
  Show paid amount from claims.

Good:
  Show sum of paid_amount from dev.prod_main_claims.medical_claim
  where paid_amount > 0 (exclude nulls and $0 denials).
```

### Join Ambiguity

**Problem:** When two tables share multiple keys, Genie may guess the wrong join.

**Fix:** Be explicit.

```
Bad:
  Show member data from eligibility and patient.

Good:
  Using dev.prod_main_members.eligibility joined to dev.prod_main_members.patient
  on person_id, show member count by age band.
```

### Grouping Confusion

**Problem:** "top 10" without aggregation direction is ambiguous.

**Fix:** Specify aggregation & sort.

```
Bad:
  Show top 10 diagnoses.

Good:
  Show a horizontal bar chart of the top 10 diagnosis_code_1 values
  by count of distinct claim_id from dev.prod_main_claims.medical_claim.
  Title it "Top 10 Diagnoses by Claim Volume". Sort descending.
```

---

## Prompt Checklist

For each widget, before finalizing:

- [ ] **Visualization type** is specified (e.g., "donut chart", "KPI", "line chart")
- [ ] **Full table path** is included (e.g., `dev.prod_main_claims.medical_claim`)
- [ ] **Metric definition** is unambiguous (e.g., "distinct person_id", "sum of charge_amount per claim")
- [ ] **Aggregation logic** is explicit (e.g., "count distinct", "max per claim_id", "sum per month")
- [ ] **Null/zero handling** is clear (e.g., "exclude nulls", "where paid_amount > 0")
- [ ] **Grouping dimensions** are exact column names
- [ ] **Filtering** is explicit (e.g., "where enrollment_end_date is null or > current_date()")
- [ ] **Formatting** is specified (currency, percentage, decimal places, date format)
- [ ] **Sorting & limiting** are clear (e.g., "top 15, sort descending")
- [ ] **Title** matches the widget's purpose
- [ ] **Data types** are cast if needed (e.g., "cast result to decimal")
- [ ] **No ambiguous pronouns** (e.g., no "it"; always use table/column names)

---

## Testing in Genie

1. **Paste the prompt** into a new widget in Databricks.
2. **Run Genie** (it will auto-generate a SQL query).
3. **Check the generated query**:
   - Does it use the right table?
   - Are column names correct?
   - Is the aggregation what you intended?
   - Does it respect the where/group by clause?
4. **If wrong**, edit the prompt to be more explicit, then re-run.
5. **Document fixes** in the prompt for the team.

---

## Example Progression

### Iteration 1 (Too Vague)
```
Show member demographics.
```
Genie: ❌ Returns a generic query; unclear what metrics or charts you want.

### Iteration 2 (Better, Still Ambiguous)
```
Show member count by payer type as a chart.
```
Genie: ✓ Returns a bar chart, but Genie picks "person_id count" instead of "distinct person_id". 
Result: Over-counts if one person_id appears in multiple rows.

### Iteration 3 (Clear & Specific)
```
Show a donut chart of member count by payer_type 
from dev.prod_main_members.eligibility.
Title it "Enrollment by Payer Type". 
Use distinct person_id count per payer_type.
```
Genie: ✅ Returns the correct donut chart with accurate counts.

---

## Tips for Different Personas

### For Analysts (Power Users)
Include the exact SQL logic in the prompt so they can verify:
```
"Compute average charge_amount per distinct claim_id, then group by claim_type."
```

### For Business Users (Less Technical)
Use plain English and business terms:
```
"Show average billed amount (per claim) by the type of medical service."
```

### For Data Engineers (Maintainers)
Include data quality notes:
```
"Note: Table refreshes daily at 2 AM. Null diagnosis_code_1 values represent 3% of claims; exclude them."
```

