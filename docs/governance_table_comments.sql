-- =============================================================================
-- Synaptiq Data Profiling Tool — Governance Table Comments
-- dev.test_main_profiler
--
-- Step 1: Run the GRANT block below as the schema owner (gary.fischer@synaptiq.ai)
--         to give yourself MODIFY on the SP-owned tables.
-- Step 2: Run the COMMENT and ALTER TABLE blocks to add descriptions.
-- =============================================================================


-- =============================================================================
-- STEP 1: Grant MODIFY to yourself (you own the schema, so this works)
-- =============================================================================

GRANT MODIFY ON TABLE dev.test_main_profiler.profiler_runs       TO `gary.fischer@synaptiq.ai`;
GRANT MODIFY ON TABLE dev.test_main_profiler.dataset_profiles    TO `gary.fischer@synaptiq.ai`;
GRANT MODIFY ON TABLE dev.test_main_profiler.column_profiles     TO `gary.fischer@synaptiq.ai`;
GRANT MODIFY ON TABLE dev.test_main_profiler.column_alerts       TO `gary.fischer@synaptiq.ai`;
GRANT MODIFY ON TABLE dev.test_main_profiler.column_comparisons  TO `gary.fischer@synaptiq.ai`;


-- =============================================================================
-- STEP 2: Table-level comments
-- =============================================================================

COMMENT ON TABLE dev.test_main_profiler.profiler_runs IS
  'One record per profiler run. A run compares two Unity Catalog tables (Side A vs Side B) and produces HTML profiles, an Excel workbook, Mermaid diagrams, and column-level drift metrics. Primary key: run_id.';

COMMENT ON TABLE dev.test_main_profiler.dataset_profiles IS
  'One record per side (A or B) per profiler run. Captures high-level statistics for the profiled dataset: row count, column count, and duplicate row count. Links to profiler_runs via run_id.';

COMMENT ON TABLE dev.test_main_profiler.column_profiles IS
  'One record per column per side per profiler run. Stores null rates, distinct value counts, numeric descriptive statistics (mean, std dev, percentiles, skewness), categorical top-K frequencies, Shannon entropy, and DQ stereotypes. Links to profiler_runs via run_id.';

COMMENT ON TABLE dev.test_main_profiler.column_alerts IS
  'One record per alert per column per side per profiler run. An alert fires when a column violates a data quality rule (e.g. high null rate, constant value, skewed distribution). Severity: critical, warn, or info. Links to profiler_runs via run_id.';

COMMENT ON TABLE dev.test_main_profiler.column_comparisons IS
  'One record per column comparison per profiler run. Stores cross-side drift metrics: PSI (Population Stability Index), KS statistic, Chi-square, Jensen-Shannon divergence, and a human-readable verdict (stable / moderate / significant / schema_change). Links to profiler_runs via run_id.';


-- =============================================================================
-- STEP 3: Column comments — profiler_runs
-- =============================================================================

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN run_id         COMMENT 'UUIDv7 (time-sortable) uniquely identifying this run. Used as the primary key for all governance tables.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN run_label      COMMENT 'Optional user-supplied label appended to the run folder name (e.g. pre-migration, baseline-2024).';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN metamodel_version COMMENT 'Version of the DQ metamodel schema used to produce this run (e.g. 1.0). Major bumps indicate breaking schema changes.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN created_utc    COMMENT 'UTC timestamp when the run was executed.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN created_date   COMMENT 'Date portion of created_utc. Used for partition filtering and date-range queries.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN side_a_fqn     COMMENT 'Fully qualified name of the baseline (Side A) table: catalog.schema.table.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN side_b_fqn     COMMENT 'Fully qualified name of the candidate (Side B) table: catalog.schema.table.';

ALTER TABLE dev.test_main_profiler.profiler_runs
  ALTER COLUMN lineage_json   COMMENT 'JSON object pointing to sibling output artifacts in the run folder: HTML profiles, Excel workbook, Mermaid diagram filenames, manifest.';


-- =============================================================================
-- STEP 4: Column comments — dataset_profiles
-- =============================================================================

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN run_id         COMMENT 'Foreign key to profiler_runs.run_id.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN side           COMMENT 'Which side of the comparison: A (baseline) or B (candidate).';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN created_date   COMMENT 'Date the run was executed.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN env_label      COMMENT 'Environment label assigned by the user at run time (e.g. PROD, TEST, QA).';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN connection     COMMENT 'Connection name used to reach this catalog (from connections.yaml).';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN catalog        COMMENT 'Unity Catalog catalog name of the profiled table.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN schema         COMMENT 'Unity Catalog schema (database) name of the profiled table.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN table          COMMENT 'Table name of the profiled table.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN row_count      COMMENT 'Number of rows in the profiled sample (up to FETCH_LIMIT). May be less than the actual table row count for large tables.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN column_count   COMMENT 'Number of columns profiled.';

ALTER TABLE dev.test_main_profiler.dataset_profiles
  ALTER COLUMN duplicate_rows COMMENT 'Count of exact duplicate rows in the sample. Set to 0 for wide tables (>30 cols) or large samples (>50k rows) where the check is skipped for performance.';


-- =============================================================================
-- STEP 5: Column comments — column_profiles
-- =============================================================================

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN run_id         COMMENT 'Foreign key to profiler_runs.run_id.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN side           COMMENT 'Which side: A (baseline) or B (candidate).';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN created_date   COMMENT 'Date the run was executed.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN catalog        COMMENT 'Unity Catalog catalog name.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN schema         COMMENT 'Unity Catalog schema name.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN table          COMMENT 'Table name.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN column_name    COMMENT 'Name of the profiled column.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN logical_type   COMMENT 'Logical data type: integer, bigint, double, float, string, boolean, date, timestamp.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN physical_type  COMMENT 'Physical storage type as reported by the SQL connector (e.g. BIGINT, STRING, DOUBLE).';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN nullable       COMMENT 'True if the column allows NULL values.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN null_count     COMMENT 'Number of NULL values in the profiled sample.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN null_pct       COMMENT 'Fraction of rows that are NULL (0.0 to 1.0). Example: 0.05 = 5% null.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN distinct_count COMMENT 'Approximate count of distinct non-null values using approx_count_distinct. Exact for small tables.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN distinct_pct   COMMENT 'Fraction of rows with a distinct value (0.0 to 1.0). Values near 1.0 suggest a natural key or ID column.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN stereotypes     COMMENT 'Comma-separated DQ stereotypes detected for this column: NullSpike (>50% null), Constant (1 distinct value), HighCardinality (>90% distinct on categorical), Skewed (|skewness|>2), Imbalanced (top value >90% of rows).';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN numeric_mean   COMMENT 'Mean (average) value. Populated for numeric columns only; NULL for string, boolean, and temporal columns.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN numeric_stddev COMMENT 'Standard deviation. Low values indicate a narrow distribution; high values indicate spread. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN numeric_p50    COMMENT 'Median (50th percentile). More robust than mean for skewed distributions. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN numeric_min    COMMENT 'Minimum observed value in the sample. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN numeric_max    COMMENT 'Maximum observed value in the sample. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN cat_entropy    COMMENT 'Shannon entropy in bits measuring value distribution diversity. 0 = constant (one value), higher = more uniform distribution. Categorical and boolean columns only.';

ALTER TABLE dev.test_main_profiler.column_profiles
  ALTER COLUMN cat_top_k_json COMMENT 'JSON object of the top-20 most frequent non-null values and their row counts. Example: {"PAID": 37500, "DENIED": 7500}. Categorical columns only.';


-- =============================================================================
-- STEP 6: Column comments — column_alerts
-- =============================================================================

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN run_id         COMMENT 'Foreign key to profiler_runs.run_id.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN side           COMMENT 'Which side triggered the alert: A (baseline) or B (candidate).';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN created_date   COMMENT 'Date the run was executed.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN catalog        COMMENT 'Unity Catalog catalog name.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN schema         COMMENT 'Unity Catalog schema name.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN table          COMMENT 'Table name.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN column_name    COMMENT 'Column that triggered the alert.';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN rule           COMMENT 'Alert rule that fired. Values: missing (high null rate), constant (single distinct value), high_cardinality (>90% unique on categorical), unique (every value is unique), skewed (|skewness|>2), imbalanced (one value dominates >90%).';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN severity       COMMENT 'Alert severity: critical (>80% null or all null), warn (>50% null, constant, imbalanced), info (high cardinality, unique, skewed).';

ALTER TABLE dev.test_main_profiler.column_alerts
  ALTER COLUMN message        COMMENT 'Human-readable description of the alert condition, including specific values (e.g. "15.3% null (7,650 of 50,000 rows)").';


-- =============================================================================
-- STEP 7: Column comments — column_comparisons
-- =============================================================================

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN run_id         COMMENT 'Foreign key to profiler_runs.run_id.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN created_date   COMMENT 'Date the run was executed.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN catalog_a      COMMENT 'Catalog of the baseline (Side A) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN schema_a       COMMENT 'Schema of the baseline (Side A) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN table_a        COMMENT 'Table name of the baseline (Side A) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN catalog_b      COMMENT 'Catalog of the candidate (Side B) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN schema_b       COMMENT 'Schema of the candidate (Side B) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN table_b        COMMENT 'Table name of the candidate (Side B) table.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN column_name    COMMENT 'Name of the column being compared across both sides.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN schema_change  COMMENT 'Type of schema change between sides: unchanged (same type and nullability), added (only in Side B), removed (only in Side A), type_changed (different data type), nullability_changed (nullable flag changed).';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN psi            COMMENT 'Population Stability Index — measures how much the distribution of this column has shifted between Side A and Side B. Thresholds: < 0.10 = stable (no meaningful drift), 0.10–0.20 = moderate drift (monitor), ≥ 0.20 = significant drift (investigate). NULL for schema-changed columns.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN ks_stat        COMMENT 'Kolmogorov-Smirnov test statistic — the maximum absolute difference between the empirical CDFs of Side A and Side B. Range 0 to 1; higher = more different distributions. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN ks_pvalue      COMMENT 'KS test p-value — probability that Side A and Side B were drawn from the same distribution. Low values (< 0.05) indicate statistically significant distributional differences. Numeric columns only.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN chi_square     COMMENT 'Chi-square test statistic — measures how much the observed frequency distribution in Side B deviates from the expected distribution in Side A. Larger values = more deviation. Categorical columns only.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN js_divergence  COMMENT 'Jensen-Shannon divergence in bits — a symmetric measure of the difference between two probability distributions. Range 0 to 1: 0 = identical distributions, 1 = completely non-overlapping distributions. Applies to both numeric and categorical columns.';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN verdict        COMMENT 'Human-readable drift verdict derived from PSI. Values: stable (PSI < 0.10), moderate (PSI 0.10–0.20), significant (PSI ≥ 0.20), schema_change (column was added, removed, or type-changed).';

ALTER TABLE dev.test_main_profiler.column_comparisons
  ALTER COLUMN stereotypes     COMMENT 'Comma-separated comparison stereotypes: Drifted (PSI ≥ 0.20, significant distribution shift), SchemaChanged (column structure differs between sides).';
