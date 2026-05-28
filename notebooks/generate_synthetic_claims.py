# Databricks notebook source

# COMMAND ----------

# MAGIC %md
# MAGIC # Synthetic Medical Claims Generator
# MAGIC
# MAGIC Generates two versions of a `medical_claims` table for the Synaptiq DQ Platform POC:
# MAGIC
# MAGIC | Side | Table | Rows |
# MAGIC |---|---|---|
# MAGIC | PROD baseline | `dev.prod_main_claims.medical_claims` | 50,000 |
# MAGIC | TEST drifted  | `dev.test_main_claims.medical_claims` | 50,000 |
# MAGIC
# MAGIC **Intentional drift baked in:**
# MAGIC
# MAGIC | Column | PROD | TEST | Expected PSI |
# MAGIC |---|---|---|---|
# MAGIC | `billed_amount` | lognormal μ=6.9 → mean ~$1,400 | lognormal μ=7.2 → mean ~$1,900 | ~0.13 (moderate) |
# MAGIC | `denial_rate` | 15% | 20% | ~0.05 (stable) |
# MAGIC | `claim_type` mix | 60% PROFESSIONAL | 55% PROFESSIONAL | &lt;0.05 (stable) |
# MAGIC
# MAGIC Run once to populate tables, then point the profiler at them.

# COMMAND ----------

import random
from datetime import date, timedelta

import numpy as np
import pandas as pd

SEED    = 42
CATALOG = "dev"
N_ROWS  = 50_000
TABLE   = "medical_claims"

# COMMAND ----------

# MAGIC %md
# MAGIC ## Reference data

# COMMAND ----------

DIAGNOSIS_CODES = [
    "M54.5",    # Low back pain
    "I10",      # Essential hypertension
    "E11.9",    # Type 2 diabetes
    "J06.9",    # Acute upper respiratory infection
    "K21.0",    # GERD
    "Z00.00",   # Adult health exam
    "Z12.31",   # Screening mammogram
    "F32.9",    # Major depressive disorder
    "J44.1",    # COPD with exacerbation
    "N39.0",    # UTI
    "Z23",      # Immunization encounter
    "R05",      # Cough
    "R51",      # Headache
    "G43.909",  # Migraine
    "E78.5",    # Hyperlipidemia
    "I25.10",   # Coronary artery disease
    "J18.9",    # Pneumonia
    "S93.401",  # Ankle sprain
    "M17.11",   # Primary osteoarthritis, right knee
    "Z87.891",  # Nicotine dependence history
]

PROCEDURE_CODES = [
    "99213",  # Office visit, established, low complexity
    "99214",  # Office visit, established, moderate complexity
    "99203",  # Office visit, new patient
    "99232",  # Hospital subsequent visit
    "99283",  # ED visit, moderate severity
    "93000",  # ECG routine
    "71046",  # Chest X-ray, 2 views
    "36415",  # Venipuncture
    "80053",  # Comprehensive metabolic panel
    "85025",  # CBC with differential
    "80061",  # Lipid panel
    "82947",  # Blood glucose
    "97110",  # Therapeutic exercises
    "97140",  # Manual therapy
    "27447",  # Total knee replacement
    "66984",  # Cataract surgery
    "45378",  # Colonoscopy
    "99395",  # Preventive visit, 18-39
    "93798",  # Cardiac rehab
    "76817",  # OB ultrasound
]

DENIAL_CODES = [
    "CO-4",    # Service not covered
    "CO-97",   # Payment included in prior claim
    "CO-16",   # Claim lacks information
    "PR-1",    # Deductible amount
    "PR-2",    # Coinsurance amount
    "CO-22",   # Coordination of benefits
    "CO-50",   # Non-covered service
    "CO-167",  # Diagnosis not covered
]

PLACE_OF_SERVICE         = ["11", "21", "22", "23", "31", "81"]
PLACE_OF_SERVICE_WEIGHTS = [0.60, 0.12, 0.13, 0.08, 0.04, 0.03]

COPAY_OPTIONS = [0.0, 20.0, 30.0, 40.0, 50.0, 75.0, 100.0]
COPAY_WEIGHTS = [0.30, 0.25, 0.20, 0.10, 0.08, 0.05, 0.02]

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generator function

# COMMAND ----------

def generate_claims(
    n: int,
    env_tag: str,
    billed_mu: float,
    billed_sigma: float,
    denial_rate: float,
    claim_type_weights: list,
    date_start: date,
    date_end: date,
    seed: int = 42,
) -> pd.DataFrame:
    """Return a Pandas DataFrame of n synthetic medical claims rows.

    env_tag is embedded in claim_id to distinguish PROD vs TEST rows.
    billed_mu / billed_sigma control the lognormal distribution of billed_amount.
    denial_rate drives claim_status mix (DENIED + downstream null cascade).
    """
    rng = np.random.default_rng(seed)
    random.seed(seed)

    n_members   = 8_000
    n_providers = 400
    members   = [f"MBR-{i:06d}" for i in range(n_members)]
    providers = [f"NPI-{i:09d}" for i in range(n_providers)]

    claim_types    = ["PROFESSIONAL", "OUTPATIENT", "INPATIENT", "ER"]
    paid_rate      = max(0.0, 1.0 - denial_rate - 0.07)
    status_weights = [paid_rate, denial_rate, 0.05, 0.02]
    statuses       = ["PAID", "DENIED", "PENDING", "REVERSED"]
    date_range     = (date_end - date_start).days

    # Vectorised random draws
    billed_raw   = rng.lognormal(billed_mu, billed_sigma, n)
    billed_raw   = np.clip(billed_raw, 10.0, 500_000.0).round(2)
    member_idx   = rng.integers(0, n_members, n)
    provider_idx = rng.integers(0, n_providers, n)
    day_offsets  = rng.integers(0, date_range, n)
    ct_idx       = rng.choice(len(claim_types), n, p=claim_type_weights)
    st_idx       = rng.choice(len(statuses), n, p=status_weights)
    pos_idx      = rng.choice(len(PLACE_OF_SERVICE), n, p=PLACE_OF_SERVICE_WEIGHTS)
    diag_idx     = rng.integers(0, len(DIAGNOSIS_CODES), n)
    proc_idx     = rng.integers(0, len(PROCEDURE_CODES), n)
    line_counts  = rng.choice([1, 2, 3, 4, 5], n, p=[0.60, 0.20, 0.10, 0.06, 0.04])
    allowed_pct  = rng.uniform(0.60, 0.95, n)
    paid_pct     = rng.uniform(0.70, 1.00, n)
    copay_idx    = rng.choice(len(COPAY_OPTIONS), n, p=COPAY_WEIGHTS)
    ded_flag     = rng.random(n) < 0.30
    ded_amt      = rng.uniform(0, 500, n).round(2)
    paid_lag     = rng.integers(14, 60, n)

    records = []
    for i in range(n):
        svc_date = date_start + timedelta(days=int(day_offsets[i]))
        status   = statuses[st_idx[i]]
        billed   = float(billed_raw[i])

        allowed    = round(billed * float(allowed_pct[i]), 2) if status != "DENIED" else None
        paid       = round((allowed or 0.0) * float(paid_pct[i]), 2) if status == "PAID" else None
        paid_date  = svc_date + timedelta(days=int(paid_lag[i])) if status == "PAID" else None
        denial_code = random.choice(DENIAL_CODES) if status == "DENIED" else None
        copay      = COPAY_OPTIONS[copay_idx[i]] if status == "PAID" else None
        deductible = float(ded_amt[i]) if (status == "PAID" and ded_flag[i]) else None

        records.append({
            "claim_id":          f"CLM-{env_tag}-{i + 1:06d}",
            "member_id":         members[member_idx[i]],
            "provider_npi":      providers[provider_idx[i]],
            "service_date":      svc_date,
            "paid_date":         paid_date,
            "claim_type":        claim_types[ct_idx[i]],
            "place_of_service":  PLACE_OF_SERVICE[pos_idx[i]],
            "primary_diagnosis": DIAGNOSIS_CODES[diag_idx[i]],
            "procedure_code":    PROCEDURE_CODES[proc_idx[i]],
            "claim_status":      status,
            "billed_amount":     billed,
            "allowed_amount":    allowed,
            "paid_amount":       paid,
            "denial_code":       denial_code,
            "copay_amount":      copay,
            "deductible_amount": deductible,
            "line_count":        int(line_counts[i]),
            "created_date":      svc_date,
        })

    return pd.DataFrame(records)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generate PROD (baseline)

# COMMAND ----------

spark.sql("CREATE SCHEMA IF NOT EXISTS dev.prod_main_claims")

pdf_prod = generate_claims(
    n                  = N_ROWS,
    env_tag            = "PROD",
    billed_mu          = 6.9,               # mean billed ~$1,400
    billed_sigma       = 0.80,
    denial_rate        = 0.15,
    claim_type_weights = [0.60, 0.25, 0.10, 0.05],
    date_start         = date(2024, 1, 1),
    date_end           = date(2024, 12, 31),
    seed               = 42,
)

sdf_prod = spark.createDataFrame(pdf_prod)
(sdf_prod.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"`{CATALOG}`.`prod_main_claims`.`{TABLE}`"))

print(f"PROD written: {sdf_prod.count():,} rows")
display(sdf_prod.describe("billed_amount", "allowed_amount", "paid_amount"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Generate TEST (drifted)
# MAGIC
# MAGIC Changes vs PROD:
# MAGIC - `billed_amount` μ: 6.9 → 7.2 (mean ~$1,400 → ~$1,900) — **moderate drift**
# MAGIC - Denial rate: 15% → 20% — **stable / borderline**
# MAGIC - PROFESSIONAL weight: 60% → 55% — **stable**
# MAGIC - Date range shifted forward 6 months — represents a newer data cut

# COMMAND ----------

spark.sql("CREATE SCHEMA IF NOT EXISTS dev.test_main_claims")

pdf_test = generate_claims(
    n                  = N_ROWS,
    env_tag            = "TEST",
    billed_mu          = 7.2,               # mean billed ~$1,900 — intentional drift
    billed_sigma       = 0.85,
    denial_rate        = 0.20,              # higher denial rate
    claim_type_weights = [0.55, 0.28, 0.12, 0.05],
    date_start         = date(2024, 7, 1),
    date_end           = date(2025, 3, 31),
    seed               = 99,               # different seed = different member/provider mix
)

sdf_test = spark.createDataFrame(pdf_test)
(sdf_test.write
    .format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"`{CATALOG}`.`test_main_claims`.`{TABLE}`"))

print(f"TEST written: {sdf_test.count():,} rows")
display(sdf_test.describe("billed_amount", "allowed_amount", "paid_amount"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validation

# COMMAND ----------

from pyspark.sql import functions as F

print("=== Row counts ===")
for schema in ("prod_main_claims", "test_main_claims"):
    n = spark.table(f"`{CATALOG}`.`{schema}`.`{TABLE}`").count()
    print(f"  {schema}: {n:,}")

print("\n=== billed_amount distribution ===")
for label, schema in [("PROD", "prod_main_claims"), ("TEST", "test_main_claims")]:
    row = (spark.table(f"`{CATALOG}`.`{schema}`.`{TABLE}`")
           .agg(
               F.mean("billed_amount").alias("mean"),
               F.expr("percentile(billed_amount, 0.5)").alias("median"),
               F.stddev("billed_amount").alias("stddev"),
               F.min("billed_amount").alias("min"),
               F.max("billed_amount").alias("max"),
           ).collect()[0])
    print(f"  {label}  mean=${row.mean:>10,.0f}  median=${row.median:>10,.0f}"
          f"  stddev=${row.stddev:>10,.0f}  min=${row.min:>8,.0f}  max=${row.max:>10,.0f}")

print("\n=== claim_status mix ===")
for label, schema in [("PROD", "prod_main_claims"), ("TEST", "test_main_claims")]:
    print(f"  {label}:")
    (spark.table(f"`{CATALOG}`.`{schema}`.`{TABLE}`")
     .groupBy("claim_status").count()
     .withColumn("pct", F.round(F.col("count") / N_ROWS * 100, 1))
     .orderBy("count", ascending=False)
     .show())

print("\n=== claim_type mix ===")
for label, schema in [("PROD", "prod_main_claims"), ("TEST", "test_main_claims")]:
    print(f"  {label}:")
    (spark.table(f"`{CATALOG}`.`{schema}`.`{TABLE}`")
     .groupBy("claim_type").count()
     .withColumn("pct", F.round(F.col("count") / N_ROWS * 100, 1))
     .orderBy("count", ascending=False)
     .show())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Done
# MAGIC
# MAGIC Both tables are ready. In the Synaptiq DQ Platform UI:
# MAGIC
# MAGIC | Field | Side A (PROD) | Side B (TEST) |
# MAGIC |---|---|---|
# MAGIC | Catalog | `dev` | `dev` |
# MAGIC | Schema | `prod_main_claims` | `test_main_claims` |
# MAGIC | Table | `medical_claims` | `medical_claims` |
# MAGIC
# MAGIC Expected profiler output:
# MAGIC - `billed_amount` — **moderate drift** (PSI ~0.13), verdict = `moderate`
# MAGIC - `allowed_amount`, `paid_amount` — drift follows `billed_amount`
# MAGIC - `claim_status` — slight shift in DENIED rate, verdict = `stable`
# MAGIC - `denial_code` — ~80% null on both sides (expected for paid claims)
# MAGIC - `paid_date`, `allowed_amount`, `paid_amount` — nullable pattern shifts with denial rate
