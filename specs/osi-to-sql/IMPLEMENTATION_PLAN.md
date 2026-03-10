# OSI‑to‑SQL Implementation Plan

> **Status**: Phase 1 complete — core module implemented  
> **Module**: `src/semabridge/converter/osi_to_sql.py`  
> **Entry point**: `convert_osi_to_sql(osi_model, dialect="snowflake", ...)`

---

## 1  Motivation

The existing pipeline uses two hops to reach SQL:

```
Source → OSI → SML → Snowflake Emitter (SQL)
```

Introducing **OSI → SQL** creates a parallel, shorter path:

```
Source → OSI → SQL   (direct)
```

This is valuable for:

| Use Case | Why |
|----------|-----|
| Quick previews | Generate DDL without persisting SML |
| Multi‑dialect support | Add Databricks / BigQuery / Postgres dialects alongside Snowflake |
| CI/CD dry‑runs | Validate generated SQL in pull requests without a live target |
| Testing | Unit‑test SQL output from canonical OSI models |

## 2  Architecture

```
                  ┌────────────┐
Source → OSI ────►│ osi_to_sml │───► SML ──► SnowflakeEmitter
                  └────────────┘
              ┌────────────────┐
         └───►│ osi_to_sql     │───► SQL DDL
              └────────────────┘
```

Both converters consume the same `OSIModel`. The SML path remains
the production deployment path; the SQL path is additive.

## 3  Module Design

### 3.1  Public API

```python
from semabridge.converter.osi_to_sql import convert_osi_to_sql, OSIToSQLResult

result: OSIToSQLResult = convert_osi_to_sql(
    osi_model,
    dialect="snowflake",       # "snowflake" | "ansi" (extensible)
    database="ANALYTICS_DB",
    schema="SEMANTIC_LAYER",
    include_source_tables=True,
    llm_config=False,          # or LLMProjectConfig for Tier 4
)

print(result.semantic_view_ddl)   # CREATE OR REPLACE VIEW ...
print(result.source_table_ddls)   # [CREATE TABLE IF NOT EXISTS ...]
print(result.metric_expressions)  # {"Revenue": "SUM(FACT.\"REVENUE\")", ...}
print(result.warnings)            # ["Metric 'X' could not be translated"]
```

### 3.2  `OSIToSQLResult`

| Field | Type | Description |
|-------|------|-------------|
| `semantic_view_ddl` | `str` | The main view DDL |
| `source_table_ddls` | `List[str]` | Table DDLs (backing tables) |
| `metric_expressions` | `Dict[str, str]` | Metric name → SQL expression |
| `dialect` | `str` | Target dialect used |
| `warnings` | `List[str]` | Non‑fatal issues |
| `all_ddl` | `str` (property) | All statements concatenated |

### 3.3  Internal converter class

`_OSIToSQLConverter` (private) holds the stateful conversion:

1. **`_generate_table_ddl(ds)`** — `CREATE TABLE IF NOT EXISTS` for each dataset
2. **`_translate_metrics()`** — resolves SQL for every metric via:
   - Pre‑existing `sql_expression` on OSIMetric
   - `dialects` list (multi‑dialect override)
   - Simple aggregation (`source_column` + `aggregation`)
   - DAXTranslator (Tier 1‑4 rule/LLM translation)
3. **`_generate_semantic_view()`** — builds the star‑schema view with JOINs from relationships

## 4  Dialect Extensibility

Adding a new dialect requires two additions:

1. A new entry in `_TYPE_MAP` (data type mapping)
2. A new entry in `_dialect_tag()` (dialect ↔ `OSIExpressionDialect.dialect` mapping)

```python
_TYPE_MAP["databricks"] = {
    OSIDataType.STRING: "STRING",
    OSIDataType.INTEGER: "BIGINT",
    ...
}
```

No code changes in the converter logic itself.

## 5  Phased Delivery

### Phase 1 — Core (✅ Complete)

- [x] `convert_osi_to_sql()` function
- [x] `OSIToSQLResult` container
- [x] Internal `_OSIToSQLConverter`
- [x] Snowflake + ANSI dialect support
- [x] Type mapping, aggregation mapping
- [x] Metric translation (override → dialect → simple‑agg → DAX)
- [x] Semantic view generation with JOINs
- [x] Exported from `converter/__init__.py`

### Phase 2 — Testing & Validation

- [ ] Unit tests for `convert_osi_to_sql()` with Snowflake + ANSI
- [ ] Test metric translation across all 5 tiers (0‑4)
- [ ] Test JOIN generation from OSI relationships
- [ ] Test type mapping for all OSI data types
- [ ] Test warning generation for untranslatable metrics
- [ ] Integration test: TMSL → OSI → SQL round‑trip
- [ ] Compare SQL output against existing SnowflakeEmitter output

### Phase 3 — CLI Integration

- [ ] Add `--sql-only` flag to `semantic-sync` to output DDL without deploying
- [ ] Add `osi-to-sql` standalone CLI command
- [ ] Add `--dialect` flag (default: snowflake)
- [ ] Save generated SQL to `output/sql/` directory

### Phase 4 — Databricks Dialect

- [ ] Add `databricks` type map
- [ ] Add Databricks‑specific view syntax (Unity Catalog)
- [ ] Test with Databricks SQL Warehouse
- [ ] Document in connector docs

### Phase 5 — Production Hardening

- [ ] SQL injection guardrails on identifier sanitization
- [ ] Quoted identifier handling for reserved words
- [ ] Performance: batch metric translation
- [ ] Dry‑run mode: validate against live schema without executing
- [ ] Cortex Analyst YAML generation from OSI (parallel to SQL)

## 6  Relationship to Existing Code

| Component | Role |
|-----------|------|
| `osi_to_sml.py` | OSI → SML (unchanged, still production path) |
| `convert_osi_to_sml()` | New standalone function wrapping `OSIToSMLConverter` |
| `osi_to_sql.py` | **NEW** — OSI → SQL direct path |
| `snowflake_emitter.py` | SML → Snowflake DDL (existing deploy engine) |
| `dax_translator.py` | Shared: translates DAX to SQL for both paths |

## 7  Testing Strategy

Tests go in `tests/test_osi_to_sql.py`:

```
TestOSIToSQL
├── test_snowflake_table_ddl        — correct CREATE TABLE syntax
├── test_ansi_table_ddl             — ANSI type mapping
├── test_simple_metric_translation  — SUM/COUNT/AVG
├── test_override_metric            — sql_expression pass‑through
├── test_dialect_metric             — multi‑dialect selection
├── test_dax_metric_translation     — DAXTranslator Tier 1‑2
├── test_semantic_view_with_joins   — relationship → LEFT JOIN
├── test_unsupported_dialect        — ConversionError raised
├── test_empty_model                — graceful fallback
├── test_warnings_for_failed        — untranslatable metrics
└── test_all_ddl_property           — concatenated output
```

## 8  Migration Path

The existing SML‑based deployment path (`osi → sml → emitter`) will
**continue to be the default**. The OSI→SQL path is **opt‑in**:

1. Callers use `convert_osi_to_sql()` for preview / dry‑run
2. When ready, the CLI gets `--emit-mode sql` to switch paths
3. Both paths consume the same `OSIModel`, guaranteeing consistency
