# Databricks notebook source

# MAGIC %md
# MAGIC # Simulate 28 Nightly Incremental Loads — Tuva Input Layer (TEST)
# MAGIC
# MAGIC Simulates 28 days of incremental data loads on the Tuva Input Layer synthetic
# MAGIC dataset in the `dev` catalog. This creates realistic time-series data for
# MAGIC demonstrating the Synaptiq Data Profiling Tool.
# MAGIC
# MAGIC ## What this notebook does
# MAGIC
# MAGIC **Part 1 — Schema migration:** Adds `META_data_source` and `META_Load_DTTM`
# MAGIC columns to all 30 tables (both PROD and TEST sides). The initial value is
# MAGIC `DATE_SUB(CURRENT_DATE, 28)` — representing "day zero" of the simulation.
# MAGIC
# MAGIC **Part 2 — Incremental loads:** Appends 28 days of new rows to `dev.test_main_*`
# MAGIC schemas **only**. PROD is intentionally left as the baseline for A/B comparison.
# MAGIC Each day adds:
# MAGIC - New patients (on days 5, 12, 19, 26)
# MAGIC - New encounters (~0.15 % daily growth)
# MAGIC - New medical claims from those encounters (drifted charge distribution)
# MAGIC - Proportionate clinical data (conditions, procedures, labs, vitals, meds, appointments)
# MAGIC - Independent pharmacy claims (~0.075 % daily growth)
# MAGIC
# MAGIC **Part 3 — Validation:** Row counts by day, growth summary, HbA1c drift by week.
# MAGIC
# MAGIC ## Drift signals preserved across loads
# MAGIC
# MAGIC | Signal | TEST distribution |
# MAGIC |--------|-------------------|
# MAGIC | `charge_amount` | lognormal(mu=7.2, sigma=0.85) |
# MAGIC | Denial rate | 20 % |
# MAGIC | HbA1c mean | Normal(8.1, 1.5) clipped [4.5, 13.0] |
# MAGIC | BMI mean | Normal(29.2, 5.0) clipped [15, 55] |
# MAGIC
# MAGIC ## Run time
# MAGIC Approximately 5-15 minutes depending on cluster size.
# MAGIC Run Part 1 once per environment setup; Part 2 is idempotent if rerun from scratch
# MAGIC (row-count increases are additive — rerunning will double-count unless the base
# MAGIC tables are regenerated first with `generate_tuva_input_layer.py`).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Imports and Configuration

# COMMAND ----------

from __future__ import annotations

import uuid
import datetime
from typing import Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Catalog / schema topology
# ---------------------------------------------------------------------------
CATALOG = "dev"

PROD_CLAIMS   = "prod_main_claims"
PROD_CLINICAL = "prod_main_clinical"
PROD_MEMBERS  = "prod_main_members"

TEST_CLAIMS   = "test_main_claims"
TEST_CLINICAL = "test_main_clinical"
TEST_MEMBERS  = "test_main_members"

# ---------------------------------------------------------------------------
# Simulation configuration
# ---------------------------------------------------------------------------
BASE_DATE = datetime.date.today() - datetime.timedelta(days=28)  # day-zero of the 28-day window

DAILY_CLAIM_GROWTH_PCT    = 0.0015   # ~0.15 % per day → ~4.2 % cumulative over 28 days
DAILY_PHARMACY_GROWTH_PCT = 0.00075  # half the medical claim growth rate

MAX_SAMPLE = 50_000                  # row cap when sampling existing IDs

NEW_PATIENT_DAYS            = {5, 12, 19, 26}
NEW_PATIENTS_PER_OCCASION   = 10

# ---------------------------------------------------------------------------
# Run flags — set RUN_PROD = True to also simulate 28 days for PROD schemas.
# PROD uses its original distribution (lower charges, lower denial, lower HbA1c).
# ---------------------------------------------------------------------------
RUN_PROD = True   # set False to skip PROD and only update TEST

# ---------------------------------------------------------------------------
# TEST drift parameters (match generate_tuva_input_layer.py TEST distribution)
# ---------------------------------------------------------------------------
TEST_CHARGE_MU    = 7.2
TEST_CHARGE_SIGMA = 0.85
TEST_DENIAL_RATE  = 0.20
TEST_PROFESSIONAL_RATE = 0.55
TEST_HBAC1C_MEAN  = 8.1
TEST_BMI_MEAN     = 29.2

# ---------------------------------------------------------------------------
# PROD baseline parameters (match generate_tuva_input_layer.py PROD distribution)
# ---------------------------------------------------------------------------
PROD_CHARGE_MU    = 6.9
PROD_CHARGE_SIGMA = 0.80
PROD_DENIAL_RATE  = 0.15
PROD_PROFESSIONAL_RATE = 0.60
PROD_HBAC1C_MEAN  = 7.2
PROD_BMI_MEAN     = 27.5

# ---------------------------------------------------------------------------
# Shared constants (match generate_tuva_input_layer.py)
# ---------------------------------------------------------------------------
DATA_SOURCE      = "SynaptiqPOC"
META_DATA_SOURCE = "META_SynaptiqPOC"
PAYER            = "Aetna"
PLAN             = "Aetna PPO"

N_PRACTITIONERS = 200
N_LOCATIONS     = 50

PRAC_IDS  = [f"PR{i:05d}" for i in range(1, N_PRACTITIONERS + 1)]
PRAC_NPIS = [f"{1000000000 + i}" for i in range(1, N_PRACTITIONERS + 1)]
LOC_IDS   = [f"L{i:04d}" for i in range(1, N_LOCATIONS + 1)]
LOC_NPIS  = [f"{1500000000 + i}" for i in range(1, N_LOCATIONS + 1)]

print("Configuration loaded.")
print(f"  BASE_DATE            = {BASE_DATE}")
print(f"  DAILY_CLAIM_GROWTH   = {DAILY_CLAIM_GROWTH_PCT:.4%}")
print(f"  NEW_PATIENT_DAYS     = {sorted(NEW_PATIENT_DAYS)}")
print(f"  NEW_PATIENTS_PER_OCC = {NEW_PATIENTS_PER_OCCASION}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Reference Data (copied from generate_tuva_input_layer.py)

# COMMAND ----------

ICD10_CODES = [
    "M54.5","I10","E11.9","J06.9","K21.0","Z00.00","Z12.31","F32.9","J44.1",
    "N39.0","Z23","R05","R51","G43.909","E78.5","I25.10","J18.9","S93.401",
    "M17.11","Z87.891","E11.65","I50.9","J45.909","F41.9","M79.3",
]

CPT_PROFESSIONAL = [
    "99213","99214","99203","99232","99283","93000","71046","36415",
    "80053","85025","80061","82947","97110","99395",
]

CPT_INSTITUTIONAL = [
    "99232","99233","99291","31500","36556","43753",
]

NDC_DRUGS: dict[str, str] = {
    "00071015223": "Lisinopril 10mg",
    "00093721956": "Metformin 500mg",
    "00054418925": "Atorvastatin 20mg",
    "65862001705": "Omeprazole 20mg",
    "00781107905": "Amlodipine 5mg",
    "00378395305": "Levothyroxine 50mcg",
    "00093310505": "Sertraline 50mg",
    "63304082805": "Metoprolol 25mg",
    "00247100552": "Albuterol Inhaler",
    "00006098154": "Januvia 100mg",
}
NDC_LIST  = list(NDC_DRUGS.keys())
NDC_NAMES = list(NDC_DRUGS.values())

# LOINC: code -> (description, units, low, high)
LOINC_LABS: dict[str, tuple[str, str, float, float]] = {
    "4548-4":  ("HbA1c",      "%",       4.5,   13.0),
    "2160-0":  ("Creatinine", "mg/dL",   0.5,    4.0),
    "2345-7":  ("Glucose",    "mg/dL",  60.0,  400.0),
    "2089-1":  ("LDL",        "mg/dL",  40.0,  220.0),
    "718-7":   ("Hemoglobin", "g/dL",    7.0,   18.0),
    "6690-2":  ("WBC",        "10*3/uL", 1.5,   18.0),
    "2823-3":  ("Potassium",  "mEq/L",   2.5,    6.5),
}
LOINC_LAB_CODES = list(LOINC_LABS.keys())

LOINC_VITALS: dict[str, tuple[str, str, float, float]] = {
    "8480-6":  ("Systolic BP",  "mmHg",   90.0, 180.0),
    "8462-4":  ("Diastolic BP", "mmHg",   50.0, 120.0),
    "8867-4":  ("Heart Rate",   "bpm",    50.0, 120.0),
    "39156-5": ("BMI",          "kg/m2",  18.0,  50.0),
    "3141-9":  ("Body Weight",  "kg",     45.0, 160.0),
}
LOINC_VITAL_CODES = list(LOINC_VITALS.keys())

ENCOUNTER_TYPES = ["outpatient","inpatient","emergency","office visit","telehealth","observation"]
ADMIT_SOURCE    = ["1","2","4","5","7","8","9"]
ADMIT_TYPE      = ["1","2","3","4","9"]
DISCH_DISP      = ["01","02","03","04","05","07","20","30","43","51"]
POS_CODES       = ["11","21","22","23","24","31","32","99"]
BILL_TYPES      = ["111","112","121","122","131","132","211","212"]
REVENUE_CODES   = ["0100","0110","0120","0200","0210","0250","0300","0370","0450","0636"]
DRG_CODES       = ["291","292","293","194","195","196","470","871","872","378","379","380","247","248","249"]
MOD_CODES       = ["25","26","59","TC","GT","95","GX","GY", None, None, None, None]

CONDITION_STATUSES = (["active"] * 6 + ["resolved"] * 3 + ["historical"])
CONDITION_TYPES    = ["encounter-diagnosis","problem-list-item","health-concern"]

APPT_STATUSES = ["completed"] * 14 + ["canceled"] * 3 + ["no-show"] * 2 + ["scheduled"] * 1
APPT_TYPES    = ["Follow-up","New Patient","Annual Wellness","Sick Visit","Procedure","Telehealth","Consultation"]

ROUTES     = ["oral","intravenous","intramuscular","subcutaneous","inhaled","topical","sublingual"]
BODY_SITES = ["left arm","right arm","left thigh","right thigh","deltoid","abdomen", None]

RACES       = ["White","Black or African American","Asian","American Indian or Alaska Native","Other","Unknown"]
ETHNICITIES = ["Not Hispanic or Latino","Hispanic or Latino","Unknown"]
SEXES       = ["male","female"]
STATES      = ["CA","TX","FL","NY","PA","IL","OH","GA","NC","MI","NJ","VA","WA","AZ","MA"]

FIRST_NAMES_F = ["Emma","Olivia","Ava","Isabella","Sophia","Mia","Charlotte","Amelia","Harper","Evelyn",
                  "Abigail","Emily","Elizabeth","Sofia","Avery","Ella","Scarlett","Grace","Victoria","Riley"]
FIRST_NAMES_M = ["Liam","Noah","William","James","Oliver","Benjamin","Elijah","Lucas","Mason","Logan",
                  "Alexander","Ethan","Daniel","Jacob","Michael","Henry","Jackson","Sebastian","Aiden","Matthew"]
LAST_NAMES    = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
                  "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
                  "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
                  "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores"]

METAL_LEVELS       = ["bronze","silver","gold","platinum","catastrophic"]
ENROLLMENT_STATUSES = ["active","inactive","pending","termed"]
LOBS               = ["commercial","medicare","medicaid","exchange"]
ATCODES = ["A10BA02","C09AA02","C10AA01","A02BC01","C08CA01",
           "H03AA01","N06AB06","C07AB02","R03AC02","A10BH01"]
RXNORM  = ["860975","861007","617311","40790","329528",
           "36567","41493","435","261242","10582"]

print("Reference data loaded.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Helper: write_delta_append

# COMMAND ----------

def write_delta_append(pdf: pd.DataFrame, catalog: str, schema: str, table: str) -> int:
    """
    Append a pandas DataFrame to an existing Delta table.

    Type alignment: reads the target table's schema and casts each column in the
    incoming DataFrame to the exact type already on disk.  This prevents
    DELTA_FAILED_TO_MERGE_FIELDS errors when an all-None column is inferred as
    NullType (or StringType) but the target column is DATE, TIMESTAMP, etc.

    Returns the number of rows appended.
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import NullType, StringType

    if pdf.empty:
        return 0

    full_name = f"`{catalog}`.`{schema}`.`{table}`"
    sdf = spark.createDataFrame(pdf)

    # Read the target schema once and build a name → DataType map.
    try:
        target_types = {f.name: f.dataType for f in spark.table(full_name).schema.fields}
    except Exception:
        target_types = {}

    for field in sdf.schema.fields:
        col = field.name
        if isinstance(field.dataType, NullType):
            # Cast to target type when known; fall back to STRING.
            cast_type = target_types.get(col, StringType())
            sdf = sdf.withColumn(col, F.lit(None).cast(cast_type))
        elif col in target_types and str(field.dataType) != str(target_types[col]):
            # Coerce mismatched types (e.g. STRING → DATE) to match target.
            try:
                sdf = sdf.withColumn(col, F.col(col).cast(target_types[col]))
            except Exception:
                pass  # leave as-is; mergeSchema will handle or raise clearly

    (sdf.write
        .format("delta")
        .mode("append")
        .option("mergeSchema", "true")
        .saveAsTable(full_name))

    return len(pdf)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 1: Add META Columns to All 30 Tables

# COMMAND ----------

# MAGIC %md
# MAGIC Adds `META_data_source` (STRING) and `META_Load_DTTM` (TIMESTAMP) to every
# MAGIC table in both the PROD and TEST schemas. The initial backfill sets
# MAGIC `META_Load_DTTM` to 28 days ago (day zero of the simulation) so that PROD
# MAGIC data appears as a pre-existing baseline relative to the incremental loads.
# MAGIC
# MAGIC Safe to rerun: existing columns are checked via schema inspection before
# MAGIC ALTER TABLE so no duplicate-column errors occur. The UPDATE only fires
# MAGIC where META_Load_DTTM is still NULL.

# COMMAND ----------

ALL_TABLES: list[tuple[str, str]] = [
    # (schema, table)
    (PROD_CLAIMS,   "medical_claim"),
    (PROD_CLAIMS,   "pharmacy_claim"),
    (PROD_CLAIMS,   "provider_attribution"),
    (PROD_CLAIMS,   "location"),
    (PROD_CLAIMS,   "practitioner"),
    (PROD_CLINICAL, "encounter"),
    (PROD_CLINICAL, "condition"),
    (PROD_CLINICAL, "procedure"),
    (PROD_CLINICAL, "lab_result"),
    (PROD_CLINICAL, "observation"),
    (PROD_CLINICAL, "medication"),
    (PROD_CLINICAL, "immunization"),
    (PROD_CLINICAL, "appointment"),
    (PROD_CLINICAL, "location"),
    (PROD_CLINICAL, "practitioner"),
    (PROD_MEMBERS,  "patient"),
    (PROD_MEMBERS,  "eligibility"),
    (PROD_MEMBERS,  "location"),
    (PROD_MEMBERS,  "practitioner"),
    (TEST_CLAIMS,   "medical_claim"),
    (TEST_CLAIMS,   "pharmacy_claim"),
    (TEST_CLAIMS,   "provider_attribution"),
    (TEST_CLAIMS,   "location"),
    (TEST_CLAIMS,   "practitioner"),
    (TEST_CLINICAL, "encounter"),
    (TEST_CLINICAL, "condition"),
    (TEST_CLINICAL, "procedure"),
    (TEST_CLINICAL, "lab_result"),
    (TEST_CLINICAL, "observation"),
    (TEST_CLINICAL, "medication"),
    (TEST_CLINICAL, "immunization"),
    (TEST_CLINICAL, "appointment"),
    (TEST_CLINICAL, "location"),
    (TEST_CLINICAL, "practitioner"),
    (TEST_MEMBERS,  "patient"),
    (TEST_MEMBERS,  "eligibility"),
    (TEST_MEMBERS,  "location"),
    (TEST_MEMBERS,  "practitioner"),
]

print(f"Adding META columns to {len(ALL_TABLES)} tables ...")
print(f"  Baseline META_Load_DTTM = {BASE_DATE} (28 days ago)")
print()

for schema, table in ALL_TABLES:
    full_name = f"`{CATALOG}`.`{schema}`.`{table}`"
    try:
        # ADD COLUMN IF NOT EXISTS is not supported on all Databricks runtimes.
        # Check existing columns first and only ALTER when the column is absent.
        existing_cols = {f.name.lower() for f in spark.table(full_name).schema.fields}

        if "meta_data_source" not in existing_cols:
            spark.sql(f"ALTER TABLE {full_name} ADD COLUMN META_data_source STRING")
        if "meta_load_dttm" not in existing_cols:
            spark.sql(f"ALTER TABLE {full_name} ADD COLUMN META_Load_DTTM TIMESTAMP")

        spark.sql(f"""
            UPDATE {full_name}
            SET    META_data_source = data_source,
                   META_Load_DTTM   = CAST(DATE_SUB(CURRENT_DATE(), 28) AS TIMESTAMP)
            WHERE  META_Load_DTTM IS NULL
        """)
        print(f"  OK  {schema}.{table}")
    except Exception as exc:
        print(f"  WARN {schema}.{table}: {exc}")

print()
print("Part 1 complete — META columns added and backfilled.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 2: 28-Day Incremental Loads (TEST Schemas Only)

# COMMAND ----------

# MAGIC %md
# MAGIC Load the existing TEST person pool once — used to assign new encounters and
# MAGIC pharmacy claims to real person IDs throughout the simulation.

# COMMAND ----------

from pyspark.sql import functions as F

# Load existing person IDs from the TEST patient table.
# Cap at MAX_SAMPLE to keep driver memory manageable for large tables.
_existing_person_ids_sdf = (
    spark.table(f"`{CATALOG}`.`{TEST_MEMBERS}`.`patient`")
    .select("person_id")
    .distinct()
    .limit(MAX_SAMPLE)
)
EXISTING_PERSON_IDS: list[str] = [
    row["person_id"] for row in _existing_person_ids_sdf.collect()
]
print(f"Loaded {len(EXISTING_PERSON_IDS):,} existing person IDs from {TEST_MEMBERS}.patient")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Helper generators for incremental rows

# COMMAND ----------

def _make_encounter_rows(
    day: int,
    person_ids: list[str],
    n_encounters: int,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Generate n_encounters encounter rows for a given simulation day.

    person_ids  -- pool of person_ids to sample from (may include new patients)
    load_dttm   -- the META_Load_DTTM / logical load date for the batch
    """
    rows: list[dict[str, Any]] = []
    load_date = load_dttm.date()

    person_idx     = rng.integers(0, len(person_ids), size=n_encounters)
    enc_type_idx   = rng.integers(0, len(ENCOUNTER_TYPES), size=n_encounters)
    dur_days       = rng.integers(0, 5, size=n_encounters)
    admit_src_idx  = rng.integers(0, len(ADMIT_SOURCE), size=n_encounters)
    admit_type_idx = rng.integers(0, len(ADMIT_TYPE), size=n_encounters)
    disch_disp_idx = rng.integers(0, len(DISCH_DISP), size=n_encounters)
    prac_idx       = rng.integers(0, N_PRACTITIONERS, size=n_encounters)
    loc_idx        = rng.integers(0, N_LOCATIONS, size=n_encounters)
    dx_idx         = rng.integers(0, len(ICD10_CODES), size=n_encounters)
    drg_idx        = rng.integers(0, len(DRG_CODES), size=n_encounters)
    charge_arr     = np.exp(rng.normal(TEST_CHARGE_MU, TEST_CHARGE_SIGMA, size=n_encounters))
    paid_arr       = charge_arr * rng.uniform(0.5, 0.85, size=n_encounters)
    allowed_arr    = charge_arr * rng.uniform(0.7, 0.95, size=n_encounters)
    # Encounter dates distributed within ±3 days of load date
    date_jitter    = rng.integers(-3, 4, size=n_encounters)

    for i in range(n_encounters):
        p_id     = person_ids[person_idx[i]]
        # patient_id: replace PER- prefix with PAT- to match base data convention
        # (base data uses PER-000001 / PAT-000001; new patients PT-LOAD05-0000 / PT-LOAD05-0000)
        pt_id    = p_id.replace("PER-", "PAT-", 1) if p_id.startswith("PER-") else f"PT-{p_id}"
        prac     = PRAC_IDS[prac_idx[i]]
        loc      = LOC_IDS[loc_idx[i]]
        enc_type = ENCOUNTER_TYPES[enc_type_idx[i]]
        is_inpatient = enc_type in ("inpatient", "emergency", "observation")

        enc_start = load_date + datetime.timedelta(days=int(date_jitter[i]))
        enc_end   = enc_start + datetime.timedelta(days=int(dur_days[i]))

        # Single canonical encounter_id — used in all downstream FK references
        enc_id = f"ENC-D{day:02d}-{p_id}-{i:05d}"

        rows.append({
            "encounter_id":                enc_id,
            "person_id":                   p_id,
            "patient_id":                  pt_id,
            "encounter_type":              enc_type,
            "encounter_start_date":        enc_start,
            "encounter_end_date":          enc_end,
            "admit_source_code":           ADMIT_SOURCE[admit_src_idx[i]] if is_inpatient else None,
            "admit_type_code":             ADMIT_TYPE[admit_type_idx[i]]  if is_inpatient else None,
            "discharge_disposition_code":  DISCH_DISP[disch_disp_idx[i]] if is_inpatient else None,
            "attending_provider_id":       prac,
            "attending_provider_name":     None,
            "facility_npi":                LOC_NPIS[loc_idx[i]],
            "facility_name":               f"Location Facility {loc_idx[i]+1:03d}",
            "primary_diagnosis_code_type": "ICD-10-CM",
            "primary_diagnosis_code":      ICD10_CODES[dx_idx[i]],
            "drg_code_type":               "MS-DRG" if is_inpatient else None,
            "drg_code":                    DRG_CODES[drg_idx[i]] if is_inpatient else None,
            "paid_amount":                 round(float(paid_arr[i]), 2),
            "allowed_amount":              round(float(allowed_arr[i]), 2),
            "charge_amount":               round(float(charge_arr[i]), 2),
            "ingest_datetime":             load_dttm,
            "data_source":                 DATA_SOURCE,
            "META_data_source":            META_DATA_SOURCE,
            "META_Load_DTTM":              load_dttm,
        })
    return pd.DataFrame(rows)


def _make_medical_claim_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Generate medical claim lines from a batch of new encounters.
    1-3 lines per encounter, using TEST drift parameters.
    """
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    lines_per_enc     = rng.integers(1, 4, size=n_enc)
    is_professional   = rng.random(size=n_enc) < TEST_PROFESSIONAL_RATE
    denial_mask       = rng.random(size=n_enc) < TEST_DENIAL_RATE
    paid_date_offsets = rng.integers(14, 45, size=n_enc)

    enc_records = encounters_df.to_dict("records")

    for i, enc in enumerate(enc_records):
        n_lines   = int(lines_per_enc[i])
        is_prof   = bool(is_professional[i])
        is_denied = bool(denial_mask[i])
        claim_type = "PROFESSIONAL" if is_prof else "INSTITUTIONAL"
        cpt_pool   = CPT_PROFESSIONAL if is_prof else CPT_INSTITUTIONAL

        enc_start: datetime.date = enc["encounter_start_date"]
        enc_end:   datetime.date = enc["encounter_end_date"]
        person_id  = enc["person_id"]
        # member_id: replace PER- with MBR- to match base data convention
        # (base data: PER-000001 / MBR-000001; new patients: PER-LOAD05-0000 / MBR-LOAD05-0000)
        # This must match the member_id written to eligibility by _make_eligibility_rows.
        member_id  = person_id.replace("PER-", "MBR-", 1) if person_id.startswith("PER-") else f"MBR-{person_id}"
        paid_dt    = enc_start + datetime.timedelta(days=int(paid_date_offsets[i]))
        claim_id   = f"CLM-D{enc_start.strftime('%Y%m%d')}-{person_id}-{i:06d}"

        charge_lines  = np.exp(rng.normal(TEST_CHARGE_MU, TEST_CHARGE_SIGMA, size=n_lines))
        paid_ratio    = rng.uniform(0.5, 0.85,  size=n_lines)
        allowed_ratio = rng.uniform(0.7, 0.95,  size=n_lines)
        coins_ratio   = rng.uniform(0.05, 0.20, size=n_lines)
        copay_arr     = rng.choice([0, 10, 20, 30, 40, 50], size=n_lines)
        ded_ratio     = rng.uniform(0.0, 0.10,  size=n_lines)
        svc_units     = rng.integers(1, 4, size=n_lines)
        hcpcs_idx     = rng.integers(0, len(cpt_pool), size=n_lines)
        dx_idx        = rng.integers(0, len(ICD10_CODES), size=15)
        rev_idx       = rng.integers(0, len(REVENUE_CODES), size=n_lines)
        mod_idx       = rng.integers(0, len(MOD_CODES), size=n_lines * 5)
        prac_i        = rng.integers(0, N_PRACTITIONERS)
        loc_i         = rng.integers(0, N_LOCATIONS)

        rendering_npi = PRAC_NPIS[prac_i]
        billing_npi   = PRAC_NPIS[rng.integers(0, N_PRACTITIONERS)]
        facility_npi  = LOC_NPIS[loc_i]
        pos_code      = rng.choice(POS_CODES)
        bill_type     = rng.choice(BILL_TYPES) if not is_prof else None
        drg_code      = rng.choice(DRG_CODES)  if not is_prof else None

        for ln_num in range(1, n_lines + 1):
            li      = ln_num - 1
            charge  = round(float(charge_lines[li]), 2)
            allowed = round(float(charge * allowed_ratio[li]), 2)
            if is_denied:
                paid = coins = copay = ded = 0.0
            else:
                paid  = round(float(charge * paid_ratio[li]), 2)
                coins = round(float(charge * coins_ratio[li]), 2)
                copay = float(copay_arr[li])
                ded   = round(float(charge * ded_ratio[li]), 2)
            total_cost = round(paid + coins + copay + ded, 2)

            mod_base = li * 5
            mods = [MOD_CODES[int(mod_idx[mod_base + j])] for j in range(5)]

            rows.append({
                "claim_id":                   claim_id,
                "claim_line_number":          ln_num,
                "claim_type":                 claim_type,
                "person_id":                  person_id,
                "member_id":                  member_id,
                "payer":                      PAYER,
                "plan":                       PLAN,
                "claim_start_date":           enc_start,
                "claim_end_date":             enc_end,
                "claim_line_start_date":      enc_start,
                "claim_line_end_date":        enc_end,
                "admission_date":             enc_start if not is_prof else None,
                "discharge_date":             enc_end   if not is_prof else None,
                "admit_source_code":          enc.get("admit_source_code"),
                "admit_type_code":            enc.get("admit_type_code"),
                "discharge_disposition_code": enc.get("discharge_disposition_code"),
                "place_of_service_code":      pos_code,
                "bill_type_code":             bill_type,
                "drg_code_type":              "MS-DRG" if drg_code else None,
                "drg_code":                   drg_code,
                "revenue_center_code":        REVENUE_CODES[rev_idx[li]] if not is_prof else None,
                "service_unit_quantity":      int(svc_units[li]),
                "hcpcs_code":                 cpt_pool[hcpcs_idx[li]],
                "hcpcs_modifier_1":           mods[0],
                "hcpcs_modifier_2":           mods[1],
                "hcpcs_modifier_3":           mods[2],
                "hcpcs_modifier_4":           mods[3],
                "hcpcs_modifier_5":           mods[4],
                "rendering_npi":              rendering_npi,
                "rendering_tin":              f"{rng.integers(100000000, 999999999):09d}",
                "billing_npi":                billing_npi,
                "billing_tin":                f"{rng.integers(100000000, 999999999):09d}",
                "facility_npi":               facility_npi,
                "paid_date":                  paid_dt,
                "paid_amount":                paid,
                "allowed_amount":             allowed,
                "charge_amount":              charge,
                "coinsurance_amount":         coins,
                "copayment_amount":           copay,
                "deductible_amount":          ded,
                "total_cost_amount":          total_cost,
                "diagnosis_code_type":        "ICD-10-CM",
                "diagnosis_code_1":           ICD10_CODES[dx_idx[0]],
                "diagnosis_code_2":           ICD10_CODES[dx_idx[1]]  if rng.random() > 0.3 else None,
                "diagnosis_code_3":           ICD10_CODES[dx_idx[2]]  if rng.random() > 0.5 else None,
                "diagnosis_code_4":           ICD10_CODES[dx_idx[3]]  if rng.random() > 0.7 else None,
                "diagnosis_code_5":           ICD10_CODES[dx_idx[4]]  if rng.random() > 0.8 else None,
                "diagnosis_code_6":           ICD10_CODES[dx_idx[5]]  if rng.random() > 0.9 else None,
                "diagnosis_code_7":           ICD10_CODES[dx_idx[6]]  if rng.random() > 0.95 else None,
                "diagnosis_code_8":           ICD10_CODES[dx_idx[7]]  if rng.random() > 0.97 else None,
                "diagnosis_code_9":           ICD10_CODES[dx_idx[8]]  if rng.random() > 0.98 else None,
                "diagnosis_code_10":          ICD10_CODES[dx_idx[9]]  if rng.random() > 0.99 else None,
                "diagnosis_code_11":          None,
                "diagnosis_code_12":          None,
                "diagnosis_code_13":          None,
                "diagnosis_code_14":          None,
                "diagnosis_code_15":          None,
                "data_source":                DATA_SOURCE,
                "META_data_source":           META_DATA_SOURCE,
                "META_Load_DTTM":             load_dttm,
            })
    return pd.DataFrame(rows)


def _make_pharmacy_claim_rows(
    n: int,
    person_ids: list[str],
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Generate n pharmacy claim rows for random persons from person_ids.
    """
    if n == 0:
        return pd.DataFrame()

    load_date = load_dttm.date()
    rows: list[dict[str, Any]] = []

    person_idx    = rng.integers(0, len(person_ids), size=n)
    ndc_idx       = rng.integers(0, len(NDC_LIST), size=n)
    dispense_jit  = rng.integers(-3, 4, size=n)
    paid_offsets  = rng.integers(7, 30, size=n)
    qty_arr       = rng.integers(30, 91, size=n)
    days_arr      = rng.integers(30, 91, size=n)
    refills_arr   = rng.integers(0, 6, size=n)
    charge_arr    = rng.uniform(10.0, 800.0, size=n)
    paid_ratio    = rng.uniform(0.5, 0.95, size=n)
    coins_ratio   = rng.uniform(0.05, 0.20, size=n)
    copay_arr     = rng.choice([0, 5, 10, 20, 40, 80], size=n)
    ded_ratio     = rng.uniform(0.0, 0.10, size=n)
    in_net        = rng.random(size=n) > 0.10
    presc_idx     = rng.integers(0, N_PRACTITIONERS, size=n)
    disp_idx      = rng.integers(0, N_PRACTITIONERS, size=n)

    for i in range(n):
        person_id = person_ids[person_idx[i]]
        member_id = person_id.replace("PER-", "MBR-", 1) if person_id.startswith("PER-") else f"MBR-{person_id}"
        disp_date = load_date + datetime.timedelta(days=int(dispense_jit[i]))
        paid_date = disp_date + datetime.timedelta(days=int(paid_offsets[i]))
        ndc       = NDC_LIST[ndc_idx[i]]
        charge    = round(float(charge_arr[i]), 2)
        paid      = round(float(charge * paid_ratio[i]), 2)
        allowed   = round(float(charge * rng.uniform(0.7, 0.95)), 2)
        coins     = round(float(charge * coins_ratio[i]), 2)
        copay     = float(copay_arr[i])
        ded       = round(float(charge * ded_ratio[i]), 2)

        rows.append({
            "claim_id":                 f"RXCLM-D{load_date.strftime('%Y%m%d')}-{person_id}-{i:05d}",
            "claim_line_number":        1,
            "person_id":                person_id,
            "member_id":                member_id,
            "payer":                    PAYER,
            "plan":                     PLAN,
            "prescribing_provider_npi": PRAC_NPIS[presc_idx[i]],
            "dispensing_provider_npi":  PRAC_NPIS[disp_idx[i]],
            "dispensing_date":          disp_date,
            "ndc_code":                 ndc,
            "quantity":                 int(qty_arr[i]),
            "days_supply":              int(days_arr[i]),
            "refills":                  int(refills_arr[i]),
            "paid_date":                paid_date,
            "paid_amount":              paid,
            "allowed_amount":           allowed,
            "charge_amount":            charge,
            "coinsurance_amount":       coins,
            "copayment_amount":         copay,
            "deductible_amount":        ded,
            "in_network_flag":          int(in_net[i]),
            "file_name":                f"pharmacy_{load_date.strftime('%Y%m%d')}.csv",
            "file_date":                load_date,
            "ingest_datetime":          load_dttm,
            "data_source":              DATA_SOURCE,
            "META_data_source":         META_DATA_SOURCE,
            "META_Load_DTTM":           load_dttm,
        })
    return pd.DataFrame(rows)


def _make_condition_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """2-4 conditions per encounter."""
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id", "encounter_start_date"]
    ].to_dict("records")
    conditions_per_enc = rng.integers(2, 5, size=n_enc)

    for i, enc in enumerate(enc_records):
        n_cond    = int(conditions_per_enc[i])
        dx_idx    = rng.integers(0, len(ICD10_CODES), size=n_cond)
        enc_date  = enc["encounter_start_date"]

        for j in range(n_cond):
            recorded_date = enc_date - datetime.timedelta(days=int(rng.integers(0, 3)))
            onset_date    = enc_date - datetime.timedelta(days=int(rng.integers(1, 180)))
            status        = rng.choice(CONDITION_STATUSES)
            resolved_date = (
                enc_date + datetime.timedelta(days=int(rng.integers(7, 90)))
                if status == "resolved" else None
            )
            icd_code = ICD10_CODES[dx_idx[j]]
            rows.append({
                "condition_id":           f"{enc['encounter_id']}-C{j:02d}",
                "person_id":              enc["person_id"],
                "patient_id":             enc["patient_id"],
                "encounter_id":           enc["encounter_id"],
                "recorded_date":          recorded_date,
                "onset_date":             onset_date,
                "resolved_date":          resolved_date,
                "status":                 status,
                "condition_type":         rng.choice(CONDITION_TYPES),
                "source_code_type":       "ICD-10-CM",
                "source_code":            icd_code,
                "source_description":     f"Diagnosis {icd_code}",
                "condition_rank":         j + 1,
                "present_on_admit_code":  rng.choice(["Y","N","U","W",None]) if j == 0 else None,
                "ingest_datetime":        load_dttm,
                "data_source":            DATA_SOURCE,
                "META_data_source":       META_DATA_SOURCE,
                "META_Load_DTTM":         load_dttm,
            })
    return pd.DataFrame(rows)


def _make_procedure_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """1-2 procedures per encounter."""
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id",
         "encounter_start_date", "encounter_end_date"]
    ].to_dict("records")
    procs_per_enc = rng.integers(1, 3, size=n_enc)

    for i, enc in enumerate(enc_records):
        n_proc      = int(procs_per_enc[i])
        cpt_indices = rng.integers(0, len(CPT_PROFESSIONAL), size=n_proc)
        prac_indices = rng.integers(0, N_PRACTITIONERS, size=n_proc)

        for j in range(n_proc):
            enc_dur = max(1, (enc["encounter_end_date"] - enc["encounter_start_date"]).days + 1)
            proc_date = enc["encounter_start_date"] + datetime.timedelta(
                days=int(rng.integers(0, enc_dur))
            )
            cpt_code = CPT_PROFESSIONAL[cpt_indices[j]]
            mod_draws = rng.integers(0, len(MOD_CODES), size=5)
            mods = [MOD_CODES[int(m)] for m in mod_draws]
            rows.append({
                "procedure_id":      f"{enc['encounter_id']}-P{j:02d}",
                "person_id":         enc["person_id"],
                "patient_id":        enc["patient_id"],
                "encounter_id":      enc["encounter_id"],
                "procedure_date":    proc_date,
                "source_code_type":  "CPT",
                "source_code":       cpt_code,
                "source_description": f"Procedure {cpt_code}",
                "modifier_1":        mods[0],
                "modifier_2":        mods[1],
                "modifier_3":        mods[2],
                "modifier_4":        mods[3],
                "modifier_5":        mods[4],
                "practitioner_id":   PRAC_IDS[prac_indices[j]],
                "ingest_datetime":   load_dttm,
                "data_source":       DATA_SOURCE,
                "META_data_source":  META_DATA_SOURCE,
                "META_Load_DTTM":    load_dttm,
            })
    return pd.DataFrame(rows)


def _make_lab_result_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    1-3 lab results per encounter. HbA1c drawn from Normal(TEST_HBAC1C_MEAN, 1.5)
    clipped to [4.5, 13.0] to preserve the drift signal.
    """
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id", "encounter_start_date"]
    ].to_dict("records")
    labs_per_enc = rng.integers(1, 4, size=n_enc)
    prac_indices = rng.integers(0, N_PRACTITIONERS, size=n_enc)

    for i, enc in enumerate(enc_records):
        n_labs     = int(labs_per_enc[i])
        lab_codes  = rng.choice(LOINC_LAB_CODES, size=n_labs, replace=True)
        prac_id    = PRAC_IDS[prac_indices[i]]
        enc_date   = enc["encounter_start_date"]

        for j in range(n_labs):
            loinc_code = lab_codes[j]
            desc, units, lo, hi = LOINC_LABS[loinc_code]

            if loinc_code == "4548-4":
                # HbA1c drift signal — TEST population has higher mean
                val_float  = float(np.clip(rng.normal(TEST_HBAC1C_MEAN, 1.5), 4.5, 13.0))
                result_str = f"{val_float:.1f}"
            else:
                val_float  = float(rng.uniform(lo, hi))
                result_str = f"{val_float:.2f}"

            abnormal = "Y" if (val_float < lo or val_float > hi) else "N"
            collect_dt = datetime.datetime.combine(
                enc_date - datetime.timedelta(days=int(rng.integers(0, 2))),
                datetime.time(int(rng.integers(6, 18)), 0),
            )
            result_dt = collect_dt + datetime.timedelta(hours=int(rng.integers(0, 3)))

            rows.append({
                "lab_result_id":                   f"{enc['encounter_id']}-L{j:02d}",
                "person_id":                       enc["person_id"],
                "patient_id":                      enc["patient_id"],
                "encounter_id":                    enc["encounter_id"],
                "accession_number":                f"ACC{rng.integers(100000, 999999)}",
                "source_order_type":               rng.choice(["laboratory","point-of-care","external"]),
                "source_order_code":               loinc_code,
                "source_order_description":        desc,
                "source_component_type":           "LOINC",
                "source_component_code":           loinc_code,
                "source_component_description":    desc,
                "status":                          "final",
                "result":                          result_str,
                "result_datetime":                 result_dt,
                "collection_datetime":             collect_dt,
                "source_units":                    units,
                "normalized_units":                units,
                "source_reference_range_low":      str(lo),
                "source_reference_range_high":     str(hi),
                "normalized_reference_range_low":  str(lo),
                "normalized_reference_range_high": str(hi),
                "source_abnormal_flag":            abnormal,
                "normalized_abnormal_flag":        abnormal,
                "specimen":                        rng.choice(["blood","serum","plasma","urine",None]),
                "ordering_practitioner_id":        prac_id,
                "ingest_datetime":                 load_dttm,
                "data_source":                     DATA_SOURCE,
                "META_data_source":                META_DATA_SOURCE,
                "META_Load_DTTM":                  load_dttm,
            })
    return pd.DataFrame(rows)


def _make_observation_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    2-4 vital-sign observations per encounter. BMI drawn from
    Normal(TEST_BMI_MEAN, 5.0) clipped to [15, 55] to preserve drift signal.
    """
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id", "encounter_start_date"]
    ].to_dict("records")
    obs_per_enc = rng.integers(2, 5, size=n_enc)

    for i, enc in enumerate(enc_records):
        n_obs       = int(obs_per_enc[i])
        vital_codes = rng.choice(LOINC_VITAL_CODES, size=n_obs, replace=True)
        enc_date    = enc["encounter_start_date"]
        obs_dt      = datetime.datetime.combine(enc_date, datetime.time(int(rng.integers(7, 17)), 0))
        panel_id    = f"PANEL-{enc['encounter_id']}"

        for j in range(n_obs):
            loinc_code = vital_codes[j]
            desc, units, lo, hi = LOINC_VITALS[loinc_code]

            if loinc_code == "39156-5":
                # BMI drift signal — TEST population has higher mean
                val_float  = float(np.clip(rng.normal(TEST_BMI_MEAN, 5.0), 15.0, 55.0))
                result_str = f"{val_float:.1f}"
            else:
                val_float  = float(rng.uniform(lo, hi))
                result_str = f"{val_float:.1f}"

            abnormal = "Y" if (val_float < lo or val_float > hi) else "N"

            rows.append({
                "observation_id":                  f"{enc['encounter_id']}-O{j:02d}",
                "person_id":                       enc["person_id"],
                "patient_id":                      enc["patient_id"],
                "encounter_id":                    enc["encounter_id"],
                "panel_id":                        panel_id,
                "observation_date":                enc_date,
                "observation_type":                "vital-signs",
                "source_code_type":                "LOINC",
                "source_code":                     loinc_code,
                "source_description":              desc,
                "result":                          result_str,
                "source_units":                    units,
                "normalized_units":                units,
                "source_reference_range_low":      str(lo),
                "source_reference_range_high":     str(hi),
                "normalized_reference_range_low":  str(lo),
                "normalized_reference_range_high": str(hi),
                "ingest_datetime":                 load_dttm,
                "data_source":                     DATA_SOURCE,
                "META_data_source":                META_DATA_SOURCE,
                "META_Load_DTTM":                  load_dttm,
            })
    return pd.DataFrame(rows)


def _make_medication_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """0-1 medications per encounter."""
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id",
         "encounter_start_date", "encounter_end_date"]
    ].to_dict("records")
    meds_per_enc = rng.integers(0, 2, size=n_enc)

    for i, enc in enumerate(enc_records):
        n_meds   = int(meds_per_enc[i])
        enc_date = enc["encounter_start_date"]

        for j in range(n_meds):
            ndc_i    = rng.integers(0, len(NDC_LIST))
            prac_i   = rng.integers(0, N_PRACTITIONERS)
            disp_dt  = enc_date + datetime.timedelta(days=int(rng.integers(0, 3)))
            presc_dt = enc_date - datetime.timedelta(days=int(rng.integers(0, 3)))

            rows.append({
                "medication_id":        f"{enc['encounter_id']}-MED{j:02d}",
                "person_id":            enc["person_id"],
                "patient_id":           enc["patient_id"],
                "encounter_id":         enc["encounter_id"],
                "dispensing_date":      disp_dt,
                "prescribing_date":     presc_dt,
                "source_code_type":     "NDC",
                "source_code":          NDC_LIST[ndc_i],
                "source_description":   NDC_NAMES[ndc_i],
                "ndc_code":             NDC_LIST[ndc_i],
                "rxnorm_code":          RXNORM[ndc_i % len(RXNORM)],
                "atc_code":             ATCODES[ndc_i % len(ATCODES)],
                "route":                rng.choice(ROUTES),
                "strength":             rng.choice(["10mg","25mg","50mg","100mg","500mg","1g","5mcg","20mg"]),
                "quantity":             float(rng.integers(30, 91)),
                "quantity_unit":        rng.choice(["tablet","capsule","mL","unit","inhaler"]),
                "days_supply":          float(rng.integers(30, 91)),
                "practitioner_id":      PRAC_IDS[prac_i],
                "ingest_datetime":      load_dttm,
                "data_source":          DATA_SOURCE,
                "META_data_source":     META_DATA_SOURCE,
                "META_Load_DTTM":       load_dttm,
            })
    return pd.DataFrame(rows)


def _make_appointment_rows(
    encounters_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """1 appointment per encounter."""
    rows: list[dict[str, Any]] = []
    n_enc = len(encounters_df)
    if n_enc == 0:
        return pd.DataFrame()

    enc_records = encounters_df[
        ["encounter_id", "person_id", "patient_id",
         "encounter_start_date", "encounter_end_date"]
    ].to_dict("records")
    prac_idx   = rng.integers(0, N_PRACTITIONERS, size=n_enc)
    loc_idx    = rng.integers(0, N_LOCATIONS, size=n_enc)
    type_idx   = rng.integers(0, len(APPT_TYPES), size=n_enc)
    status_idx = rng.integers(0, len(APPT_STATUSES), size=n_enc)
    dur_arr    = rng.integers(15, 61, size=n_enc)

    for i, enc in enumerate(enc_records):
        enc_date     = enc["encounter_start_date"]
        start_hour   = int(rng.integers(8, 17))
        appt_start   = datetime.datetime.combine(enc_date, datetime.time(start_hour, 0))
        duration_min = int(dur_arr[i])
        appt_end     = appt_start + datetime.timedelta(minutes=duration_min)
        status       = APPT_STATUSES[status_idx[i]]
        cancel_reason = (
            rng.choice(["patient request","provider unavailable","weather","other"])
            if status in ("canceled", "no-show") else None
        )
        appt_type = APPT_TYPES[type_idx[i]]

        rows.append({
            "appointment_id":      f"{enc['encounter_id']}-A",
            "person_id":           enc["person_id"],
            "patient_id":          enc["patient_id"],
            "encounter_id":        enc["encounter_id"],
            "start_datetime":      appt_start,
            "end_datetime":        appt_end,
            "duration":            duration_min,
            "location_id":         LOC_IDS[loc_idx[i]],
            "practitioner_id":     PRAC_IDS[prac_idx[i]],
            "type_code":           f"APPT-{appt_type.replace(' ','-').upper()[:12]}",
            "type_description":    appt_type,
            "status_code":         status.upper().replace("-","_"),
            "status_description":  status,
            "reason":              rng.choice(["routine","follow-up","sick","preventive","chronic-management",None]),
            "cancellation_reason": cancel_reason,
            "data_source":         DATA_SOURCE,
            "META_data_source":    META_DATA_SOURCE,
            "META_Load_DTTM":      load_dttm,
        })
    return pd.DataFrame(rows)


def _make_patient_rows(
    day: int,
    n_patients: int,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate n_patients new patient rows for a load day."""
    rows: list[dict[str, Any]] = []
    load_date = load_dttm.date()

    for i in range(n_patients):
        person_id  = f"PER-LOAD{day:02d}-{i:04d}"
        patient_id = f"PT-LOAD{day:02d}-{i:04d}"
        sex        = rng.choice(SEXES)
        fn         = rng.choice(FIRST_NAMES_F if sex == "female" else FIRST_NAMES_M)
        mn         = rng.choice(FIRST_NAMES_F + FIRST_NAMES_M) if rng.random() > 0.4 else None
        ln         = rng.choice(LAST_NAMES)
        state      = rng.choice(STATES)
        birth_year  = int(rng.integers(1939, 2006))
        birth_month = int(rng.integers(1, 13))
        birth_day   = int(rng.integers(1, 29))
        birth_date  = datetime.date(birth_year, birth_month, birth_day)

        rows.append({
            "person_id":              person_id,
            "patient_id":             patient_id,
            "first_name":             fn,
            "middle_name":            mn,
            "last_name":              ln,
            "name_suffix":            rng.choice(["Jr.","Sr.","II","III",None,None,None,None]),
            "sex":                    sex,
            "race":                   rng.choice(RACES),
            "ethnicity":              rng.choice(ETHNICITIES),
            "birth_date":             birth_date,
            "death_date":             None,
            "death_flag":             0,
            "social_security_number": f"{rng.integers(100,999)}-{rng.integers(10,99)}-{rng.integers(1000,9999)}",
            "address":                f"{rng.integers(100,9999)} Elm Ave",
            "city":                   f"City{rng.integers(1, 500):03d}",
            "state":                  state,
            "zip_code":               f"{rng.integers(10000,99999):05d}",
            "county":                 f"{state} County {rng.integers(1,20)}",
            "latitude":               round(float(rng.uniform(25.0, 48.0)), 6),
            "longitude":              round(float(rng.uniform(-124.0, -67.0)), 6),
            "phone":                  f"({rng.integers(200,999)}) {rng.integers(200,999)}-{rng.integers(1000,9999)}",
            "email":                  f"{fn.lower()}.{ln.lower()}{rng.integers(1,999)}@example.com",
            "ingest_datetime":        load_dttm,
            "data_source":            DATA_SOURCE,
            "META_data_source":       META_DATA_SOURCE,
            "META_Load_DTTM":         load_dttm,
        })
    return pd.DataFrame(rows)


def _make_eligibility_rows(
    new_patient_df: pd.DataFrame,
    load_dttm: datetime.datetime,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate one open-ended eligibility span for each new patient."""
    rows: list[dict[str, Any]] = []
    load_date = load_dttm.date()

    for rec in new_patient_df.to_dict("records"):
        person_id  = rec["person_id"]
        # member_id must match what medical_claim will derive for this person_id
        member_id  = person_id.replace("PER-", "MBR-", 1) if person_id.startswith("PER-") else f"MBR-{person_id}"
        sub_id     = member_id
        sex        = rec["sex"]
        fn         = rec["first_name"]
        mn         = rec.get("middle_name")
        ln         = rec["last_name"]
        state      = rec["state"]

        rows.append({
            "person_id":                    person_id,
            "member_id":                    member_id,
            "subscriber_id":                sub_id,
            "subscriber_relation":          "self",
            "enrollment_start_date":        load_date,
            "enrollment_end_date":          datetime.date(9999, 12, 31),
            "payer":                        PAYER,
            "payer_type":                   "commercial",
            "plan":                         PLAN,
            "first_name":                   fn,
            "middle_name":                  mn,
            "last_name":                    ln,
            "name_suffix":                  None,
            "social_security_number":       rec.get("social_security_number"),
            "address":                      rec.get("address"),
            "city":                         rec.get("city"),
            "state":                        state,
            "zip_code":                     rec.get("zip_code"),
            "phone":                        rec.get("phone"),
            "email":                        rec.get("email"),
            "ethnicity":                    rec.get("ethnicity"),
            "gender":                       sex,
            "race":                         rec.get("race"),
            "birth_date":                   rec.get("birth_date"),
            "death_date":                   None,
            "death_flag":                   0,
            "original_reason_entitlement_code": rng.choice(["0","1","2","3"]),
            "dual_status_code":             None,
            "medicare_status_code":         None,
            "enrollment_status":            "active",
            "hospice_flag":                 0,
            "institutional_snp_flag":       0,
            "medicaid_indicator":           0,
            "long_term_institutional_flag": 0,
            "part_d_raf_type":              None,
            "low_income_subsidy_indicator": 0,
            "metal_level":                  rng.choice(METAL_LEVELS),
            "csr_indicator":                None,
            "enrollment_duration_months":   1,
            "esrd_status":                  0,
            "transplant_duration_months":   None,
            "group_id":                     f"GRP{rng.integers(1000,9999)}",
            "group_name":                   rng.choice(["Employer A","Employer B","Employer C","Individual Market"]),
            "file_name":                    f"eligibility_{load_date.strftime('%Y%m%d')}.csv",
            "file_date":                    load_date,
            "ingest_datetime":              load_dttm,
            "data_source":                  DATA_SOURCE,
            "META_data_source":             META_DATA_SOURCE,
            "META_Load_DTTM":               load_dttm,
        })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Main simulation loop: 28 days

# COMMAND ----------

print("=" * 72)
print("PART 2: 28-DAY INCREMENTAL LOADS — TEST SCHEMAS ONLY")
print(f"  BASE_DATE              = {BASE_DATE}")
print(f"  DAILY_CLAIM_GROWTH_PCT = {DAILY_CLAIM_GROWTH_PCT:.4%}")
print(f"  NEW_PATIENT_DAYS       = {sorted(NEW_PATIENT_DAYS)}")
print("=" * 72)
print()

# Snapshot current row counts so growth percentages stay consistent across
# days (using the original base data, not the cumulative total).
_enc_base_count = spark.table(f"`{CATALOG}`.`{TEST_CLINICAL}`.`encounter`").count()
_rx_base_count  = spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`pharmacy_claim`").count()

print(f"Base encounter count  : {_enc_base_count:,}")
print(f"Base pharmacy count   : {_rx_base_count:,}")
print()

# Mutable person pool — starts from EXISTING_PERSON_IDS and grows as new
# patients are appended on NEW_PATIENT_DAYS.
current_person_ids: list[str] = list(EXISTING_PERSON_IDS)

# Accumulate daily summary for display at the end
daily_summary: list[dict[str, Any]] = []

for d in range(1, 29):
    rng = np.random.default_rng(seed=d)  # per-day seed for reproducibility

    load_dttm = datetime.datetime.combine(
        BASE_DATE + datetime.timedelta(days=d),
        datetime.time(2, 0, 0),  # simulate a 2 AM nightly ETL window
    )
    load_date = load_dttm.date()

    new_patient_count   = 0
    new_encounter_count = 0
    new_claim_count     = 0
    new_rx_count        = 0

    # ------------------------------------------------------------------
    # Step 1 — New patients (only on NEW_PATIENT_DAYS)
    # ------------------------------------------------------------------
    new_patients_df    = pd.DataFrame()
    new_patient_ids: list[str] = []

    if d in NEW_PATIENT_DAYS:
        new_patients_df = _make_patient_rows(d, NEW_PATIENTS_PER_OCCASION, load_dttm, rng)
        new_elig_df     = _make_eligibility_rows(new_patients_df, load_dttm, rng)

        write_delta_append(new_patients_df, CATALOG, TEST_MEMBERS, "patient")
        write_delta_append(new_elig_df,     CATALOG, TEST_MEMBERS, "eligibility")

        new_patient_ids   = new_patients_df["person_id"].tolist()
        current_person_ids.extend(new_patient_ids)
        new_patient_count = len(new_patients_df)

    # ------------------------------------------------------------------
    # Step 2 — New encounters
    # Growth is relative to the fixed base count to avoid compounding drift.
    # Blend ~30 % from new patients (if any) with 70 % from existing pool.
    # ------------------------------------------------------------------
    n_new_enc = max(1, round(_enc_base_count * DAILY_CLAIM_GROWTH_PCT))

    if new_patient_ids:
        n_from_new      = max(0, round(n_new_enc * 0.30))
        n_from_existing = n_new_enc - n_from_new
        # Encounters for new patients
        enc_new_df = _make_encounter_rows(d, new_patient_ids, n_from_new, load_dttm, rng)
        # Encounters for existing patients
        enc_exist_df = _make_encounter_rows(d, current_person_ids, n_from_existing, load_dttm, rng)
        new_enc_df = pd.concat([enc_new_df, enc_exist_df], ignore_index=True)
    else:
        new_enc_df = _make_encounter_rows(d, current_person_ids, n_new_enc, load_dttm, rng)

    write_delta_append(new_enc_df, CATALOG, TEST_CLINICAL, "encounter")
    new_encounter_count = len(new_enc_df)

    # ------------------------------------------------------------------
    # Step 3 — Medical claims (from new encounters)
    # ------------------------------------------------------------------
    new_claims_df = _make_medical_claim_rows(new_enc_df, load_dttm, rng)
    write_delta_append(new_claims_df, CATALOG, TEST_CLAIMS, "medical_claim")
    new_claim_count = len(new_claims_df)

    # ------------------------------------------------------------------
    # Step 4 — Proportionate clinical data for each new encounter
    # ------------------------------------------------------------------
    cond_df  = _make_condition_rows(new_enc_df, load_dttm, rng)
    proc_df  = _make_procedure_rows(new_enc_df, load_dttm, rng)
    lab_df   = _make_lab_result_rows(new_enc_df, load_dttm, rng)
    obs_df   = _make_observation_rows(new_enc_df, load_dttm, rng)
    med_df   = _make_medication_rows(new_enc_df, load_dttm, rng)
    appt_df  = _make_appointment_rows(new_enc_df, load_dttm, rng)

    write_delta_append(cond_df,  CATALOG, TEST_CLINICAL, "condition")
    write_delta_append(proc_df,  CATALOG, TEST_CLINICAL, "procedure")
    write_delta_append(lab_df,   CATALOG, TEST_CLINICAL, "lab_result")
    write_delta_append(obs_df,   CATALOG, TEST_CLINICAL, "observation")
    write_delta_append(med_df,   CATALOG, TEST_CLINICAL, "medication")
    write_delta_append(appt_df,  CATALOG, TEST_CLINICAL, "appointment")

    # ------------------------------------------------------------------
    # Step 5 — Pharmacy claims (independent of encounters)
    # ------------------------------------------------------------------
    n_new_rx   = max(1, round(_rx_base_count * DAILY_PHARMACY_GROWTH_PCT))
    new_rx_df  = _make_pharmacy_claim_rows(n_new_rx, current_person_ids, load_dttm, rng)
    write_delta_append(new_rx_df, CATALOG, TEST_CLAIMS, "pharmacy_claim")
    new_rx_count = len(new_rx_df)

    # Progress report
    print(
        f"Day {d:>2}/28  {load_date}  |"
        f"  {new_patient_count:>2} new pts  |"
        f"  {new_encounter_count:>3} encounters  |"
        f"  {new_claim_count:>5} claim lines  |"
        f"  {new_rx_count:>3} Rx claims"
    )

    daily_summary.append({
        "day":                  d,
        "load_date":            load_date,
        "new_patients":         new_patient_count,
        "new_encounters":       new_encounter_count,
        "new_medical_claims":   new_claim_count,
        "new_pharmacy_claims":  new_rx_count,
        "new_conditions":       len(cond_df),
        "new_procedures":       len(proc_df),
        "new_lab_results":      len(lab_df),
        "new_observations":     len(obs_df),
        "new_medications":      len(med_df),
        "new_appointments":     len(appt_df),
    })

print()
print("Part 2 (TEST) complete — 28-day simulation finished.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 2b: 28-Day Incremental Loads — PROD Schemas
# MAGIC
# MAGIC Runs the same simulation for `dev.prod_main_*` using the PROD baseline
# MAGIC distribution (lower charges, lower denial rate, lower HbA1c).
# MAGIC Skipped when `RUN_PROD = False`.

# COMMAND ----------

if RUN_PROD:
    # Override the TEST_* constants in the global namespace so the generator
    # functions (which reference them by name) use PROD parameters.
    TEST_CHARGE_MU         = PROD_CHARGE_MU
    TEST_CHARGE_SIGMA      = PROD_CHARGE_SIGMA
    TEST_DENIAL_RATE       = PROD_DENIAL_RATE
    TEST_PROFESSIONAL_RATE = PROD_PROFESSIONAL_RATE
    TEST_HBAC1C_MEAN       = PROD_HBAC1C_MEAN
    TEST_BMI_MEAN          = PROD_BMI_MEAN

    print("=" * 72)
    print("PART 2b: 28-DAY INCREMENTAL LOADS — PROD SCHEMAS")
    print(f"  Charge mu={TEST_CHARGE_MU}, denial={TEST_DENIAL_RATE:.0%}, HbA1c mean={TEST_HBAC1C_MEAN}")
    print("=" * 72)
    print()

    _prod_enc_base = spark.table(f"`{CATALOG}`.`{PROD_CLINICAL}`.`encounter`").count()
    _prod_rx_base  = spark.table(f"`{CATALOG}`.`{PROD_CLAIMS}`.`pharmacy_claim`").count()

    prod_person_ids: list[str] = (
        spark.table(f"`{CATALOG}`.`{PROD_MEMBERS}`.`patient`")
        .select("person_id").limit(MAX_SAMPLE)
        .rdd.flatMap(lambda r: [r[0]]).collect()
    )
    current_prod_person_ids = list(prod_person_ids)

    prod_daily_summary: list[dict] = []

    for d in range(1, 29):
        rng = np.random.default_rng(seed=d + 100)   # offset seed so PROD differs from TEST

        load_dttm = datetime.datetime.combine(
            BASE_DATE + datetime.timedelta(days=d),
            datetime.time(2, 0, 0),
        )

        new_pt_count = new_enc_count = new_clm_count = new_rx_count = 0

        # New patients
        new_prod_patient_ids: list[str] = []
        if d in NEW_PATIENT_DAYS:
            np_df  = _make_patient_rows(d + 100, NEW_PATIENTS_PER_OCCASION, load_dttm, rng)
            ne_df  = _make_eligibility_rows(np_df, load_dttm, rng)
            write_delta_append(np_df, CATALOG, PROD_MEMBERS, "patient")
            write_delta_append(ne_df, CATALOG, PROD_MEMBERS, "eligibility")
            new_prod_patient_ids = np_df["person_id"].tolist()
            current_prod_person_ids.extend(new_prod_patient_ids)
            new_pt_count = len(np_df)

        # New encounters
        n_enc = max(1, round(_prod_enc_base * DAILY_CLAIM_GROWTH_PCT))
        if new_prod_patient_ids:
            n_new = max(0, round(n_enc * 0.30))
            enc_df = pd.concat([
                _make_encounter_rows(d + 100, new_prod_patient_ids, n_new, load_dttm, rng),
                _make_encounter_rows(d + 100, current_prod_person_ids, n_enc - n_new, load_dttm, rng),
            ], ignore_index=True)
        else:
            enc_df = _make_encounter_rows(d + 100, current_prod_person_ids, n_enc, load_dttm, rng)
        write_delta_append(enc_df, CATALOG, PROD_CLINICAL, "encounter")
        new_enc_count = len(enc_df)

        # Claims + clinical
        clm_df  = _make_medical_claim_rows(enc_df, load_dttm, rng)
        write_delta_append(clm_df, CATALOG, PROD_CLAIMS, "medical_claim")
        new_clm_count = len(clm_df)

        for fn, tbl in [
            (_make_condition_rows,    ("condition",    PROD_CLINICAL)),
            (_make_procedure_rows,    ("procedure",    PROD_CLINICAL)),
            (_make_lab_result_rows,   ("lab_result",   PROD_CLINICAL)),
            (_make_observation_rows,  ("observation",  PROD_CLINICAL)),
            (_make_medication_rows,   ("medication",   PROD_CLINICAL)),
            (_make_appointment_rows,  ("appointment",  PROD_CLINICAL)),
        ]:
            write_delta_append(fn(enc_df, load_dttm, rng), CATALOG, tbl[1], tbl[0])

        # Pharmacy
        n_rx   = max(1, round(_prod_rx_base * DAILY_PHARMACY_GROWTH_PCT))
        rx_df  = _make_pharmacy_claim_rows(n_rx, current_prod_person_ids, load_dttm, rng)
        write_delta_append(rx_df, CATALOG, PROD_CLAIMS, "pharmacy_claim")
        new_rx_count = len(rx_df)

        print(
            f"Day {d:>2}/28  {load_dttm.date()}  |"
            f"  {new_pt_count:>2} new pts  |"
            f"  {new_enc_count:>3} encounters  |"
            f"  {new_clm_count:>5} claim lines  |"
            f"  {new_rx_count:>3} Rx claims"
        )

    # Restore TEST constants for any subsequent cells
    TEST_CHARGE_MU         = 7.2
    TEST_CHARGE_SIGMA      = 0.85
    TEST_DENIAL_RATE       = 0.20
    TEST_PROFESSIONAL_RATE = 0.55
    TEST_HBAC1C_MEAN       = 8.1
    TEST_BMI_MEAN          = 29.2

    print()
    print("Part 2b (PROD) complete — 28-day simulation finished.")
else:
    print("RUN_PROD = False — skipping PROD incremental loads.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Part 3: Validation

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.1 Daily summary (from in-memory accumulator)

# COMMAND ----------

daily_summary_df = spark.createDataFrame(daily_summary)
display(daily_summary_df.orderBy("day"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.2 Medical claim row count by META_Load_DTTM date

# COMMAND ----------

print("Medical claim rows grouped by META_Load_DTTM date (TEST side):")

mc_by_day = (
    spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`medical_claim`")
    .filter(F.col("META_Load_DTTM").isNotNull())
    .groupBy(F.to_date("META_Load_DTTM").alias("load_date"))
    .agg(
        F.count("*").alias("claim_lines"),
        F.countDistinct("claim_id").alias("distinct_claims"),
    )
    .orderBy("load_date")
)
display(mc_by_day)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.3 Total growth: base vs post-simulation

# COMMAND ----------

print("Total row counts — TEST tables (before vs after simulation):")

growth_tables = [
    (TEST_CLAIMS,   "medical_claim"),
    (TEST_CLAIMS,   "pharmacy_claim"),
    (TEST_CLINICAL, "encounter"),
    (TEST_CLINICAL, "condition"),
    (TEST_CLINICAL, "procedure"),
    (TEST_CLINICAL, "lab_result"),
    (TEST_CLINICAL, "observation"),
    (TEST_CLINICAL, "medication"),
    (TEST_CLINICAL, "appointment"),
    (TEST_MEMBERS,  "patient"),
    (TEST_MEMBERS,  "eligibility"),
]

growth_rows = []
for schema, table in growth_tables:
    full_name  = f"`{CATALOG}`.`{schema}`.`{table}`"
    total_cnt  = spark.table(full_name).count()
    base_cnt   = (
        spark.table(full_name)
        .filter(F.col("META_Load_DTTM") == F.to_timestamp(F.date_sub(F.current_date(), 28)))
        .count()
    )
    incr_cnt   = total_cnt - base_cnt
    pct_growth = round(100.0 * incr_cnt / base_cnt, 2) if base_cnt > 0 else None
    growth_rows.append({
        "schema":           schema,
        "table":            table,
        "base_rows":        base_cnt,
        "incremental_rows": incr_cnt,
        "total_rows":       total_cnt,
        "growth_pct":       pct_growth,
    })
    print(
        f"  {schema:<28s}  {table:<20s}  "
        f"base={base_cnt:>8,}  incr={incr_cnt:>7,}  "
        f"total={total_cnt:>9,}  growth={pct_growth}%"
    )

growth_sdf = spark.createDataFrame(growth_rows)
display(growth_sdf)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.4 New patient count per load date

# COMMAND ----------

print("New patient count by load date (TEST side):")

new_pt_by_day = (
    spark.table(f"`{CATALOG}`.`{TEST_MEMBERS}`.`patient`")
    .filter(F.col("person_id").like("PER-LOAD%"))
    .groupBy(F.to_date("META_Load_DTTM").alias("load_date"))
    .agg(F.count("*").alias("new_patients"))
    .orderBy("load_date")
)
display(new_pt_by_day)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.5 HbA1c mean by week — drift continues across incremental loads

# COMMAND ----------

print("HbA1c mean by calendar week (TEST lab_result, LOINC 4548-4):")
print("Expected: mean consistently near 8.1 across all weeks (TEST drift preserved).")
print()

hba1c_by_week = (
    spark.table(f"`{CATALOG}`.`{TEST_CLINICAL}`.`lab_result`")
    .filter(F.col("source_component_code") == "4548-4")
    .withColumn("load_week", F.weekofyear(F.to_date("META_Load_DTTM")))
    .withColumn("load_year",  F.year(F.to_date("META_Load_DTTM")))
    .groupBy("load_year", "load_week")
    .agg(
        F.round(F.mean(F.col("result").cast("double")), 3).alias("mean_hba1c"),
        F.count("*").alias("n_records"),
    )
    .orderBy("load_year", "load_week")
)
display(hba1c_by_week)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 3.6 Charge amount drift — incremental data preserves TEST distribution

# COMMAND ----------

print("Charge amount statistics across incremental loads (TEST medical_claim):")
print("Expected mean near exp(7.2 + 0.85^2/2) ≈ $2,150 (vs PROD ~$1,600)")
print()

charge_stats = (
    spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`medical_claim`")
    .filter(F.col("META_Load_DTTM").isNotNull())
    .groupBy(F.to_date("META_Load_DTTM").alias("load_date"))
    .agg(
        F.round(F.mean("charge_amount"), 2).alias("mean_charge"),
        F.round(F.stddev("charge_amount"), 2).alias("std_charge"),
        F.count("*").alias("n_lines"),
    )
    .orderBy("load_date")
)
display(charge_stats)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC ### Summary
# MAGIC
# MAGIC | What was done | Details |
# MAGIC |---------------|---------|
# MAGIC | META columns added | All 38 tables (PROD + TEST, all schemas) |
# MAGIC | Incremental days simulated | 28 (TEST schemas only) |
# MAGIC | New patients added | 4 batches × 10 = 40 new persons (days 5, 12, 19, 26) |
# MAGIC | Daily encounter growth | ~37 encounters/day (~0.15 % of 25 000 base) |
# MAGIC | Drift signals preserved | charge_amount lognormal(7.2, 0.85), HbA1c Normal(8.1, 1.5), BMI Normal(29.2, 5.0) |
# MAGIC
# MAGIC ### Next steps
# MAGIC
# MAGIC 1. Point the Synaptiq A/B Profiler at:
# MAGIC    - Side A: `dev.prod_main_claims.medical_claim` (static PROD baseline)
# MAGIC    - Side B: `dev.test_main_claims.medical_claim` (grown TEST + time-series)
# MAGIC 2. The `META_Load_DTTM` column enables time-sliced profiling — filter Side B
# MAGIC    to a specific date window for point-in-time comparisons.
# MAGIC 3. PROD tables are unchanged — rerun `generate_tuva_input_layer.py` to reset
# MAGIC    the TEST baseline, then rerun this notebook to regenerate the simulation.
