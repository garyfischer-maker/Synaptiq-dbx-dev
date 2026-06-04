# Databricks AI/BI Genie Dashboard Prompts

Three-page dashboard based on the Tuva Input Layer synthetic dataset in `dev` catalog.
Style reference: left sidebar navigation with page tiles (Members | Claims & Charges | Clinical Conditions).

---

## Setup Instructions

1. In Databricks workspace → **SQL** → **Dashboards** → **Create AI/BI Dashboard**
2. Name it: **Synaptiq Healthcare Analytics**
3. Create **3 pages** (left nav) named:
   - `Members`
   - `Claims & Charges`
   - `Clinical Conditions`
4. For each page, enter the Genie prompts below — one prompt per widget.
5. Set **data source** to the `dev` catalog before running prompts.

---

## Page 1 — Members

*Source tables: `dev.prod_main_members.eligibility`, `dev.prod_main_members.patient`*

---

### KPI Row (4 counter tiles)

**Prompt 1 — Total enrolled members**
```
Show total count of distinct person_id values from dev.prod_main_members.eligibility
as a single number KPI titled "Total Members".
```

**Prompt 2 — Active enrollments**
```
Show count of distinct person_id from dev.prod_main_members.eligibility
where enrollment_end_date >= current_date() or enrollment_end_date = '9999-12-31'
as a single number KPI titled "Active Members".
```

**Prompt 3 — Average enrollment duration (months)**
```
Show average number of months between enrollment_start_date and enrollment_end_date
from dev.prod_main_members.eligibility as a single number KPI titled "Avg Enrollment Months".
Round to 1 decimal place.
```

**Prompt 4 — Unique employer groups**
```
Show count of distinct group_id from dev.prod_main_members.eligibility
where group_id is not null as a KPI titled "Employer Groups".
```

---

### Row 2 — Payer mix + Age distribution

**Prompt 5 — Payer type donut chart**
```
Show a donut chart of member count by payer_type from dev.prod_main_members.eligibility.
Title it "Enrollment by Payer Type". Use distinct person_id count per payer_type.
Label the segments: commercial, medicaid, medicare.
```

**Prompt 6 — Age band bar chart**
```
Using dev.prod_main_members.eligibility joined to dev.prod_main_members.patient on person_id,
calculate each member's current age in years from birth_date.
Group into age bands: 0-17, 18-34, 35-49, 50-64, 65+.
Show a vertical bar chart of member count per age band titled "Members by Age Band".
```

**Prompt 7 — Gender split**
```
Show a donut chart of member count by gender from dev.prod_main_members.eligibility
titled "Members by Gender". Use distinct person_id per gender value.
```

---

### Row 3 — Geography + enrollment trend

**Prompt 8 — Members by state (bar chart)**
```
Show top 15 states by member count from dev.prod_main_members.eligibility
using a horizontal bar chart. Count distinct person_id per state.
Title it "Top 15 States by Enrollment". Sort descending.
```

**Prompt 9 — New enrollments by month**
```
Show count of new member enrollments per month from dev.prod_main_members.eligibility
using enrollment_start_date truncated to month.
Use a line chart. Title it "New Enrollments by Month".
Only include dates from 2024-01-01 onwards.
```

**Prompt 10 — Race/ethnicity breakdown**
```
Show a horizontal bar chart of member count by race from dev.prod_main_members.eligibility.
Count distinct person_id per race value. Title it "Members by Race".
Sort descending by count. Exclude null values.
```

---

## Page 2 — Claims & Charges

*Source tables: `dev.prod_main_claims.medical_claim`, `dev.prod_main_claims.pharmacy_claim`*

---

### KPI Row

**Prompt 1 — Total claims**
```
Show total count of distinct claim_id from dev.prod_main_claims.medical_claim
as a KPI titled "Total Claims".
```

**Prompt 2 — Total billed amount**
```
Show sum of charge_amount from dev.prod_main_claims.medical_claim
formatted as currency (e.g. $12.4M) as a KPI titled "Total Billed".
Use one row per claim (take max charge_amount per claim_id to avoid line double-counting).
```

**Prompt 3 — Total paid amount**
```
Show sum of paid_amount from dev.prod_main_claims.medical_claim
where paid_amount > 0 formatted as currency as a KPI titled "Total Paid".
```

**Prompt 4 — Denial rate**
```
Show the percentage of claim lines where paid_amount = 0 or paid_amount is null
from dev.prod_main_claims.medical_claim as a KPI titled "Denial Rate".
Format as a percentage (e.g. 18.5%).
```

---

### Row 2 — Cost trends + claim type mix

**Prompt 5 — Monthly billed vs paid trend**
```
Show a dual-line chart of monthly total charge_amount and total paid_amount
from dev.prod_main_claims.medical_claim using claim_start_date truncated to month.
Title it "Monthly Billed vs Paid". Aggregate by summing charge_amount and paid_amount per month.
Only include 2024 onwards.
```

**Prompt 6 — Claims by type (donut)**
```
Show a donut chart of distinct claim count by claim_type
from dev.prod_main_claims.medical_claim.
Count distinct claim_id per claim_type. Title it "Claims by Type".
```

**Prompt 7 — Average cost by claim type (bar)**
```
Show a bar chart of average charge_amount per claim by claim_type
from dev.prod_main_claims.medical_claim.
Compute average charge_amount per distinct claim_id, then group by claim_type.
Title it "Avg Billed Amount by Claim Type".
```

---

### Row 3 — Top diagnoses + place of service

**Prompt 8 — Top 10 diagnosis codes by claim volume**
```
Show a horizontal bar chart of the top 10 diagnosis_code_1 values by count of distinct claim_id
from dev.prod_main_claims.medical_claim.
Exclude null values. Title it "Top 10 Diagnoses by Claim Volume". Sort descending.
```

**Prompt 9 — Top 10 diagnosis codes by total paid**
```
Show a horizontal bar chart of the top 10 diagnosis_code_1 values by sum of paid_amount
from dev.prod_main_claims.medical_claim where paid_amount > 0.
Title it "Top 10 Diagnoses by Paid Amount". Sort descending. Exclude nulls.
```

**Prompt 10 — Claims by place of service**
```
Show a bar chart of claim count by place_of_service_code from dev.prod_main_claims.medical_claim.
Count distinct claim_id per code. Title it "Claims by Place of Service".
Map codes to labels: 11=Office, 21=Inpatient Hospital, 22=Outpatient Hospital,
23=Emergency Dept, 31=Skilled Nursing. Sort by count descending.
```

---

### Row 4 — Denial analysis + cost distribution

**Prompt 11 — Denial rate by claim type**
```
Show a grouped bar chart of denial rate (% of claim lines with paid_amount = 0)
by claim_type from dev.prod_main_claims.medical_claim.
Title it "Denial Rate by Claim Type".
```

**Prompt 12 — Charge amount distribution (histogram)**
```
Show a histogram of charge_amount per claim from dev.prod_main_claims.medical_claim.
Compute one row per claim_id using max(charge_amount).
Use 20 bins between $0 and $50,000. Title it "Claim Charge Amount Distribution".
```

**Prompt 13 — HCPCS top procedures by volume**
```
Show a horizontal bar chart of the top 10 hcpcs_code values by count of claim lines
from dev.prod_main_claims.medical_claim where hcpcs_code is not null.
Title it "Top Procedures by Volume (HCPCS)". Sort descending.
```

---

## Page 3 — Clinical Conditions

*Source tables: `dev.prod_main_clinical.condition`, `dev.prod_main_clinical.encounter`,
`dev.prod_main_clinical.lab_result`, `dev.prod_main_clinical.observation`*

---

### KPI Row

**Prompt 1 — Total encounters**
```
Show total count of distinct encounter_id from dev.prod_main_clinical.encounter
as a KPI titled "Total Encounters".
```

**Prompt 2 — Unique patients with encounters**
```
Show count of distinct person_id from dev.prod_main_clinical.encounter
as a KPI titled "Unique Patients".
```

**Prompt 3 — Average encounters per patient**
```
Show average number of encounters per person_id from dev.prod_main_clinical.encounter
as a KPI titled "Avg Encounters / Patient". Round to 1 decimal.
```

**Prompt 4 — Total active conditions**
```
Show count of condition_id from dev.prod_main_clinical.condition
where status = 'active' as a KPI titled "Active Conditions".
```

---

### Row 2 — Encounter type breakdown + volume trend

**Prompt 5 — Encounter type distribution (donut)**
```
Show a donut chart of encounter count by encounter_type from dev.prod_main_clinical.encounter.
Count distinct encounter_id per encounter_type. Title it "Encounters by Type".
```

**Prompt 6 — Monthly encounter volume trend**
```
Show a line chart of encounter count per month from dev.prod_main_clinical.encounter
using encounter_start_date truncated to month.
Title it "Monthly Encounter Volume". Only include 2024 onwards.
```

**Prompt 7 — Encounters by day of week**
```
Show a bar chart of encounter count by day of week (Monday through Sunday)
from dev.prod_main_clinical.encounter using encounter_start_date.
Title it "Encounter Volume by Day of Week".
```

---

### Row 3 — Top conditions + prevalence

**Prompt 8 — Top 15 conditions by patient count**
```
Show a horizontal bar chart of the top 15 source_code values by count of distinct person_id
from dev.prod_main_clinical.condition where source_code_type = 'icd-10-cm'.
Join source_description to label each bar.
Title it "Top 15 Conditions by Patient Prevalence". Sort descending.
```

**Prompt 9 — Top conditions by encounter volume**
```
Show a horizontal bar chart of the top 10 primary_diagnosis_code values
by count of distinct encounter_id from dev.prod_main_clinical.encounter.
Exclude null values. Title it "Top 10 Diagnoses by Encounter Volume". Sort descending.
```

**Prompt 10 — Active vs resolved conditions split**
```
Show a donut chart of condition count by status from dev.prod_main_clinical.condition.
Include: active, resolved, historical. Title it "Condition Status Distribution".
```

---

### Row 4 — Lab results + vitals

**Prompt 11 — Average HbA1c by month**
```
Show a line chart of average HbA1c result per month from dev.prod_main_clinical.lab_result
where source_component_code = '4548-4' and result is not null.
Cast result to decimal. Use result_datetime truncated to month on the x-axis.
Title it "Avg HbA1c Trend Over Time".
```

**Prompt 12 — HbA1c distribution (histogram)**
```
Show a histogram of HbA1c result values from dev.prod_main_clinical.lab_result
where source_component_code = '4548-4' and result is not null.
Cast result to decimal. Use 20 bins between 4 and 14.
Title it "HbA1c Value Distribution". Add vertical reference lines at 5.7 (prediabetes) and 6.5 (diabetes).
```

**Prompt 13 — Average BMI by month**
```
Show a line chart of average BMI result per month from dev.prod_main_clinical.observation
where source_code = '39156-5' and result is not null.
Cast result to decimal. Use observation_date truncated to month on the x-axis.
Title it "Avg BMI Trend Over Time".
```

**Prompt 14 — Condition co-occurrence (top 10 diagnosis pairs)**
```
From dev.prod_main_clinical.condition, find the top 10 most common pairs of source_code
values that appear together for the same person_id.
Show as a table with columns: condition_a, condition_b, patient_count.
Title it "Top Condition Co-occurrences".
```

---

## PROD vs TEST Comparison Prompts

Use these to surface the drift signals we baked into the synthetic data.

**Members comparison**
```
Show side-by-side KPIs comparing total member count in dev.prod_main_members.eligibility
vs dev.test_main_members.eligibility. Label them PROD and TEST.
```

**Claims charge amount comparison**
```
Show overlapping histograms of charge_amount per claim from dev.prod_main_claims.medical_claim
(labelled PROD) and dev.test_main_claims.medical_claim (labelled TEST).
Title it "Charge Amount Distribution: PROD vs TEST".
Expected: TEST distribution is shifted right (higher charges).
```

**HbA1c PROD vs TEST**
```
Show a side-by-side box plot of HbA1c values (source_component_code = '4548-4')
from dev.prod_main_clinical.lab_result and dev.test_main_clinical.lab_result.
Label PROD and TEST. Title it "HbA1c: PROD vs TEST".
Expected: TEST mean ~8.1, PROD mean ~7.2.
```

**Denial rate PROD vs TEST**
```
Show a grouped bar chart comparing denial rate (% lines with paid_amount = 0)
by claim_type for dev.prod_main_claims.medical_claim (PROD)
vs dev.test_main_claims.medical_claim (TEST).
Title it "Denial Rate by Claim Type: PROD vs TEST".
Expected: TEST denial rate ~20%, PROD ~15%.
```

---

## Tips for Genie

- Paste each prompt into a **new widget** on its page — one prompt = one chart or KPI
- If Genie misinterprets a field name, add: *"the column is named exactly `charge_amount`"*
- For KPI tiles, tell Genie: *"display as a counter/number tile, not a chart"*
- For the left navigation sidebar: in the dashboard settings, enable **"Multi-page"** and name each page — Databricks renders the page list as a left nav automatically
- To compare PROD vs TEST in the same chart, use `UNION ALL` with a literal `'PROD'` / `'TEST'` column — Genie handles this well when prompted explicitly
