# SemaBridge - System Limitations and Constraints

**Last Updated: February 2026**

This document outlines the current limitations, constraints, and known issues in SemaBridge to help users understand what is and isn't supported across different platforms.

---

## Platform Support

### Currently Supported Platforms ✅
- **Snowflake** - Full support (source & target)
- **Microsoft Fabric (Power BI)** - Full support (source & target)

### Planned Platforms 📋
- **Databricks Unity Catalog** - Planned Q2 2026
- **Additional Platforms** - Extensible via plugin system

### Platform-Specific Limitations
Each platform has unique constraints that SemaBridge handles through its format system and behavior policies. See sections below for platform-specific details.

---

## 1. DAX Translation Limitations

SemaBridge uses a **3-tier translation system** for converting DAX measures to SQL:

### Tier 1: Full Translation ✅
**Supported - Generates complete SQL**

- Simple aggregations: `SUM()`, `AVG()`, `COUNT()`, `MIN()`, `MAX()`
- Basic arithmetic: `+`, `-`, `*`, `/`
- Column references
- Table references

**Example:**
```dax
Total Revenue = SUM(Sales[Amount])
```
→ Translates to: `SUM("SALES"."AMOUNT")`

### Tier 2: Partial Translation ⚠️
**Partially Supported - Generates SQL with limitations**

- `CALCULATE()` with simple filters
- Basic filter expressions
- Single-table contexts

**Limitations:**
- Complex filter contexts may not translate correctly
- Multiple filter conditions may be simplified
- Context transition not fully supported

### Tier 3: Metadata Only ❌
**Not Supported - Stores as metadata only**

- Time intelligence functions (`SAMEPERIODLASTYEAR`, `DATEADD`, `TOTALYTD`, etc.)
- Complex `CALCULATE()` with multiple contexts
- Iterator functions (`SUMX`, `AVERAGEX`, `FILTER`, etc.)
- Relationship functions (`RELATED`, `RELATEDTABLE`, `USERELATIONSHIP`)
- Variables (`VAR`, `RETURN`)
- Nested calculations
- Custom aggregations

**Behavior:**
- Measure is stored in SML with original DAX
- No SQL equivalent generated
- Marked as "complex" in metadata
- Can be manually overridden via behavior policy

### Manual Override Solution

For Tier 3 measures, use the **metric_overrides** in behavior policy:

```yaml
semantic_model:
  metric_overrides:
    "Revenue SPLY": "LAG(SUM(FACT.\"REVENUE\"), 12) OVER (PARTITION BY PRODUCT.\"PRODUCT_KEY\" ORDER BY CALENDAR.\"YEARPERIOD\")"
    "YTD Revenue": "SUM(CASE WHEN CALENDAR.\"DATE\" <= CURRENT_DATE THEN FACT.\"REVENUE\" ELSE 0 END)"
```

---

## 2. Snowflake Identifier Constraints

### Reserved Words
Snowflake has reserved keywords that cannot be used as unquoted identifiers.

**Automatic Handling:**
- Reserved words are prefixed with `L_` (e.g., `TABLE` → `L_TABLE`)
- Controlled by `compatibility.suppress_reserved_words` in behavior policy

**Common Reserved Words:**
- `TABLE`, `VIEW`, `SELECT`, `FROM`, `WHERE`, `ORDER`, `GROUP`
- `DATE`, `TIME`, `TIMESTAMP`, `INTERVAL`
- `USER`, `ROLE`, `SCHEMA`, `DATABASE`

### Identifier Length
- **Maximum length:** 255 characters
- Identifiers exceeding this are truncated with hash suffix

### Special Characters
- **Spaces:** Require double quotes (`"My Column"`)
- **Mixed case:** Requires double quotes to preserve case
- **Default behavior:** Force uppercase (configurable via `compatibility.force_uppercase`)

### Quoting Rules
Controlled by `snowflake.quote_identifiers` in behavior policy:
- `true` (default): All identifiers quoted
- `false`: Only quote when necessary (spaces, reserved words, mixed case)

---

## 3. Microsoft Fabric API Limitations

### Rate Limits
- **API calls:** Subject to Azure AD throttling
- **Large models:** May timeout on extraction (>1000 tables)
- **Concurrent requests:** Limited by tenant configuration

### Model Size Constraints
- **Maximum tables:** ~1000 tables per model (Fabric limit)
- **Maximum measures:** ~1000 measures per model
- **Maximum relationships:** ~1000 relationships per model

### Authentication
- **Service Principal only:** User authentication not supported
- **Requires:** `FABRIC_TENANT_ID`, `FABRIC_CLIENT_ID`, `FABRIC_CLIENT_SECRET`
- **Permissions needed:** 
  - `Dataset.Read.All`
  - `Dataset.ReadWrite.All` (for deployment)

### TMSL Generation
- **Compatibility mode:** May not support all Fabric features
- **Custom visuals:** Not preserved
- **Report-level measures:** Not extracted
- **Calculation groups:** Limited support

---

## 4. DuckDB Version Control Limitations

### Storage
- **Embedded database:** Single file (`semabridge.db`)
- **No distributed storage:** Cannot share across machines without file sharing
- **Size limits:** Practical limit ~100GB (depends on disk space)

### Snapshot Behavior
- **Immutable snapshots:** Cannot modify historical snapshots
- **No branching:** Linear version history only
- **Snapshot size:** Full model stored per snapshot (no delta compression)

### Rollback Constraints
- **Non-destructive:** Creates new snapshot, doesn't delete history
- **Target deployment required:** Rollback requires re-deploying to target system
- **No automatic rollback:** Manual intervention required

---

## 5. Performance Considerations

### Extraction Performance
- **Snowflake:** 
  - Large schemas (>500 tables): 5-10 minutes
  - `INFORMATION_SCHEMA` queries can be slow
  - Caching available via `model.cache_enabled`

- **Fabric:**
  - Model extraction: 30-60 seconds per model
  - Row count queries: Additional 10-30 seconds
  - API throttling may slow large extractions

### Transformation Performance
- **SML Assembly:** O(n²) for relationship detection
- **Measure Detection:** O(n) per table
- **Hierarchy Detection:** O(n) per table

### Deployment Performance
- **Snowflake:**
  - DDL execution: 1-5 seconds per view
  - Large models (>100 views): 2-5 minutes
  
- **Fabric:**
  - Model upload: 30-90 seconds
  - Processing time: 1-5 minutes (Fabric-side)

---

## 6. Relationship Detection Limitations

### Automatic Detection
- **Naming conventions only:** Relies on `_id`, `_key` suffixes
- **No semantic analysis:** Cannot infer relationships from data
- **Single-column only:** Composite keys not supported

### Explicit Relationships
- **Snowflake:** Foreign key constraints are used
- **Fabric:** Relationships from TMSL are preserved

### Cardinality
- **Detection:** Based on primary key presence
- **Default:** Many-to-one if uncertain
- **No validation:** Cardinality not verified against data

---

## 7. Hierarchy Detection Limitations

### Supported Patterns
- **Date hierarchies:** Year → Quarter → Month → Day
- **Geographic:** Country → State → City
- **Category:** Category → Subcategory

### Limitations
- **Pattern matching only:** Based on column names
- **No custom hierarchies:** User-defined hierarchies not auto-detected
- **No ragged hierarchies:** Assumes balanced hierarchies

---

## 8. UI/Qt GUI Limitations

### Platform Support
- **Tested on:** macOS, Windows 10/11, Ubuntu 20.04+
- **Qt version:** 6.6.0+
- **Not tested:** Older Linux distributions, BSD systems

### Features
- **Diff viewer:** Text-based only (no visual schema diff)
- **YAML editor:** Basic syntax highlighting only
- **No real-time sync:** Manual refresh required

---

## 9. Configuration Limitations

### Environment Variables
- **Required:** All credentials must be in environment variables
- **No inline secrets:** Cannot specify passwords in YAML files
- **No credential rotation:** Requires restart to pick up new credentials

### Project Configuration
- **Single source/target:** Cannot sync multiple sources simultaneously
- **No conditional logic:** Behavior policies are static
- **YAML only:** No JSON or TOML support

---

## 10. Known Issues

### Issue 1: Large Model Memory Usage
- **Symptom:** High memory usage with models >500 tables
- **Workaround:** Use table inclusion/exclusion filters
- **Status:** Optimization planned

### Issue 2: Fabric Timeout on Large Models
- **Symptom:** Extraction timeout for models >800 tables
- **Workaround:** Extract in batches using filters
- **Status:** Investigating pagination

### Issue 3: DAX Time Intelligence
- **Symptom:** Time intelligence functions not translated
- **Workaround:** Use metric_overrides with SQL equivalents
- **Status:** By design (Tier 3)

### Issue 4: Concurrent Execution
- **Symptom:** Parallel execution flag is experimental
- **Workaround:** Keep `enable_parallel_execution: false`
- **Status:** Under development

---

## 11. Unsupported Features

### Not Implemented
- ❌ Databricks connector (planned)
- ❌ Incremental sync (full sync only)
- ❌ Bi-directional sync in single command
- ❌ Automatic conflict resolution
- ❌ Multi-tenant deployments
- ❌ Role-based access control (RBAC) in SemaBridge
- ❌ Audit logging (basic logging only)

### Will Not Implement
- ❌ Direct Source → Target conversion (must go through SML)
- ❌ Inline credential storage
- ❌ Automatic schema migration
- ❌ Real-time streaming sync

---

## 12. Workarounds and Best Practices

### For Large Models
1. Use table inclusion/exclusion filters
2. Enable caching: `model.cache_enabled: true`
3. Run during off-peak hours
4. Consider splitting into multiple projects

### For Complex DAX
1. Use metric_overrides in behavior policy
2. Document SQL equivalents
3. Test SQL translations in Snowflake first
4. Consider simplifying DAX where possible

### For Performance
1. Use incremental extraction (cache)
2. Limit relationship detection scope
3. Disable unnecessary features
4. Use dry-run mode for testing

### For Reliability
1. Always validate before deploying
2. Use version tags for important snapshots
3. Test rollback procedures
4. Monitor DuckDB file size

---

## 13. Getting Help

### Reporting Issues
- Check this document first
- Review logs in `src/.semantic_metadata/logs/`
- Include error messages and configuration (redact secrets)

### Feature Requests
- Check roadmap in `docs/ROADMAP.md`
- Describe use case and business value
- Provide examples if possible

### Community Support
- Review `docs/CONTRIBUTING.md` for guidelines
- Check existing issues before creating new ones

---

## Version Compatibility

| SemaBridge Version | Snowflake | Fabric API | Python |
|-------------------|-----------|------------|--------|
| 1.0.0 | ≥7.0 | 2023+ | 3.10+ |

---

**Note:** This document is updated regularly. Check the "Last Updated" date at the top.
