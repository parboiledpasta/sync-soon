# Tiered Safety DAX-to-Snowflake Translation Engine

## Overview

The Tiered Safety engine is SemaBridge's formal classification and override system for
translating complex DAX measures from Power BI (VertiPaq) to Snowflake Semantic Views. It
ensures that only safe, automatable DAX logic is converted automatically, while
context-dependent or structurally hazardous measures are flagged for manual SQL override.

## Architecture

```
DAX Measures (SML Model)
         │
         ▼
┌─────────────────────────┐
│  TieredSafetyClassifier │  ← Classifies each measure into Tiers 1–4
│  (tiered_safety.py)     │
└────────┬────────────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
 Tier 1/2   Tier 3/4
 (Auto)     (Override Required)
    │         │
    ▼         ▼
┌──────────┐  ┌─────────────────┐
│DAXTransl.│  │OverrideGenerator│  ← Generates 3-layer override files
│(existing)│  │(override_gen.py)│
└──────────┘  └────────┬────────┘
                       │
              ┌────────┼────────┐
              ▼        ▼        ▼
          Layer 1   Layer 2  Layer 3
          Dynamic   Semantic Cortex
          Table     View     Metadata
              │        │        │
              ▼        ▼        ▼
        ┌──────────────────────────┐
        │    OverrideValidator     │  ← Validates completeness & SQL
        │  (override_validator.py) │
        └──────────────────────────┘
              │
              ▼
        ┌──────────────────────────┐
        │  TieredSafetyPipeline    │  ← Orchestrates full workflow
        │  (safety_pipeline.py)    │
        └──────────────────────────┘
```

## Safety Tiers

| Tier | Name                     | Automation Level   | Description                                            |
|------|--------------------------|--------------------|--------------------------------------------------------|
| 1    | Deterministic Translation| Fully Automated    | Direct SQL mapping (SUM, COUNT, DISTINCTCOUNT, etc.)   |
| 2    | Conditional Equivalency  | Partially Automated| Parameter mapping, simple arithmetic (DIVIDE, IF)      |
| 3    | Contextual Dissonance    | Manual Override    | Complex filter/time contexts (CALCULATE+FILTER, SUMX)  |
| 4    | Structural Hazard        | Manual Override    | Nested row contexts, relationship mods (RANKX, EARLIER)|

## Hazard Categories (Tier 3 & 4)

### Category 1: Complex Filter Contexts
- **Functions:** `CALCULATE+FILTER`, `CALCULATE+ALL`, `ALLEXCEPT`, `ALLSELECTED`, `KEEPFILTERS`
- **Problem:** Dynamic filter context cannot be predicted at view-creation time
- **Override Strategy:** Pre-compute filtered subsets in Dynamic Tables with explicit WHERE clauses

### Category 2: Row Context Iterators
- **Functions:** `SUMX`, `AVERAGEX`, `MINX`, `MAXX`, `COUNTX`, `CONCATENATEX`, `ADDCOLUMNS`
- **Problem:** Row-by-row evaluation generates correlated subqueries
- **Override Strategy:** Pre-aggregate row-level expressions in Dynamic Tables

### Category 3: Advanced Time Intelligence
- **Functions:** `PARALLELPERIOD`, `DATEADD`, `SAMEPERIODLASTYEAR`, `PREVIOUSYEAR/MONTH/QUARTER`
- **Problem:** Context-aware temporal shifting not available in SQL scalar functions
- **Override Strategy:** Materialized calendar dimension with temporal offset columns

### Category 4: Dynamic Relationship Modifiers
- **Functions:** `USERELATIONSHIP`, `CROSSFILTER`, `TREATAS`
- **Problem:** Snowflake Semantic Views require static join paths
- **Override Strategy:** Physical table aliases with separate static JOIN paths

### Category 5: Specialized Built-in Logic
- **Functions:** `RANKX`, `EARLIER`, `EARLIEST`, `TOPN`, `GENERATE`, `PATH`
- **Problem:** No SQL equivalent for nested imperative row contexts
- **Override Strategy:** Window functions (RANK, ROW_NUMBER, DENSE_RANK) with explicit PARTITION BY

## Override File Structure

Each Manual SQL Override file (YAML) follows a three-layer architecture:

```yaml
# Override Metadata
metric_name: "OptOut_Rank_Measure"
safety_tier: 4
hazard_category: "specialized_builtin_logic"

# Layer 1: Dynamic Table (materialized pre-computation)
layer_1_dynamic_table:
  table_name: "optout_event_sequence_dt"
  target_lag: "1 hour"
  warehouse: "ANALYTICS_COMPUTE_WH"
  source_table: "raw_marketing_events_vue"
  window_functions:
    - function: "RANK"
      partition_by: ["user_id", "type_id"]
      order_by: ["date_received"]
      alias: "optout_chronological_rank"

# Layer 2: Semantic View (metric projection)
layer_2_semantic_view:
  view_name: "marketing_optout_analytics_sv"
  metrics:
    - name: "primary_user_optouts"
      expression: "COUNT(CASE WHEN rank = 1 AND value = 0 THEN user_id END)"

# Layer 3: Cortex Analyst Metadata
layer_3_cortex_metadata:
  metric_synonyms:
    - column_name: "primary_user_optouts"
      synonyms: ["first time unsubscribes", "initial opt outs"]
```

## Usage

### Pipeline Integration

```python
from pathlib import Path
from semabridge.converter.safety_pipeline import TieredSafetyPipeline

pipeline = TieredSafetyPipeline(
    override_dir=Path("output/manual_sql_overrides"),
    database="ANALYTICS_DB",
    schema="SEMANTIC",
)

# Run on an SML model
result = pipeline.run(sml_model)

# Check results
print(f"Auto-translatable: {result.auto_translatable_count}")
print(f"Override required: {result.override_required_count}")
print(f"Override coverage: {result.override_coverage}%")

# Write report
pipeline.write_safety_report(result, Path("output/reports"))
```

### Standalone Classification

```python
from semabridge.converter.tiered_safety import TieredSafetyClassifier

classifier = TieredSafetyClassifier()

# Classify a single measure
result = classifier.classify("OptOut_Rank", "RANKX(FILTER(...), ...)")
print(f"Tier: {result.tier.value}")
print(f"Hazard: {result.primary_hazard.value}")
print(f"Override required: {result.requires_override}")

# Batch classify
measures = {"Revenue": "SUM([Amount])", "Rank": "RANKX(...)"}
results = classifier.classify_all(measures)
summary = classifier.get_summary(results)
```

### Override Generation

```python
from semabridge.converter.override_generator import OverrideGenerator

generator = OverrideGenerator(database="MY_DB", schema="SEMANTIC")
override = generator.generate_override(classification, source_model="MyModel")

# Write YAML and SQL
generator.write_override_yaml(override, Path("output/overrides"))
generator.write_sql(override, Path("output/overrides"))

# Render SQL DDL
sql = generator.render_sql(override)
print(sql)
```

### Override Validation

```python
from semabridge.converter.override_validator import OverrideValidator

validator = OverrideValidator()

# Validate single file
result = validator.validate_file(Path("override_my_metric.yaml"))
print(result.summary())

# Validate entire directory
results = validator.validate_directory(Path("output/overrides"))
```

## File Locations

| File | Purpose |
|------|---------|
| `src/semabridge/converter/tiered_safety.py` | Safety classification engine |
| `src/semabridge/converter/override_schema.py` | Override file Pydantic models |
| `src/semabridge/converter/override_generator.py` | Override file & SQL generator |
| `src/semabridge/converter/override_validator.py` | Override validation & loading |
| `src/semabridge/converter/safety_pipeline.py` | Full pipeline orchestration |
| `tests/test_tiered_safety.py` | Comprehensive test suite |
| `output/manual_sql_overrides/` | Generated override files |
| `output/reports/tiered_safety_report.json` | Safety report output |
