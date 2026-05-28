# Databricks notebook source

# MAGIC %md
# MAGIC # Tuva Project Input Layer — Synthetic POC Data Generator
# MAGIC
# MAGIC Generates **all 15 Tuva Input Layer tables** as Delta tables in the `dev` catalog,
# MAGIC across two environments:
# MAGIC
# MAGIC | Environment | Catalog schemas | Purpose |
# MAGIC |-------------|-----------------|---------|
# MAGIC | **PROD** | `dev.prod_main_claims`, `dev.prod_main_clinical`, `dev.prod_main_members` | Baseline population |
# MAGIC | **TEST** | `dev.test_main_claims`, `dev.test_main_clinical`, `dev.test_main_members` | Drifted population |
# MAGIC
# MAGIC ## Tables generated (15 total)
# MAGIC
# MAGIC **Claims** (`*_claims`): `medical_claim`, `pharmacy_claim`, `provider_attribution`
# MAGIC
# MAGIC **Clinical** (`*_clinical`): `encounter`, `condition`, `procedure`, `lab_result`,
# MAGIC `observation`, `medication`, `immunization`, `appointment`
# MAGIC
# MAGIC **Members** (`*_members`): `eligibility`, `patient`
# MAGIC
# MAGIC **Reference** (both schemas): `location`, `practitioner`
# MAGIC
# MAGIC ## Intentional drift (PROD vs TEST)
# MAGIC
# MAGIC | Signal | PROD | TEST | Expected PSI |
# MAGIC |--------|------|------|--------------|
# MAGIC | `charge_amount` | lognormal(mu=6.9, σ=0.80) | lognormal(mu=7.2, σ=0.85) | ~0.13 moderate |
# MAGIC | Denial rate | 15% | 20% | — |
# MAGIC | `claim_type` mix | PROFESSIONAL 60% | PROFESSIONAL 55% | — |
# MAGIC | HbA1c mean | 7.2 | 8.1 | sicker TEST pop |
# MAGIC | BMI mean | 27.5 | 29.2 | higher TEST pop |
# MAGIC | Date range | 2024-01-01 to 2024-12-31 | 2024-07-01 to 2025-03-31 | — |
# MAGIC
# MAGIC ## Population sizes
# MAGIC - 5,000 persons, 200 practitioners, 50 locations
# MAGIC - 25,000 encounters per side
# MAGIC - ~50,000 medical claim lines, ~3,000 pharmacy claims per side
# MAGIC - ~75,000 conditions, ~50,000 procedures, ~50,000 lab results per side
# MAGIC - ~75,000 observations, ~20,000 medications, ~15,000 immunizations per side
# MAGIC - ~25,000 appointments per side

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Imports and Configuration

# COMMAND ----------

from __future__ import annotations

import uuid
import math
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
# Population sizes
# ---------------------------------------------------------------------------
N_PERSONS       = 5_000
N_PRACTITIONERS = 200
N_LOCATIONS     = 50
N_ENCOUNTERS    = 25_000

# ---------------------------------------------------------------------------
# Drift parameters
# ---------------------------------------------------------------------------
PROD_CHARGE_MU    = 6.9
PROD_CHARGE_SIGMA = 0.80
TEST_CHARGE_MU    = 7.2
TEST_CHARGE_SIGMA = 0.85

PROD_DENIAL_RATE  = 0.15
TEST_DENIAL_RATE  = 0.20

PROD_PROFESSIONAL_RATE = 0.60
TEST_PROFESSIONAL_RATE = 0.55

PROD_HBAC1C_MEAN = 7.2
TEST_HBAC1C_MEAN = 8.1

PROD_BMI_MEAN = 27.5
TEST_BMI_MEAN = 29.2

PROD_DATE_START = datetime.date(2024,  1,  1)
PROD_DATE_END   = datetime.date(2024, 12, 31)
TEST_DATE_START = datetime.date(2024,  7,  1)
TEST_DATE_END   = datetime.date(2025,  3, 31)

# ---------------------------------------------------------------------------
# Fixed seeds (deterministic reruns)
# ---------------------------------------------------------------------------
SEED_SHARED   = 42
SEED_PROD     = 100
SEED_TEST     = 200

DATA_SOURCE = "SynaptiqPOC"
PAYER       = "Aetna"
PLAN        = "Aetna PPO"

print("Config loaded.")
print(f"  N_PERSONS={N_PERSONS}, N_PRACTITIONERS={N_PRACTITIONERS}, N_LOCATIONS={N_LOCATIONS}")
print(f"  N_ENCOUNTERS={N_ENCOUNTERS}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Reference Data (Codes)

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
NDC_LIST      = list(NDC_DRUGS.keys())
NDC_NAMES     = list(NDC_DRUGS.values())

CVX_VACCINES: dict[str, str] = {
    "158": "Influenza",
    "207": "COVID-19 Moderna",
    "208": "COVID-19 Pfizer",
    "9":   "Td adult",
    "3":   "MMR",
    "33":  "Pneumococcal",
    "43":  "Hepatitis B",
}
CVX_CODES = list(CVX_VACCINES.keys())
CVX_NAMES = list(CVX_VACCINES.values())

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

STATES = [
    "CA","TX","FL","NY","PA","IL","OH","GA","NC","MI",
    "NJ","VA","WA","AZ","MA",
]

SPECIALTIES = [
    "Family Medicine","Internal Medicine","Cardiology","Orthopedic Surgery",
    "Emergency Medicine","Psychiatry","Radiology","Gastroenterology",
    "Neurology","Endocrinology",
]

FIRST_NAMES_F = ["Emma","Olivia","Ava","Isabella","Sophia","Mia","Charlotte","Amelia","Harper","Evelyn",
                  "Abigail","Emily","Elizabeth","Sofia","Avery","Ella","Scarlett","Grace","Victoria","Riley"]
FIRST_NAMES_M = ["Liam","Noah","William","James","Oliver","Benjamin","Elijah","Lucas","Mason","Logan",
                  "Alexander","Ethan","Daniel","Jacob","Michael","Henry","Jackson","Sebastian","Aiden","Matthew"]
LAST_NAMES    = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
                  "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
                  "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
                  "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores"]

RACES      = ["White","Black or African American","Asian","American Indian or Alaska Native","Other","Unknown"]
ETHNICITIES = ["Not Hispanic or Latino","Hispanic or Latino","Unknown"]
SEXES      = ["male","female"]

ENCOUNTER_TYPES  = ["outpatient","inpatient","emergency","office visit","telehealth","observation"]
ADMIT_SOURCE     = ["1","2","4","5","7","8","9"]
ADMIT_TYPE       = ["1","2","3","4","9"]
DISCH_DISP       = ["01","02","03","04","05","07","20","30","43","51"]
POS_CODES        = ["11","21","22","23","24","31","32","99"]
BILL_TYPES       = ["111","112","121","122","131","132","211","212"]
REVENUE_CODES    = ["0100","0110","0120","0200","0210","0250","0300","0370","0450","0636"]
DRG_CODES        = ["291","292","293","194","195","196","470","871","872","378","379","380","247","248","249"]

CONDITION_STATUSES = ["active","active","active","active","active","active",  # 60%
                       "resolved","resolved","resolved",                        # 30%
                       "historical"]                                            # 10%
CONDITION_TYPES    = ["encounter-diagnosis","problem-list-item","health-concern"]

APPT_STATUSES     = (["completed"]*14 + ["canceled"]*3 + ["no-show"]*2 + ["scheduled"]*1)
APPT_TYPES        = ["Follow-up","New Patient","Annual Wellness","Sick Visit","Procedure","Telehealth","Consultation"]

MOD_CODES = ["25","26","59","TC","GT","95","GX","GY",None,None,None,None]  # sparse

ROUTES     = ["oral","intravenous","intramuscular","subcutaneous","inhaled","topical","sublingual"]
BODY_SITES = ["left arm","right arm","left thigh","right thigh","deltoid","abdomen",None]

print("Reference data loaded.")
print(f"  ICD10={len(ICD10_CODES)}, NDC={len(NDC_LIST)}, CVX={len(CVX_CODES)}")
print(f"  LOINC labs={len(LOINC_LAB_CODES)}, vitals={len(LOINC_VITAL_CODES)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Shared Entity IDs

# COMMAND ----------

_rng_shared = np.random.default_rng(SEED_SHARED)

def _make_ids(n: int, prefix: str) -> list[str]:
    """Generate n deterministic UUIDs with a readable prefix."""
    return [f"{prefix}-{str(uuid.UUID(int=int.from_bytes(_rng_shared.bytes(16), 'big')))}"
            for _ in range(n)]

# Person / member / patient IDs (1:1:1 mapping)
PERSON_IDS  = [f"P{i:06d}" for i in range(1, N_PERSONS + 1)]
MEMBER_IDS  = [f"M{i:06d}" for i in range(1, N_PERSONS + 1)]
PATIENT_IDS = [f"PT{i:06d}" for i in range(1, N_PERSONS + 1)]

PERSON_TO_MEMBER  = dict(zip(PERSON_IDS, MEMBER_IDS))
PERSON_TO_PATIENT = dict(zip(PERSON_IDS, PATIENT_IDS))

# Practitioners
PRAC_IDS  = [f"PR{i:05d}" for i in range(1, N_PRACTITIONERS + 1)]
PRAC_NPIS = [f"{1000000000 + i}" for i in range(1, N_PRACTITIONERS + 1)]
PRAC_NPI_MAP = dict(zip(PRAC_IDS, PRAC_NPIS))

# Locations
LOC_IDS  = [f"L{i:04d}" for i in range(1, N_LOCATIONS + 1)]
LOC_NPIS = [f"{1500000000 + i}" for i in range(1, N_LOCATIONS + 1)]

# Subscriber IDs (same as member for simplicity)
SUBSCRIBER_IDS = MEMBER_IDS

print(f"Shared IDs generated:")
print(f"  Persons={len(PERSON_IDS)}, Practitioners={len(PRAC_IDS)}, Locations={len(LOC_IDS)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Helper: write_delta

# COMMAND ----------

def write_delta(pdf: pd.DataFrame, catalog: str, schema: str, table: str) -> None:
    """
    Write a pandas DataFrame as a Delta table using saveAsTable.

    Overwrites the table and schema each call (idempotent for reruns).
    Prints row count after write.
    """
    full_name = f"`{catalog}`.`{schema}`.`{table}`"
    sdf = spark.createDataFrame(pdf)
    (sdf.write
        .format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(full_name))
    count = spark.table(full_name).count()
    print(f"  Wrote {count:>10,} rows -> {full_name}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Static Generator Functions (no drift parameters)

# COMMAND ----------

def gen_location() -> pd.DataFrame:
    """
    Generate N_LOCATIONS location rows.
    Static — same data written to both PROD and TEST.
    """
    rng = np.random.default_rng(SEED_SHARED + 10)
    rows = []
    facility_types = ["hospital","clinic","urgent care","lab","imaging center","pharmacy","SNF"]
    orgs = ["Regional Health System","Community Medical Center","National Labs","Radiology Partners",
            "AmeriHealth","ValuMed Group","StatCare Network"]

    for i, (loc_id, npi) in enumerate(zip(LOC_IDS, LOC_NPIS)):
        state = rng.choice(STATES)
        rows.append({
            "location_id":          loc_id,
            "npi":                  npi,
            "name":                 f"Location Facility {i+1:03d}",
            "facility_type":        rng.choice(facility_types),
            "parent_organization":  rng.choice(orgs),
            "address":              f"{rng.integers(100, 9999)} Main St",
            "city":                 f"City{i+1:03d}",
            "state":                state,
            "zip_code":             f"{rng.integers(10000, 99999):05d}",
            "latitude":             round(float(rng.uniform(25.0, 48.0)), 6),
            "longitude":            round(float(rng.uniform(-124.0, -67.0)), 6),
            "data_source":          DATA_SOURCE,
        })
    return pd.DataFrame(rows)


def gen_practitioner() -> pd.DataFrame:
    """
    Generate N_PRACTITIONERS practitioner rows.
    Static — same data written to both PROD and TEST.
    """
    rng = np.random.default_rng(SEED_SHARED + 20)
    rows = []
    sub_specialties = ["General","Interventional","Pediatric","Geriatric","Sports Medicine",None,None]
    affiliations = ["Regional Health System","Community Medical Center","Solo Practice",
                    "ValuMed Group","StatCare Network"]

    for prac_id, npi in zip(PRAC_IDS, PRAC_NPIS):
        sex = rng.choice(SEXES)
        fn  = rng.choice(FIRST_NAMES_F if sex == "female" else FIRST_NAMES_M)
        ln  = rng.choice(LAST_NAMES)
        rows.append({
            "practitioner_id":    prac_id,
            "npi":                npi,
            "first_name":         fn,
            "last_name":          ln,
            "practice_affiliation": rng.choice(affiliations),
            "specialty":          rng.choice(SPECIALTIES),
            "sub_specialty":      rng.choice(sub_specialties),
            "data_source":        DATA_SOURCE,
        })
    return pd.DataFrame(rows)


def gen_patient() -> pd.DataFrame:
    """
    Generate N_PERSONS patient rows.
    Static — same data written to both PROD and TEST.
    """
    rng = np.random.default_rng(SEED_SHARED + 30)
    rows = []
    now_ts = datetime.datetime.utcnow()

    for person_id, patient_id in zip(PERSON_IDS, PATIENT_IDS):
        sex   = rng.choice(SEXES)
        fn    = rng.choice(FIRST_NAMES_F if sex == "female" else FIRST_NAMES_M)
        mn    = rng.choice(FIRST_NAMES_F + FIRST_NAMES_M) if rng.random() > 0.4 else None
        ln    = rng.choice(LAST_NAMES)
        state = rng.choice(STATES)
        # Birth date: ages 18-85
        birth_year  = int(rng.integers(1939, 2006))
        birth_month = int(rng.integers(1, 13))
        birth_day   = int(rng.integers(1, 29))
        birth_date  = datetime.date(birth_year, birth_month, birth_day)
        death_flag  = int(rng.random() < 0.02)
        death_date  = (datetime.date(2024, int(rng.integers(1, 13)), int(rng.integers(1, 29)))
                       if death_flag else None)
        rows.append({
            "person_id":             person_id,
            "patient_id":            patient_id,
            "first_name":            fn,
            "middle_name":           mn,
            "last_name":             ln,
            "name_suffix":           rng.choice(["Jr.","Sr.","II","III",None,None,None,None]),
            "sex":                   sex,
            "race":                  rng.choice(RACES),
            "ethnicity":             rng.choice(ETHNICITIES),
            "birth_date":            birth_date,
            "death_date":            death_date,
            "death_flag":            death_flag,
            "social_security_number": f"{rng.integers(100,999)}-{rng.integers(10,99)}-{rng.integers(1000,9999)}",
            "address":               f"{rng.integers(100,9999)} Elm Ave",
            "city":                  f"City{rng.integers(1, 500):03d}",
            "state":                 state,
            "zip_code":              f"{rng.integers(10000,99999):05d}",
            "county":                f"{state} County {rng.integers(1,20)}",
            "latitude":              round(float(rng.uniform(25.0, 48.0)), 6),
            "longitude":             round(float(rng.uniform(-124.0, -67.0)), 6),
            "phone":                 f"({rng.integers(200,999)}) {rng.integers(200,999)}-{rng.integers(1000,9999)}",
            "email":                 f"{fn.lower()}.{ln.lower()}{rng.integers(1,999)}@example.com",
            "ingest_datetime":       now_ts,
            "data_source":           DATA_SOURCE,
        })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Eligibility and Encounter Generators

# COMMAND ----------

def gen_eligibility(date_start: datetime.date, date_end: datetime.date, seed: int) -> pd.DataFrame:
    """
    Generate one eligibility span per person (~N_PERSONS rows).
    date_start / date_end define the enrollment window.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []

    metal_levels       = ["bronze","silver","gold","platinum","catastrophic"]
    entitlement_codes  = ["0","1","2","3"]
    dual_status_codes  = ["NA","02","04","08",None]
    medicare_codes     = ["10","20","31","11","21",None]
    enrollment_statuses = ["active","inactive","pending","termed"]

    date_range_days = (date_end - date_start).days

    for person_id, member_id, sub_id in zip(PERSON_IDS, MEMBER_IDS, SUBSCRIBER_IDS):
        enroll_start = date_start + datetime.timedelta(days=int(rng.integers(0, max(1, date_range_days // 4))))
        enroll_end   = date_end   - datetime.timedelta(days=int(rng.integers(0, max(1, date_range_days // 4))))
        if enroll_end <= enroll_start:
            enroll_end = enroll_start + datetime.timedelta(days=30)
        duration_months = max(1, round((enroll_end - enroll_start).days / 30.4))

        sex   = rng.choice(SEXES)
        fn    = rng.choice(FIRST_NAMES_F if sex == "female" else FIRST_NAMES_M)
        mn    = rng.choice(FIRST_NAMES_F + FIRST_NAMES_M) if rng.random() > 0.5 else None
        ln    = rng.choice(LAST_NAMES)
        state = rng.choice(STATES)

        death_flag = int(rng.random() < 0.02)
        death_date = (enroll_end - datetime.timedelta(days=int(rng.integers(0, 30)))
                      if death_flag else None)

        file_date = date_start + datetime.timedelta(days=int(rng.integers(0, 10)))

        rows.append({
            "person_id":                    person_id,
            "member_id":                    member_id,
            "subscriber_id":                sub_id,
            "subscriber_relation":          rng.choice(["self","spouse","child","other"]),
            "enrollment_start_date":        enroll_start,
            "enrollment_end_date":          enroll_end,
            "payer":                        PAYER,
            "payer_type":                   "commercial",
            "plan":                         PLAN,
            "first_name":                   fn,
            "middle_name":                  mn,
            "last_name":                    ln,
            "name_suffix":                  rng.choice(["Jr.","Sr.",None,None,None]),
            "social_security_number":       f"{rng.integers(100,999)}-{rng.integers(10,99)}-{rng.integers(1000,9999)}",
            "address":                      f"{rng.integers(100,9999)} Oak Blvd",
            "city":                         f"City{rng.integers(1,500):03d}",
            "state":                        state,
            "zip_code":                     f"{rng.integers(10000,99999):05d}",
            "phone":                        f"({rng.integers(200,999)}) {rng.integers(200,999)}-{rng.integers(1000,9999)}",
            "email":                        f"{fn.lower()}.{ln.lower()}{rng.integers(1,999)}@example.com",
            "ethnicity":                    rng.choice(ETHNICITIES),
            "gender":                       sex,
            "race":                         rng.choice(RACES),
            "birth_date":                   (date_start - datetime.timedelta(days=int(rng.integers(18*365, 85*365)))),
            "death_date":                   death_date,
            "death_flag":                   death_flag,
            "original_reason_entitlement_code": rng.choice(entitlement_codes),
            "dual_status_code":             rng.choice(dual_status_codes),
            "medicare_status_code":         rng.choice(medicare_codes),
            "enrollment_status":            rng.choice(enrollment_statuses),
            "hospice_flag":                 int(rng.random() < 0.01),
            "institutional_snp_flag":       int(rng.random() < 0.02),
            "medicaid_indicator":           int(rng.random() < 0.15),
            "long_term_institutional_flag": int(rng.random() < 0.01),
            "part_d_raf_type":              rng.choice(["institutional","community",None,None]),
            "low_income_subsidy_indicator": int(rng.random() < 0.10),
            "metal_level":                  rng.choice(metal_levels),
            "csr_indicator":                rng.choice(["73","87","94",None,None]),
            "enrollment_duration_months":   duration_months,
            "esrd_status":                  int(rng.random() < 0.005),
            "transplant_duration_months":   None,
            "group_id":                     f"GRP{rng.integers(1000,9999)}",
            "group_name":                   rng.choice(["Employer A","Employer B","Employer C","Individual Market"]),
            "file_name":                    f"eligibility_{file_date.strftime('%Y%m%d')}.csv",
            "file_date":                    file_date,
            "ingest_datetime":              now_ts,
            "data_source":                  DATA_SOURCE,
        })
    return pd.DataFrame(rows)


def gen_encounters(
    date_start: datetime.date,
    date_end: datetime.date,
    seed: int,
) -> pd.DataFrame:
    """
    Generate N_ENCOUNTERS encounter rows for one side.
    Returns DataFrame including encounter_id, person_id, patient_id, dates — used
    as the key linkage for all downstream clinical tables.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    date_range_days = (date_end - date_start).days
    rows = []

    person_idx      = rng.integers(0, N_PERSONS,       size=N_ENCOUNTERS)
    enc_type_idx    = rng.integers(0, len(ENCOUNTER_TYPES), size=N_ENCOUNTERS)
    dur_days        = rng.integers(0, 5,                size=N_ENCOUNTERS)  # LOS 0-4 days
    admit_src_idx   = rng.integers(0, len(ADMIT_SOURCE), size=N_ENCOUNTERS)
    admit_type_idx  = rng.integers(0, len(ADMIT_TYPE),   size=N_ENCOUNTERS)
    disch_disp_idx  = rng.integers(0, len(DISCH_DISP),   size=N_ENCOUNTERS)
    prac_idx        = rng.integers(0, N_PRACTITIONERS,  size=N_ENCOUNTERS)
    loc_idx         = rng.integers(0, N_LOCATIONS,       size=N_ENCOUNTERS)
    dx_idx          = rng.integers(0, len(ICD10_CODES),  size=N_ENCOUNTERS)
    drg_idx         = rng.integers(0, len(DRG_CODES),    size=N_ENCOUNTERS)
    charge_arr      = np.exp(rng.normal(PROD_CHARGE_MU, PROD_CHARGE_SIGMA, size=N_ENCOUNTERS))
    paid_arr        = charge_arr * rng.uniform(0.5, 0.85, size=N_ENCOUNTERS)
    allowed_arr     = charge_arr * rng.uniform(0.7, 0.95, size=N_ENCOUNTERS)
    day_offsets     = rng.integers(0, max(1, date_range_days), size=N_ENCOUNTERS)

    for i in range(N_ENCOUNTERS):
        enc_start = date_start + datetime.timedelta(days=int(day_offsets[i]))
        enc_end   = enc_start  + datetime.timedelta(days=int(dur_days[i]))
        if enc_end > date_end:
            enc_end = date_end
        p_id  = PERSON_IDS[person_idx[i]]
        pt_id = PERSON_TO_PATIENT[p_id]
        prac  = PRAC_IDS[prac_idx[i]]
        loc   = LOC_IDS[loc_idx[i]]
        enc_type = ENCOUNTER_TYPES[enc_type_idx[i]]
        is_inpatient = enc_type in ("inpatient","emergency","observation")
        rows.append({
            "encounter_id":                p_id + f"-E{i:07d}",
            "person_id":                   p_id,
            "patient_id":                  pt_id,
            "encounter_type":              enc_type,
            "encounter_start_date":        enc_start,
            "encounter_end_date":          enc_end,
            "admit_source_code":           ADMIT_SOURCE[admit_src_idx[i]] if is_inpatient else None,
            "admit_type_code":             ADMIT_TYPE[admit_type_idx[i]]  if is_inpatient else None,
            "discharge_disposition_code":  DISCH_DISP[disch_disp_idx[i]] if is_inpatient else None,
            "attending_provider_id":       prac,
            "attending_provider_name":     None,  # resolved at display time
            "facility_npi":                LOC_NPIS[loc_idx[i]],
            "facility_name":               f"Location Facility {loc_idx[i]+1:03d}",
            "primary_diagnosis_code_type": "ICD-10-CM",
            "primary_diagnosis_code":      ICD10_CODES[dx_idx[i]],
            "drg_code_type":               "MS-DRG" if is_inpatient else None,
            "drg_code":                    DRG_CODES[drg_idx[i]] if is_inpatient else None,
            "paid_amount":                 round(float(paid_arr[i]), 2),
            "allowed_amount":              round(float(allowed_arr[i]), 2),
            "charge_amount":               round(float(charge_arr[i]), 2),
            "ingest_datetime":             now_ts,
            "data_source":                 DATA_SOURCE,
        })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Medical Claims Generator

# COMMAND ----------

def gen_medical_claims(
    encounters_df: pd.DataFrame,
    charge_mu: float,
    charge_sigma: float,
    denial_rate: float,
    professional_rate: float,
    seed: int,
) -> pd.DataFrame:
    """
    Generate medical claim lines from encounters.
    Each encounter gets 1-3 claim lines.
    Applies charge_mu / charge_sigma for the lognormal draw (drift signal).
    denial_rate controls adj_qualification (denied claims get paid_amount=0).
    professional_rate controls PROFESSIONAL vs INSTITUTIONAL split.
    Returns DataFrame with ~50,000 rows.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    n_enc  = len(encounters_df)
    rows   = []

    # Vectorize heavy draws up front
    lines_per_enc   = rng.integers(1, 4, size=n_enc)                    # 1-3 lines
    is_professional = rng.random(size=n_enc) < professional_rate
    denial_mask     = rng.random(size=n_enc) < denial_rate
    paid_date_offsets = rng.integers(14, 90, size=n_enc)

    enc_records = encounters_df.to_dict("records")

    for i, enc in enumerate(enc_records):
        n_lines      = int(lines_per_enc[i])
        is_prof      = bool(is_professional[i])
        is_denied    = bool(denial_mask[i])
        claim_type   = "PROFESSIONAL" if is_prof else "INSTITUTIONAL"
        cpt_pool     = CPT_PROFESSIONAL if is_prof else CPT_INSTITUTIONAL

        claim_id = f"CLM-{enc['person_id']}-{i:07d}"
        enc_start: datetime.date = enc["encounter_start_date"]
        enc_end:   datetime.date = enc["encounter_end_date"]
        paid_dt    = enc_start + datetime.timedelta(days=int(paid_date_offsets[i]))
        person_id  = enc["person_id"]
        member_id  = PERSON_TO_MEMBER[person_id]

        # Per-line draws
        charge_lines  = np.exp(rng.normal(charge_mu, charge_sigma, size=n_lines))
        paid_ratio    = rng.uniform(0.5, 0.85,  size=n_lines)
        allowed_ratio = rng.uniform(0.7, 0.95,  size=n_lines)
        coins_ratio   = rng.uniform(0.05, 0.20, size=n_lines)
        copay_arr     = rng.choice([0, 10, 20, 30, 40, 50], size=n_lines)
        ded_ratio     = rng.uniform(0.0, 0.10,  size=n_lines)
        svc_units     = rng.integers(1, 4,       size=n_lines)
        hcpcs_idx     = rng.integers(0, len(cpt_pool), size=n_lines)
        dx_idx        = rng.integers(0, len(ICD10_CODES), size=15)  # up to 15 dx per claim
        rev_idx       = rng.integers(0, len(REVENUE_CODES), size=n_lines)
        mod_idx       = rng.integers(0, len(MOD_CODES), size=n_lines * 5)
        prac_idx      = rng.integers(0, N_PRACTITIONERS)
        loc_idx       = rng.integers(0, N_LOCATIONS)

        rendering_npi = PRAC_NPIS[prac_idx]
        billing_npi   = PRAC_NPIS[rng.integers(0, N_PRACTITIONERS)]
        facility_npi  = LOC_NPIS[loc_idx]
        pos_code      = rng.choice(POS_CODES)
        bill_type     = rng.choice(BILL_TYPES) if not is_prof else None
        drg_code      = rng.choice(DRG_CODES) if not is_prof else None

        for ln_num in range(1, n_lines + 1):
            li = ln_num - 1
            charge  = round(float(charge_lines[li]), 2)
            allowed = round(float(charge * allowed_ratio[li]), 2)
            if is_denied:
                paid    = 0.0
                coins   = 0.0
                copay   = 0.0
                ded     = 0.0
            else:
                paid    = round(float(charge * paid_ratio[li]), 2)
                coins   = round(float(charge * coins_ratio[li]), 2)
                copay   = float(copay_arr[li])
                ded     = round(float(charge * ded_ratio[li]), 2)
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
            })

    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Pharmacy Claims Generator

# COMMAND ----------

def gen_pharmacy_claims(
    date_start: datetime.date,
    date_end: datetime.date,
    seed: int,
) -> pd.DataFrame:
    """
    Generate ~3,000 pharmacy claim lines.
    Persons are randomly sampled from PERSON_IDS.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    n = 3_000
    date_range_days = (date_end - date_start).days
    rows = []

    person_idx      = rng.integers(0, N_PERSONS,     size=n)
    ndc_idx         = rng.integers(0, len(NDC_LIST),  size=n)
    dispense_offsets = rng.integers(0, max(1, date_range_days), size=n)
    paid_offsets    = rng.integers(7, 30,             size=n)
    qty_arr         = rng.integers(30, 91,            size=n)
    days_arr        = rng.integers(30, 91,            size=n)
    refills_arr     = rng.integers(0, 6,              size=n)
    charge_arr      = rng.uniform(10.0, 800.0,        size=n)
    paid_ratio      = rng.uniform(0.5, 0.95,          size=n)
    coins_ratio     = rng.uniform(0.05, 0.20,         size=n)
    copay_arr       = rng.choice([0, 5, 10, 20, 40, 80], size=n)
    ded_ratio       = rng.uniform(0.0, 0.10,          size=n)
    in_net          = rng.random(size=n) > 0.10
    presc_prac_idx  = rng.integers(0, N_PRACTITIONERS, size=n)
    disp_prac_idx   = rng.integers(0, N_PRACTITIONERS, size=n)
    file_date_offs  = rng.integers(0, 10,             size=n)

    for i in range(n):
        person_id = PERSON_IDS[person_idx[i]]
        member_id = PERSON_TO_MEMBER[person_id]
        disp_date = date_start + datetime.timedelta(days=int(dispense_offsets[i]))
        paid_date = disp_date  + datetime.timedelta(days=int(paid_offsets[i]))
        if paid_date > date_end:
            paid_date = date_end
        ndc       = NDC_LIST[ndc_idx[i]]
        charge    = round(float(charge_arr[i]), 2)
        paid      = round(float(charge * paid_ratio[i]), 2)
        allowed   = round(float(charge * rng.uniform(0.7, 0.95)), 2)
        coins     = round(float(charge * coins_ratio[i]), 2)
        copay     = float(copay_arr[i])
        ded       = round(float(charge * ded_ratio[i]), 2)
        file_date = date_start + datetime.timedelta(days=int(file_date_offs[i]))

        rows.append({
            "claim_id":                  f"RXCLM-{person_id}-{i:06d}",
            "claim_line_number":         1,
            "person_id":                 person_id,
            "member_id":                 member_id,
            "payer":                     PAYER,
            "plan":                      PLAN,
            "prescribing_provider_npi":  PRAC_NPIS[presc_prac_idx[i]],
            "dispensing_provider_npi":   PRAC_NPIS[disp_prac_idx[i]],
            "dispensing_date":           disp_date,
            "ndc_code":                  ndc,
            "quantity":                  int(qty_arr[i]),
            "days_supply":               int(days_arr[i]),
            "refills":                   int(refills_arr[i]),
            "paid_date":                 paid_date,
            "paid_amount":               paid,
            "allowed_amount":            allowed,
            "charge_amount":             charge,
            "coinsurance_amount":        coins,
            "copayment_amount":          copay,
            "deductible_amount":         ded,
            "in_network_flag":           int(in_net[i]),
            "file_name":                 f"pharmacy_{file_date.strftime('%Y%m%d')}.csv",
            "file_date":                 file_date,
            "ingest_datetime":           now_ts,
            "data_source":               DATA_SOURCE,
        })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Condition and Procedure Generators

# COMMAND ----------

def gen_conditions(encounters_df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    Generate ~3 conditions per encounter (~75,000 rows).
    Links to encounter_id and person_id from encounters_df.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date"]].to_dict("records")

    conditions_per_enc = rng.integers(2, 5, size=n_enc)  # 2-4 conditions avg ~3

    for i, enc in enumerate(enc_records):
        n_cond     = int(conditions_per_enc[i])
        dx_indices = rng.integers(0, len(ICD10_CODES), size=n_cond)
        enc_date   = enc["encounter_start_date"]

        for j in range(n_cond):
            recorded_offset = rng.integers(0, 3)
            onset_offset    = rng.integers(1, 180)
            recorded_date   = enc_date - datetime.timedelta(days=int(recorded_offset))
            onset_date      = enc_date - datetime.timedelta(days=int(onset_offset))
            status          = rng.choice(CONDITION_STATUSES)
            resolved_date   = (enc_date + datetime.timedelta(days=int(rng.integers(7, 90)))
                               if status == "resolved" else None)
            condition_rank  = j + 1
            icd_code        = ICD10_CODES[dx_indices[j]]

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
                "condition_rank":         condition_rank,
                "present_on_admit_code":  rng.choice(["Y","N","U","W",None]) if j == 0 else None,
                "ingest_datetime":        now_ts,
                "data_source":            DATA_SOURCE,
            })
    return pd.DataFrame(rows)


def gen_procedures(encounters_df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    Generate ~2 procedures per encounter (~50,000 rows).
    Links to encounter_id and person_id from encounters_df.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date","encounter_end_date"]].to_dict("records")

    procs_per_enc = rng.integers(1, 4, size=n_enc)  # 1-3 procedures avg ~2

    for i, enc in enumerate(enc_records):
        n_proc     = int(procs_per_enc[i])
        cpt_indices = rng.integers(0, len(CPT_PROFESSIONAL), size=n_proc)
        prac_indices = rng.integers(0, N_PRACTITIONERS, size=n_proc)

        for j in range(n_proc):
            proc_date_offset = rng.integers(0, max(1, (enc["encounter_end_date"] -
                                                        enc["encounter_start_date"]).days + 1))
            proc_date = enc["encounter_start_date"] + datetime.timedelta(days=int(proc_date_offset))
            cpt_code  = CPT_PROFESSIONAL[cpt_indices[j]]
            mod_draws = rng.integers(0, len(MOD_CODES), size=5)
            mods      = [MOD_CODES[int(m)] for m in mod_draws]

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
                "ingest_datetime":   now_ts,
                "data_source":       DATA_SOURCE,
            })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Lab Result and Observation Generators

# COMMAND ----------

def gen_lab_results(
    encounters_df: pd.DataFrame,
    hba1c_mean: float,
    seed: int,
) -> pd.DataFrame:
    """
    Generate ~2 lab results per encounter (~50,000 rows).
    hba1c_mean controls the drift signal for LOINC 4548-4 (HbA1c).
    All HbA1c values are drawn from Normal(hba1c_mean, 1.5) clipped to [4.5, 13.0].
    Other labs draw uniformly within their reference range.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date"]].to_dict("records")

    labs_per_enc  = rng.integers(1, 4, size=n_enc)  # 1-3 labs avg ~2
    prac_indices  = rng.integers(0, N_PRACTITIONERS, size=n_enc)
    order_types   = ["laboratory","point-of-care","external"]

    for i, enc in enumerate(enc_records):
        n_labs     = int(labs_per_enc[i])
        lab_codes  = rng.choice(LOINC_LAB_CODES, size=n_labs, replace=True)
        prac_id    = PRAC_IDS[prac_indices[i]]
        enc_date   = enc["encounter_start_date"]

        for j in range(n_labs):
            loinc_code = lab_codes[j]
            desc, units, lo, hi = LOINC_LABS[loinc_code]

            if loinc_code == "4548-4":
                # HbA1c drift signal
                val_float = float(np.clip(rng.normal(hba1c_mean, 1.5), 4.5, 13.0))
                result_str = f"{val_float:.1f}"
            else:
                val_float  = float(rng.uniform(lo, hi))
                result_str = f"{val_float:.2f}"

            abnormal = "Y" if (val_float < lo or val_float > hi) else "N"
            collect_offset = rng.integers(0, 2)
            result_offset  = rng.integers(0, 3)
            collection_dt  = datetime.datetime.combine(
                enc_date - datetime.timedelta(days=int(collect_offset)),
                datetime.time(int(rng.integers(6, 18)), 0))
            result_dt      = collection_dt + datetime.timedelta(hours=int(result_offset))

            rows.append({
                "lab_result_id":                   f"{enc['encounter_id']}-L{j:02d}",
                "person_id":                       enc["person_id"],
                "patient_id":                      enc["patient_id"],
                "encounter_id":                    enc["encounter_id"],
                "accession_number":                f"ACC{rng.integers(100000,999999)}",
                "source_order_type":               rng.choice(order_types),
                "source_order_code":               loinc_code,
                "source_order_description":        desc,
                "source_component_type":           "LOINC",
                "source_component_code":           loinc_code,
                "source_component_description":    desc,
                "status":                          "final",
                "result":                          result_str,
                "result_datetime":                 result_dt,
                "collection_datetime":             collection_dt,
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
                "ingest_datetime":                 now_ts,
                "data_source":                     DATA_SOURCE,
            })
    return pd.DataFrame(rows)


def gen_observations(
    encounters_df: pd.DataFrame,
    bmi_mean: float,
    seed: int,
) -> pd.DataFrame:
    """
    Generate ~3 vital-sign observations per encounter (~75,000 rows).
    bmi_mean controls the drift signal for LOINC 39156-5 (BMI).
    BMI values drawn from Normal(bmi_mean, 5.0) clipped to [15, 55].
    Other vitals draw uniformly within reference range.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date"]].to_dict("records")

    obs_per_enc = rng.integers(2, 5, size=n_enc)  # 2-4 vitals avg ~3

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
                # BMI drift signal
                val_float  = float(np.clip(rng.normal(bmi_mean, 5.0), 15.0, 55.0))
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
                "ingest_datetime":                 now_ts,
                "data_source":                     DATA_SOURCE,
            })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Medication, Immunization, Appointment, and Provider Attribution Generators

# COMMAND ----------

def gen_medications(encounters_df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    Generate ~20,000 medication rows linked to encounters.
    Each encounter has 0-2 medications (average ~0.8 per encounter).
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date","encounter_end_date"]].to_dict("records")

    meds_per_enc = rng.integers(0, 3, size=n_enc)  # 0-2 per encounter

    atc_codes = ["A10BA02","C09AA02","C10AA01","A02BC01","C08CA01",
                 "H03AA01","N06AB06","C07AB02","R03AC02","A10BH01"]
    rxnorm_codes = ["860975","861007","617311","40790","329528",
                    "10582","36567","41493","435","261242"]

    for i, enc in enumerate(enc_records):
        n_meds   = int(meds_per_enc[i])
        enc_date = enc["encounter_start_date"]

        for j in range(n_meds):
            ndc_idx = rng.integers(0, len(NDC_LIST))
            prac_idx = rng.integers(0, N_PRACTITIONERS)
            dispense_offset = rng.integers(0, 3)
            prescribe_offset = rng.integers(0, 3)
            disp_date = enc_date + datetime.timedelta(days=int(dispense_offset))
            presc_date = enc_date - datetime.timedelta(days=int(prescribe_offset))

            rows.append({
                "medication_id":        f"{enc['encounter_id']}-MED{j:02d}",
                "person_id":            enc["person_id"],
                "patient_id":           enc["patient_id"],
                "encounter_id":         enc["encounter_id"],
                "dispensing_date":      disp_date,
                "prescribing_date":     presc_date,
                "source_code_type":     "NDC",
                "source_code":          NDC_LIST[ndc_idx],
                "source_description":   NDC_NAMES[ndc_idx],
                "ndc_code":             NDC_LIST[ndc_idx],
                "rxnorm_code":          rxnorm_codes[ndc_idx % len(rxnorm_codes)],
                "atc_code":             atc_codes[ndc_idx % len(atc_codes)],
                "route":                rng.choice(ROUTES),
                "strength":             rng.choice(["10mg","25mg","50mg","100mg","500mg","1g","5mcg","20mg"]),
                "quantity":             float(rng.integers(30, 91)),
                "quantity_unit":        rng.choice(["tablet","capsule","mL","unit","inhaler"]),
                "days_supply":          float(rng.integers(30, 91)),
                "practitioner_id":      PRAC_IDS[prac_idx],
                "ingest_datetime":      now_ts,
                "data_source":          DATA_SOURCE,
            })
    return pd.DataFrame(rows)


def gen_immunizations(
    date_start: datetime.date,
    date_end: datetime.date,
    seed: int,
) -> pd.DataFrame:
    """
    Generate ~3 immunizations per person (~15,000 rows total).
    Immunizations are patient-level events, optionally linked to encounters.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    n_imm = N_PERSONS * 3
    date_range_days = (date_end - date_start).days
    rows = []

    person_idx    = rng.integers(0, N_PERSONS,     size=n_imm)
    cvx_idx       = rng.integers(0, len(CVX_CODES), size=n_imm)
    date_offsets  = rng.integers(0, max(1, date_range_days), size=n_imm)
    prac_idx      = rng.integers(0, N_PRACTITIONERS, size=n_imm)
    loc_idx       = rng.integers(0, N_LOCATIONS,     size=n_imm)
    dose_arr      = rng.integers(1, 4,               size=n_imm)
    lot_arr       = rng.integers(100000, 999999,      size=n_imm)
    statuses      = ["completed","not-done","entered-in-error"]
    status_wts    = [0.92, 0.06, 0.02]

    for i in range(n_imm):
        person_id = PERSON_IDS[person_idx[i]]
        patient_id = PERSON_TO_PATIENT[person_id]
        cvx_code  = CVX_CODES[cvx_idx[i]]
        cvx_name  = CVX_VACCINES[cvx_code]
        occ_date  = date_start + datetime.timedelta(days=int(date_offsets[i]))
        status    = rng.choice(statuses, p=status_wts)
        status_reason = ("patient refusal" if status == "not-done"
                         else ("data entry error" if status == "entered-in-error"
                               else None))

        rows.append({
            "immunization_id":   f"IMM-{person_id}-{i:06d}",
            "person_id":         person_id,
            "patient_id":        patient_id,
            "encounter_id":      None,  # most immunizations not tied to a specific encounter
            "source_code_type":  "CVX",
            "source_code":       cvx_code,
            "source_description": cvx_name,
            "status":            status,
            "status_reason":     status_reason,
            "occurrence_date":   occ_date,
            "source_dose":       str(int(dose_arr[i])),
            "lot_number":        str(int(lot_arr[i])),
            "body_site":         rng.choice(BODY_SITES),
            "route":             rng.choice(["intramuscular","subcutaneous","intranasal","oral"]),
            "location_id":       LOC_IDS[loc_idx[i]],
            "practitioner_id":   PRAC_IDS[prac_idx[i]],
            "ingest_datetime":   now_ts,
            "data_source":       DATA_SOURCE,
        })
    return pd.DataFrame(rows)


def gen_appointments(encounters_df: pd.DataFrame, seed: int) -> pd.DataFrame:
    """
    Generate ~25,000 appointment rows, one per encounter.
    Appointment start/end mirrors encounter dates with small offsets.
    """
    rng = np.random.default_rng(seed)
    rows = []
    n_enc = len(encounters_df)
    enc_records = encounters_df[["encounter_id","person_id","patient_id",
                                  "encounter_start_date","encounter_end_date"]].to_dict("records")

    prac_idx     = rng.integers(0, N_PRACTITIONERS, size=n_enc)
    loc_idx      = rng.integers(0, N_LOCATIONS,     size=n_enc)
    type_idx     = rng.integers(0, len(APPT_TYPES), size=n_enc)
    status_idx   = rng.integers(0, len(APPT_STATUSES), size=n_enc)
    dur_arr      = rng.integers(15, 61,             size=n_enc)  # 15-60 minute appointments
    reason_codes = ["routine","follow-up","sick","preventive","chronic-management",None]

    for i, enc in enumerate(enc_records):
        enc_date     = enc["encounter_start_date"]
        start_hour   = int(rng.integers(8, 17))
        appt_start   = datetime.datetime.combine(enc_date, datetime.time(start_hour, 0))
        duration_min = int(dur_arr[i])
        appt_end     = appt_start + datetime.timedelta(minutes=duration_min)
        status       = APPT_STATUSES[status_idx[i]]
        cancel_reason = (rng.choice(["patient request","provider unavailable","weather","other"])
                         if status in ("canceled", "no-show") else None)
        appt_type    = APPT_TYPES[type_idx[i]]

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
            "reason":              rng.choice(reason_codes),
            "cancellation_reason": cancel_reason,
            "data_source":         DATA_SOURCE,
        })
    return pd.DataFrame(rows)


def gen_provider_attribution(
    date_start: datetime.date,
    date_end: datetime.date,
    seed: int,
) -> pd.DataFrame:
    """
    Generate one attribution record per person (~5,000 rows).
    year_month is the first month of the date range.
    """
    rng = np.random.default_rng(seed)
    now_ts = datetime.datetime.utcnow()
    rows = []

    prac_idx    = rng.integers(0, N_PRACTITIONERS, size=N_PERSONS)
    year_month  = date_start.strftime("%Y-%m")
    lobs        = ["commercial","medicare","medicaid","exchange"]
    file_date   = date_start + datetime.timedelta(days=5)

    for i, person_id in enumerate(PERSON_IDS):
        member_id = PERSON_TO_MEMBER[person_id]
        prac_id   = PRAC_IDS[prac_idx[i]]
        prac_npi  = PRAC_NPI_MAP[prac_id]
        lob       = rng.choice(lobs)

        rows.append({
            "person_id":                              person_id,
            "member_id":                              member_id,
            "year_month":                             year_month,
            "payer":                                  PAYER,
            "plan":                                   PLAN,
            "payer_attributed_provider":              prac_npi,
            "payer_attributed_provider_practice":     f"Practice {rng.integers(1, 50):03d}",
            "payer_attributed_provider_organization": rng.choice(["Regional Health System",
                                                                   "Community Medical Center",
                                                                   "StatCare Network"]),
            "payer_attributed_provider_lob":          lob,
            "custom_attributed_provider":             prac_npi,
            "custom_attributed_provider_practice":    f"Practice {rng.integers(1, 50):03d}",
            "custom_attributed_provider_organization": rng.choice(["Regional Health System",
                                                                    "Community Medical Center",
                                                                    "StatCare Network"]),
            "custom_attributed_provider_lob":         lob,
            "file_name":                              f"attribution_{file_date.strftime('%Y%m%d')}.csv",
            "ingest_datetime":                        now_ts,
            "data_source":                            DATA_SOURCE,
        })
    return pd.DataFrame(rows)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 12. PROD Data Generation

# COMMAND ----------

print("=" * 70)
print("GENERATING PROD DATA")
print(f"  Date range: {PROD_DATE_START} to {PROD_DATE_END}")
print(f"  charge_mu={PROD_CHARGE_MU}, charge_sigma={PROD_CHARGE_SIGMA}")
print(f"  denial_rate={PROD_DENIAL_RATE}, professional_rate={PROD_PROFESSIONAL_RATE}")
print(f"  HbA1c mean={PROD_HBAC1C_MEAN}, BMI mean={PROD_BMI_MEAN}")
print("=" * 70)

# Create PROD schemas
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{PROD_CLAIMS}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{PROD_CLINICAL}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{PROD_MEMBERS}`")
print("PROD schemas created (or already exist).")

# COMMAND ----------

# Reference tables (static, same both sides) — written to PROD claims schema
print("\n-- Reference tables (PROD) --")
prod_location_df     = gen_location()
prod_practitioner_df = gen_practitioner()
write_delta(prod_location_df,     CATALOG, PROD_CLAIMS, "location")
write_delta(prod_practitioner_df, CATALOG, PROD_CLAIMS, "practitioner")

# COMMAND ----------

# Members
print("\n-- Members (PROD) --")
prod_patient_df     = gen_patient()
prod_eligibility_df = gen_eligibility(PROD_DATE_START, PROD_DATE_END, seed=SEED_PROD + 1)
write_delta(prod_patient_df,     CATALOG, PROD_MEMBERS, "patient")
write_delta(prod_eligibility_df, CATALOG, PROD_MEMBERS, "eligibility")

# COMMAND ----------

# Clinical — Encounters (needed first for referential integrity downstream)
print("\n-- Clinical: Encounters (PROD) --")
prod_encounters_df = gen_encounters(PROD_DATE_START, PROD_DATE_END, seed=SEED_PROD + 2)
write_delta(prod_encounters_df, CATALOG, PROD_CLINICAL, "encounter")

# COMMAND ----------

# Clinical — Conditions
print("\n-- Clinical: Conditions (PROD) --")
prod_conditions_df = gen_conditions(prod_encounters_df, seed=SEED_PROD + 3)
write_delta(prod_conditions_df, CATALOG, PROD_CLINICAL, "condition")

# COMMAND ----------

# Clinical — Procedures
print("\n-- Clinical: Procedures (PROD) --")
prod_procedures_df = gen_procedures(prod_encounters_df, seed=SEED_PROD + 4)
write_delta(prod_procedures_df, CATALOG, PROD_CLINICAL, "procedure")

# COMMAND ----------

# Clinical — Lab Results
print("\n-- Clinical: Lab Results (PROD) --")
prod_lab_results_df = gen_lab_results(prod_encounters_df, hba1c_mean=PROD_HBAC1C_MEAN, seed=SEED_PROD + 5)
write_delta(prod_lab_results_df, CATALOG, PROD_CLINICAL, "lab_result")

# COMMAND ----------

# Clinical — Observations (vitals)
print("\n-- Clinical: Observations (PROD) --")
prod_observations_df = gen_observations(prod_encounters_df, bmi_mean=PROD_BMI_MEAN, seed=SEED_PROD + 6)
write_delta(prod_observations_df, CATALOG, PROD_CLINICAL, "observation")

# COMMAND ----------

# Clinical — Medications
print("\n-- Clinical: Medications (PROD) --")
prod_medications_df = gen_medications(prod_encounters_df, seed=SEED_PROD + 7)
write_delta(prod_medications_df, CATALOG, PROD_CLINICAL, "medication")

# COMMAND ----------

# Clinical — Immunizations
print("\n-- Clinical: Immunizations (PROD) --")
prod_immunizations_df = gen_immunizations(PROD_DATE_START, PROD_DATE_END, seed=SEED_PROD + 8)
write_delta(prod_immunizations_df, CATALOG, PROD_CLINICAL, "immunization")

# COMMAND ----------

# Clinical — Appointments
print("\n-- Clinical: Appointments (PROD) --")
prod_appointments_df = gen_appointments(prod_encounters_df, seed=SEED_PROD + 9)
write_delta(prod_appointments_df, CATALOG, PROD_CLINICAL, "appointment")

# COMMAND ----------

# Claims — Medical
print("\n-- Claims: Medical (PROD) --")
prod_medical_claims_df = gen_medical_claims(
    prod_encounters_df,
    charge_mu=PROD_CHARGE_MU,
    charge_sigma=PROD_CHARGE_SIGMA,
    denial_rate=PROD_DENIAL_RATE,
    professional_rate=PROD_PROFESSIONAL_RATE,
    seed=SEED_PROD + 10,
)
write_delta(prod_medical_claims_df, CATALOG, PROD_CLAIMS, "medical_claim")

# COMMAND ----------

# Claims — Pharmacy
print("\n-- Claims: Pharmacy (PROD) --")
prod_pharmacy_claims_df = gen_pharmacy_claims(PROD_DATE_START, PROD_DATE_END, seed=SEED_PROD + 11)
write_delta(prod_pharmacy_claims_df, CATALOG, PROD_CLAIMS, "pharmacy_claim")

# COMMAND ----------

# Claims — Provider Attribution
print("\n-- Claims: Provider Attribution (PROD) --")
prod_provider_attr_df = gen_provider_attribution(PROD_DATE_START, PROD_DATE_END, seed=SEED_PROD + 12)
write_delta(prod_provider_attr_df, CATALOG, PROD_CLAIMS, "provider_attribution")

# COMMAND ----------

# Reference tables — also written to PROD clinical and members schemas
print("\n-- Reference tables: clinical + members schemas (PROD) --")
write_delta(gen_location(),     CATALOG, PROD_CLINICAL, "location")
write_delta(gen_practitioner(), CATALOG, PROD_CLINICAL, "practitioner")
write_delta(gen_location(),     CATALOG, PROD_MEMBERS,  "location")
write_delta(gen_practitioner(), CATALOG, PROD_MEMBERS,  "practitioner")

print("\nPROD generation complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 13. TEST Data Generation

# COMMAND ----------

print("=" * 70)
print("GENERATING TEST DATA")
print(f"  Date range: {TEST_DATE_START} to {TEST_DATE_END}")
print(f"  charge_mu={TEST_CHARGE_MU}, charge_sigma={TEST_CHARGE_SIGMA}")
print(f"  denial_rate={TEST_DENIAL_RATE}, professional_rate={TEST_PROFESSIONAL_RATE}")
print(f"  HbA1c mean={TEST_HBAC1C_MEAN}, BMI mean={TEST_BMI_MEAN}")
print("=" * 70)

# Create TEST schemas
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{TEST_CLAIMS}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{TEST_CLINICAL}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{TEST_MEMBERS}`")
print("TEST schemas created (or already exist).")

# COMMAND ----------

# Reference tables (static, same both sides) — written to TEST claims schema
print("\n-- Reference tables (TEST) --")
write_delta(gen_location(),     CATALOG, TEST_CLAIMS, "location")
write_delta(gen_practitioner(), CATALOG, TEST_CLAIMS, "practitioner")

# COMMAND ----------

# Members
print("\n-- Members (TEST) --")
test_patient_df     = gen_patient()  # static, same both sides
test_eligibility_df = gen_eligibility(TEST_DATE_START, TEST_DATE_END, seed=SEED_TEST + 1)
write_delta(test_patient_df,     CATALOG, TEST_MEMBERS, "patient")
write_delta(test_eligibility_df, CATALOG, TEST_MEMBERS, "eligibility")

# COMMAND ----------

# Clinical — Encounters (TEST-specific encounters; downstream tables reference these)
print("\n-- Clinical: Encounters (TEST) --")
test_encounters_df = gen_encounters(TEST_DATE_START, TEST_DATE_END, seed=SEED_TEST + 2)
write_delta(test_encounters_df, CATALOG, TEST_CLINICAL, "encounter")

# COMMAND ----------

# Clinical — Conditions
print("\n-- Clinical: Conditions (TEST) --")
test_conditions_df = gen_conditions(test_encounters_df, seed=SEED_TEST + 3)
write_delta(test_conditions_df, CATALOG, TEST_CLINICAL, "condition")

# COMMAND ----------

# Clinical — Procedures
print("\n-- Clinical: Procedures (TEST) --")
test_procedures_df = gen_procedures(test_encounters_df, seed=SEED_TEST + 4)
write_delta(test_procedures_df, CATALOG, TEST_CLINICAL, "procedure")

# COMMAND ----------

# Clinical — Lab Results (drifted HbA1c mean)
print("\n-- Clinical: Lab Results (TEST) --")
test_lab_results_df = gen_lab_results(test_encounters_df, hba1c_mean=TEST_HBAC1C_MEAN, seed=SEED_TEST + 5)
write_delta(test_lab_results_df, CATALOG, TEST_CLINICAL, "lab_result")

# COMMAND ----------

# Clinical — Observations (drifted BMI mean)
print("\n-- Clinical: Observations (TEST) --")
test_observations_df = gen_observations(test_encounters_df, bmi_mean=TEST_BMI_MEAN, seed=SEED_TEST + 6)
write_delta(test_observations_df, CATALOG, TEST_CLINICAL, "observation")

# COMMAND ----------

# Clinical — Medications
print("\n-- Clinical: Medications (TEST) --")
test_medications_df = gen_medications(test_encounters_df, seed=SEED_TEST + 7)
write_delta(test_medications_df, CATALOG, TEST_CLINICAL, "medication")

# COMMAND ----------

# Clinical — Immunizations
print("\n-- Clinical: Immunizations (TEST) --")
test_immunizations_df = gen_immunizations(TEST_DATE_START, TEST_DATE_END, seed=SEED_TEST + 8)
write_delta(test_immunizations_df, CATALOG, TEST_CLINICAL, "immunization")

# COMMAND ----------

# Clinical — Appointments
print("\n-- Clinical: Appointments (TEST) --")
test_appointments_df = gen_appointments(test_encounters_df, seed=SEED_TEST + 9)
write_delta(test_appointments_df, CATALOG, TEST_CLINICAL, "appointment")

# COMMAND ----------

# Claims — Medical (drifted charge distribution + denial rate + claim_type mix)
print("\n-- Claims: Medical (TEST) --")
test_medical_claims_df = gen_medical_claims(
    test_encounters_df,
    charge_mu=TEST_CHARGE_MU,
    charge_sigma=TEST_CHARGE_SIGMA,
    denial_rate=TEST_DENIAL_RATE,
    professional_rate=TEST_PROFESSIONAL_RATE,
    seed=SEED_TEST + 10,
)
write_delta(test_medical_claims_df, CATALOG, TEST_CLAIMS, "medical_claim")

# COMMAND ----------

# Claims — Pharmacy
print("\n-- Claims: Pharmacy (TEST) --")
test_pharmacy_claims_df = gen_pharmacy_claims(TEST_DATE_START, TEST_DATE_END, seed=SEED_TEST + 11)
write_delta(test_pharmacy_claims_df, CATALOG, TEST_CLAIMS, "pharmacy_claim")

# COMMAND ----------

# Claims — Provider Attribution
print("\n-- Claims: Provider Attribution (TEST) --")
test_provider_attr_df = gen_provider_attribution(TEST_DATE_START, TEST_DATE_END, seed=SEED_TEST + 12)
write_delta(test_provider_attr_df, CATALOG, TEST_CLAIMS, "provider_attribution")

# COMMAND ----------

# Reference tables — also written to TEST clinical and members schemas
print("\n-- Reference tables: clinical + members schemas (TEST) --")
write_delta(gen_location(),     CATALOG, TEST_CLINICAL, "location")
write_delta(gen_practitioner(), CATALOG, TEST_CLINICAL, "practitioner")
write_delta(gen_location(),     CATALOG, TEST_MEMBERS,  "location")
write_delta(gen_practitioner(), CATALOG, TEST_MEMBERS,  "practitioner")

print("\nTEST generation complete.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 14. Validation

# COMMAND ----------

# MAGIC %md
# MAGIC ### Row counts — all 30 tables (15 PROD + 15 TEST)

# COMMAND ----------

print("=" * 70)
print("VALIDATION: Row counts")
print("=" * 70)

validation_tables = [
    # (side, schema, table)
    ("PROD", PROD_CLAIMS,   "medical_claim"),
    ("PROD", PROD_CLAIMS,   "pharmacy_claim"),
    ("PROD", PROD_CLAIMS,   "provider_attribution"),
    ("PROD", PROD_CLAIMS,   "location"),
    ("PROD", PROD_CLAIMS,   "practitioner"),
    ("PROD", PROD_CLINICAL, "encounter"),
    ("PROD", PROD_CLINICAL, "condition"),
    ("PROD", PROD_CLINICAL, "procedure"),
    ("PROD", PROD_CLINICAL, "lab_result"),
    ("PROD", PROD_CLINICAL, "observation"),
    ("PROD", PROD_CLINICAL, "medication"),
    ("PROD", PROD_CLINICAL, "immunization"),
    ("PROD", PROD_CLINICAL, "appointment"),
    ("PROD", PROD_MEMBERS,  "patient"),
    ("PROD", PROD_MEMBERS,  "eligibility"),
    ("TEST", TEST_CLAIMS,   "medical_claim"),
    ("TEST", TEST_CLAIMS,   "pharmacy_claim"),
    ("TEST", TEST_CLAIMS,   "provider_attribution"),
    ("TEST", TEST_CLAIMS,   "location"),
    ("TEST", TEST_CLAIMS,   "practitioner"),
    ("TEST", TEST_CLINICAL, "encounter"),
    ("TEST", TEST_CLINICAL, "condition"),
    ("TEST", TEST_CLINICAL, "procedure"),
    ("TEST", TEST_CLINICAL, "lab_result"),
    ("TEST", TEST_CLINICAL, "observation"),
    ("TEST", TEST_CLINICAL, "medication"),
    ("TEST", TEST_CLINICAL, "immunization"),
    ("TEST", TEST_CLINICAL, "appointment"),
    ("TEST", TEST_MEMBERS,  "patient"),
    ("TEST", TEST_MEMBERS,  "eligibility"),
]

count_rows = []
for side, schema, table in validation_tables:
    cnt = spark.table(f"`{CATALOG}`.`{schema}`.`{table}`").count()
    count_rows.append({"side": side, "schema": schema, "table": table, "row_count": cnt})
    print(f"  {side:4s}  {schema:<28s}  {table:<24s}  {cnt:>12,}")

counts_df = spark.createDataFrame(count_rows)
display(counts_df)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Drift signal validation — charge_amount, HbA1c, BMI

# COMMAND ----------

from pyspark.sql import functions as F

print("=" * 70)
print("DRIFT SIGNAL VALIDATION")
print("=" * 70)

# charge_amount comparison
print("\n-- charge_amount statistics (medical_claim) --")
prod_charge = (spark.table(f"`{CATALOG}`.`{PROD_CLAIMS}`.`medical_claim`")
               .select(F.mean("charge_amount").alias("mean_charge"),
                       F.stddev("charge_amount").alias("std_charge"),
                       F.percentile_approx("charge_amount", 0.5).alias("median_charge")))
test_charge = (spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`medical_claim`")
               .select(F.mean("charge_amount").alias("mean_charge"),
                       F.stddev("charge_amount").alias("std_charge"),
                       F.percentile_approx("charge_amount", 0.5).alias("median_charge")))
print("PROD charge_amount:")
prod_charge.show()
print("TEST charge_amount:")
test_charge.show()

# COMMAND ----------

# HbA1c comparison (LOINC 4548-4)
print("\n-- HbA1c mean result (LOINC 4548-4) --")

prod_hba1c = (spark.table(f"`{CATALOG}`.`{PROD_CLINICAL}`.`lab_result`")
              .filter(F.col("source_component_code") == "4548-4")
              .select(F.mean(F.col("result").cast("double")).alias("mean_hba1c"),
                      F.count("*").alias("n_records")))
test_hba1c = (spark.table(f"`{CATALOG}`.`{TEST_CLINICAL}`.`lab_result`")
              .filter(F.col("source_component_code") == "4548-4")
              .select(F.mean(F.col("result").cast("double")).alias("mean_hba1c"),
                      F.count("*").alias("n_records")))
print(f"PROD HbA1c (expected mean ~{PROD_HBAC1C_MEAN}):")
prod_hba1c.show()
print(f"TEST HbA1c (expected mean ~{TEST_HBAC1C_MEAN}):")
test_hba1c.show()

# COMMAND ----------

# BMI comparison (LOINC 39156-5)
print("\n-- BMI mean result (LOINC 39156-5) --")

prod_bmi = (spark.table(f"`{CATALOG}`.`{PROD_CLINICAL}`.`observation`")
            .filter(F.col("source_code") == "39156-5")
            .select(F.mean(F.col("result").cast("double")).alias("mean_bmi"),
                    F.count("*").alias("n_records")))
test_bmi = (spark.table(f"`{CATALOG}`.`{TEST_CLINICAL}`.`observation`")
            .filter(F.col("source_code") == "39156-5")
            .select(F.mean(F.col("result").cast("double")).alias("mean_bmi"),
                    F.count("*").alias("n_records")))
print(f"PROD BMI (expected mean ~{PROD_BMI_MEAN}):")
prod_bmi.show()
print(f"TEST BMI (expected mean ~{TEST_BMI_MEAN}):")
test_bmi.show()

# COMMAND ----------

# claim_type mix comparison
print("\n-- claim_type distribution (medical_claim) --")

prod_claim_types = (spark.table(f"`{CATALOG}`.`{PROD_CLAIMS}`.`medical_claim`")
                    .groupBy("claim_type")
                    .agg(F.count("*").alias("n"))
                    .withColumn("side", F.lit("PROD")))
test_claim_types = (spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`medical_claim`")
                    .groupBy("claim_type")
                    .agg(F.count("*").alias("n"))
                    .withColumn("side", F.lit("TEST")))
claim_type_compare = prod_claim_types.union(test_claim_types).orderBy("side","claim_type")
display(claim_type_compare)

# COMMAND ----------

# denial signal — paid_amount == 0 rate
print("\n-- Denial rate (paid_amount = 0 pct of medical_claim lines) --")
prod_denial = (spark.table(f"`{CATALOG}`.`{PROD_CLAIMS}`.`medical_claim`")
               .agg((F.sum((F.col("paid_amount") == 0).cast("int")) /
                     F.count("*")).alias("denial_rate_pct")))
test_denial = (spark.table(f"`{CATALOG}`.`{TEST_CLAIMS}`.`medical_claim`")
               .agg((F.sum((F.col("paid_amount") == 0).cast("int")) /
                     F.count("*")).alias("denial_rate_pct")))
print(f"PROD denial rate (expected ~{PROD_DENIAL_RATE:.0%}):")
prod_denial.show()
print(f"TEST denial rate (expected ~{TEST_DENIAL_RATE:.0%}):")
test_denial.show()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 15. Done
# MAGIC
# MAGIC All 15 Tuva Input Layer tables have been written to both PROD and TEST environments.
# MAGIC
# MAGIC ### Table inventory
# MAGIC
# MAGIC | Schema | Tables |
# MAGIC |--------|--------|
# MAGIC | `dev.prod_main_claims` / `dev.test_main_claims` | `medical_claim`, `pharmacy_claim`, `provider_attribution`, `location`, `practitioner` |
# MAGIC | `dev.prod_main_clinical` / `dev.test_main_clinical` | `encounter`, `condition`, `procedure`, `lab_result`, `observation`, `medication`, `immunization`, `appointment`, `location`, `practitioner` |
# MAGIC | `dev.prod_main_members` / `dev.test_main_members` | `patient`, `eligibility`, `location`, `practitioner` |
# MAGIC
# MAGIC ### Next steps for the A/B profiler POC
# MAGIC
# MAGIC 1. Point the Synaptiq A/B Profiler at `dev.prod_main_claims.medical_claim` (Side A)
# MAGIC    vs `dev.test_main_claims.medical_claim` (Side B) and run a full profile.
# MAGIC 2. Expected drift signals to detect:
# MAGIC    - `charge_amount`: moderate PSI (~0.13) — lognormal shift mu 6.9 -> 7.2
# MAGIC    - `claim_type`: PROFESSIONAL share drops 60% -> 55%
# MAGIC    - `paid_amount`: zero-spike increases (denial rate 15% -> 20%)
# MAGIC 3. Repeat for `lab_result` (HbA1c mean 7.2 -> 8.1) and `observation` (BMI mean 27.5 -> 29.2).
# MAGIC 4. Reference tables (`location`, `practitioner`) should show **zero drift** — useful
# MAGIC    as a negative control to verify the profiler does not false-positive on stable tables.
# MAGIC
# MAGIC ### Re-running
# MAGIC
# MAGIC All generators use fixed seeds (`SEED_PROD`, `SEED_TEST`) — rerunning produces
# MAGIC identical data. Change seeds or drift parameters at the top of the notebook to
# MAGIC explore different scenarios.
