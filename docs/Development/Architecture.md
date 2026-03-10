# SemaBridge Architecture

**Last Updated: February 2026**

SemaBridge is a **universal semantic layer bridge** that enables **bi-directional synchronization of semantic models across multiple data platforms**. Currently supporting **Snowflake** and **Microsoft Fabric (Power BI)**, with an extensible architecture designed for easy integration of additional platforms like **Databricks** and more.

---

## High-Level Architecture

```
┌─────────────────┐                    ┌─────────────────┐
│   Platform A    │◄───Bidirectional──►│   Platform B    │
│  (Snowflake,    │         Sync       │  (Fabric,       │
│   Databricks,   │                    │   etc.)         │
│   etc.)         │                    │                 │
└────────┬────────┘                    └────────┬────────┘
         │                                      │
         │  Extract                    Extract  │
         ▼                                      ▼
┌──────────────────────────────────────────────────────────────┐
│              SemaBridge Universal Bridge System              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              10-Step Execution Engine                │    │
│  │  1. Load Config  2. Init IDs  3. Auth  4. Extract    │    │
│  │  5. Validate  6. Convert  7. Persist  8. Transform   │    │
│  │  9. Deploy  10. Finalize                             │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │ Connectors  │  │  Converter  │  │ Repository  │           │
│  │ (Extensible │  │ (TMSL↔SML   │  │ (DuckDB +   │           │ 
│  │  Plugin     │  │  ↔OSI)      │  │  Versioning)│           │
│  │  System)    │  │             │  │             │           │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘           │
│         │                │                │                  │
│         └────────►┌──────┴──────┐◄────────┘                  │
│                   │  SML/OSI    │                            │
│                   │ Intermediate│                            │
│                   │   (YAML)    │                            │
│                   │ Platform-   │                            │
│                   │  Agnostic   │                            │
│                   └──────┬──────┘                            │
│                          │                                   │
│         ┌────────────────┼────────────────┐                  │
│         ▼                ▼                ▼                  │
│   ┌───────────┐   ┌───────────┐   ┌───────────┐              │
│   │ Behavior  │   │    UI     │   │ Validation│              │
│   │ Policies  │   │  (Qt GUI) │   │  Engine   │              │
│   └───────────┘   └───────────┘   └───────────┘              │
└──────────────────────────────────────────────────────────────┘
```

### Extensible Connector System

**Currently Supported:**
- ✅ Snowflake (Source & Target)
- ✅ Microsoft Fabric / Power BI (Source & Target)

**Planned Connectors:**
- 🔄 Databricks Unity Catalog (Q2 2026)
- 🔌 Additional platforms via plugin system

---

## Core Layers

### 1. CLI Layer (`cli/`)

The command-line interface provides multiple command groups:

| Command Group | Description |
|--------------|-------------|
| **Core Commands** | `validate`, `semantic-sync`, `init`, `version`, `config` |
| **Semantic Commands** | `snapshot`, `compare`, `history`, `rollback` |
| **Diff Commands** | `diff source`, `diff versions`, `diff stats` |
| **Logs Commands** | `logs list`, `logs show`, `logs stats` |
| **UV Commands** | `uv sync`, `uv validate`, `uv build`, `uv discover`, `uv rollback`, `uv status` |

**Key Files:**
- `main.py` - Main CLI entry point with Typer
- `semantic_commands.py` - Version control commands
- `diff_commands.py` - Comparison utilities
- `logs_commands.py` - Log management
- `uv_commands_cli.py` - UV build system integration

### 2. Core Layer (`core/`)

Central orchestration and configuration management.

| Module | Purpose |
|--------|---------|
| `execution_engine.py` | **10-step execution pipeline** (see below) |
| `engine.py` | Legacy engine (being phased out) |
| `settings.py` | Pydantic settings from environment variables |
| `behavior.py` | Behavior policy system |
| `config_loader.py` | Configuration file discovery |
| `project.py` | Project configuration management |
| `run_summary.py` | Execution result tracking |
| `source_format.py` | Source format validation |
| `uv_registry.py` | UV command registry |
| `uv_commands.py` | UV command implementations |

**10-Step Execution Pipeline:**

The `ExecutionEngine` enforces a mandatory 10-step flow for all sync operations:

1. **Load and Validate Configuration** - Parse YAML/env, validate connector types
2. **Initialize Identifiers** - Generate unique `project_id` and `run_id`
3. **Resolve Authentication** - Validate credentials from environment variables
4. **Extract from Source** - Connect and extract semantic model
5. **Validate and Parse Source Format** - Apply format validation rules
6. **Convert to Canonical SML** - Transform to intermediate representation
7. **Persist Artifacts** - Save to DuckDB with versioning
8. **Convert to Target Format** (Optional) - Generate target-specific artifacts
9. **Deploy to Target** (Optional) - Execute deployment
10. **Finalize Run** - Create run summary and cleanup

Each step is tracked, logged, and can fail independently with proper error handling.

### 3. Connectors Layer (`connectors/`)

Platform-specific integrations for extraction and emission with an extensible plugin architecture.

#### Currently Supported Platforms

**Snowflake Connector:**
| Module | Purpose |
|--------|---------|
| `snowflake_extractor.py` | Batch extraction from `INFORMATION_SCHEMA` |
| `snowflake_emitter.py` | Generate DDL for Semantic Views + Cortex YAML |

**Microsoft Fabric Connector:**
| Module | Purpose |
|--------|---------|
| `fabric_extractor.py` | Azure AD OAuth + Fabric REST API extraction |
| `fabric_publisher.py` | Upload model.bim to Fabric REST API |
| `tmsl_generator.py` | Generate Fabric-compatible model.bim |

#### Shared Detection & Inference Modules

| Module | Purpose |
|--------|---------|
| `relationship_detector.py` | Infer FK relationships from naming conventions |
| `hierarchy_detector.py` | Detect date/geo/category hierarchies |
| `measure_detector.py` | Identify fact tables and numeric measures |
| `inference_engine.py` | ML-based table classification (FACT vs DIM) |

#### Extensible Architecture

The connector system is designed for easy addition of new platforms:

**Planned Connectors:**
- **Databricks Unity Catalog** (Q2 2026)

**Adding New Connectors:**
1. Implement extractor interface in `connectors/`
2. Implement emitter/publisher interface
3. Add format converter in `converter/`
4. Register connector in plugin system

The standardized SML/OSI intermediate format ensures new connectors integrate seamlessly without modifying core logic.

### 4. Converter Layer (`converter/`)

Transformation logic between formats with full OSI (Open Semantic Interchange) support.

| Module | Purpose |
|--------|---------|
| `tmsl_to_osi.py` | ✅ Fabric TMSL JSON → OSI (canonical format) |
| `osi_to_sml.py` | ✅ OSI → SML with semantic enrichment |
| `sml_to_osi.py` | ✅ SML → OSI bidirectional conversion |
| `tmsl_to_sml.py` | Legacy direct TMSL → SML (being phased out) |
| `dax_translator.py` | DAX → SQL translation (3-tier system) |
| `measure_triage.py` | Classify measures by complexity |
| `hierarchy_flattener.py` | Flatten hierarchies for SQL |
| `materialization_builder.py` | Build materialized views |

**Conversion Pipeline:**
```
Platform A (e.g., Fabric TMSL) → OSI → SML → Platform B (e.g., Snowflake)
                                  ↑
                             Canonical
                            Intermediate
                              Format
                         (Platform-Agnostic)
```

**OSI Benefits:**
- Platform-agnostic intermediate format
- Clean separation of concerns (extract → transform → load)
- Enables easy addition of new platforms (Databricks, etc.)
- Bidirectional conversion with data preservation
- Standardized interface for all connectors

**DAX Translation Tiers:**
- **Tier 1**: Direct aggregations (SUM, AVG, COUNT) → Full SQL
- **Tier 2**: CALCULATE with simple filters → Partial support
- **Tier 3**: Complex/Time-Intelligence → Metadata only (use metric_overrides)

### 5. Intermediate Layer (`intermediate/` and `sml/`)

Canonical representation using SML (Semantic Modeling Language) and OSI (Open Semantic Interchange).

**OSI Models (`intermediate/models.py`):** ✅ Fully Implemented
| Class | Purpose |
|-------|---------|
| `OSIModel` | Root OSI semantic model container |
| `OSIDataset` | Platform-agnostic table/dataset definition |
| `OSIColumn` | Column with OSI data types |
| `OSIMetric` | Measure with expression and aggregation |
| `OSIRelationship` | Relationship with cardinality |
| `OSIDimension` | Dimension with attributes and hierarchies |
| `OSIAttribute` | Dimension attribute |
| `OSIHierarchy` | Hierarchical structure |
| `OSILevel` | Hierarchy level |

**SML Models (`sml/models.py`):**
| Class | Purpose |
|-------|---------|
| `SMLModel` | Root container with metadata |
| `SMLDataset` | Table/query definition |
| `SMLColumn` | Column with normalized data types |
| `SMLMetric` | Measure with DAX/SQL expression |
| `SMLRelationship` | FK relationship with cardinality |
| `SMLDimension` | Logical dimension with hierarchies |

**Key Features:**
- ✅ Full OSI ↔ SML bidirectional conversion
- ✅ Round-trip conversion preserves all metadata
- ✅ Type mapping between OSI and SML enums
- ✅ Used in production Fabric → Snowflake pipeline

**SML Utilities:**
- `assembler.py` - Build SML from metadata
- `serializer.py` - YAML read/write operations

### 6. Repository Layer (`repository/`)

Git-like version control using **DuckDB**.

| Module | Purpose |
|--------|---------|
| `duckdb_manager.py` | Core DuckDB operations and schema management |
| `semantic_version_manager.py` | Version registry with parent-child lineage |
| `semantic_snapshot_manager.py` | Immutable snapshot storage (JSON files) |
| `semantic_diff_engine.py` | Entity-level change comparison |
| `rollback_orchestrator.py` | Cross-adapter rollback coordination |
| `command_logger.py` | Command execution logging |

**DuckDB Schema:**
```sql
semantic_projects (
    project_id TEXT PRIMARY KEY,
    name TEXT,
    workspace_id TEXT,
    head_snapshot_id TEXT,
    adapter TEXT,
    created_at TIMESTAMP
)

semantic_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    project_id TEXT,
    timestamp TIMESTAMP,
    version_tag TEXT,
    sml_blob JSON,
    status TEXT,
    duration_ms INTEGER,
    run_id TEXT
)

semantic_changes (
    change_id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    object_type TEXT,
    diff_type TEXT,
    old_value JSON,
    new_value JSON
)

source_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT,
    source_type TEXT,
    artifact_data JSON,
    created_at TIMESTAMP
)
```

### 7. Formats Layer (`formats/`)

Schema definitions and validation rules for different platforms.

| Module | Purpose |
|--------|---------|
| `schema.py` | Format schema definitions |
| `sanitizer.py` | Identifier sanitization (reserved words, special chars) |
| `yaml_validator.py` | YAML configuration validation |

**Sanitization Rules:**
- Reserved word prefixing (`TABLE` → `L_TABLE`)
- Special character handling (spaces, mixed case)
- Length constraints (max 255 chars)
- Case normalization (configurable)

### 8. UI Layer (`ui/`)

Qt6-based graphical user interface.

| Module | Purpose |
|--------|---------|
| `main_window.py` | Main application window |
| `diff_viewer.py` | Visual diff comparison |
| `version_history.py` | Version timeline browser |
| `yaml_editor.py` | Syntax-highlighted YAML editor |
| `source_browser.py` | Source system explorer |
| `workers.py` | Background task execution |

**Features:**
- Real-time diff visualization
- Version history timeline
- YAML configuration editor
- Source system browsing
- Background task management

### 9. Validation Layer (`validation/`)

Configuration and model validation.

| Module | Purpose |
|--------|---------|
| `config_validator.py` | Validate configuration files |
| `validator.py` | Model validation rules |
| `inspector.py` | Schema inspection utilities |

### 10. Utils Layer (`utils/`)

Shared utilities and helpers.

| Module | Purpose |
|--------|---------|
| `logger.py` | Standard logging configuration |
| `enterprise_logger.py` | Enterprise-grade logging with sessions |
| `logging_init.py` | Logging initialization |
| `cache.py` | Metadata caching for performance |

### 11. Plugins Layer (`plugins/`)

Extensible plugin architecture (future use).

| Module | Purpose |
|--------|---------|
| `base.py` | Plugin base classes |
| `registry.py` | Plugin discovery and registration |

---

## Data Flows

### Forward Sync (Snowflake → Fabric)

```
Snowflake DB
       │
       ▼ (INFORMATION_SCHEMA queries)
SnowflakeExtractor
       │
       ▼ (Auto-detection)
Detectors (Relationship, Hierarchy, Measure, Inference)
       │
       ▼ (Assembler)
SMLModel (YAML)
       │
       ├──► DuckDBManager.commit_model() → Version Control
       │
       ▼ (Generator)
TMSLGenerator → model.bim
       │
       ▼ (Publisher)
FabricPublisher → Fabric REST API
       │
       ▼
Fabric Semantic Model Deployed
```

### Reverse Sync (Fabric → Snowflake)

```
Fabric Semantic Model
       │
       ▼ (REST API: getDefinition)
FabricExtractor → TMSL JSON
       │
       ▼ (Transformer)
TMSLTransformer + DAXTranslator (3-tier)
       │
       ▼
SMLModel
       │
       ├──► DuckDBManager.commit_model() → Version Control
       │
       ▼ (Emitter with Behavior Policy)
SnowflakeEmitter → DDL + Cortex YAML
       │
       ├──► semantic_view.sql (CREATE VIEW statements)
       └──► cortex_analyst.yaml (Cortex Analyst config)
       │
       ▼ (Execute DDL)
Snowflake Semantic Views Created
```

### Rollback Flow

```
CLI: semabridge rollback --project-id <id> --tag v1.0
       │
       ▼
RollbackOrchestrator
       │
       ├── 1. Validate feasibility (check snapshot exists)
       ├── 2. Create pre-rollback snapshot (backup current state)
       ├── 3. Retrieve target snapshot from DuckDB
       ├── 4. Reconstruct SML model from snapshot
       ├── 5. Execute adapter rollback (re-deploy)
       ├── 6. Create post-rollback snapshot
       └── 7. Link rollback lineage (parent-child relationship)
       │
       ▼
Target System Updated + Full History Preserved
```

### Behavior Policy Flow

```
User Configuration (policy.yaml)
       │
       ▼
ConnectorBehavior.from_yaml()
       │
       ├──► SnowflakeBehavior (quoting, reserved words, etc.)
       ├──► FabricBehavior (overwrite, TMSL mode)
       ├──► SemanticModelBehavior (metric_overrides, suffixes)
       ├──► CompatibilityBehavior (case, reserved words)
       ├──► FeatureFlags (cortex, parallel execution)
       └──► LegacyCleanup (deprecated views)
       │
       ▼
Applied During Execution (Step 2: Init Identifiers)
       │
       ▼
Influences: Emitter, Generator, Sanitizer, Validator
```

---

## Configuration

Settings loaded via Pydantic from environment variables and YAML files:

### Environment Variables

```bash
# Snowflake Configuration
SNOWFLAKE_ACCOUNT=myaccount.region
SNOWFLAKE_USER=myuser
SNOWFLAKE_PASSWORD=mypassword
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=MYDB
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=SYSADMIN  # Optional

# Fabric Configuration
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-client-id
FABRIC_CLIENT_SECRET=your-client-secret
FABRIC_WORKSPACE_ID=your-workspace-id

# Model Configuration
MODEL_NAME="My Semantic Model"
MODEL_DESCRIPTION="Sales and inventory data"
MODEL_CACHE_ENABLED=true
MODEL_CACHE_DIR=.cache

# LLM Configuration (Optional)
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4
```

### Project Configuration (`semabridge.yaml`)

```yaml
# Source connector configuration
source:
  type: fabric  # or snowflake
  dataset_id: "1cb616cc-52b7-4268-b458-b092d64c84a6"
  workspace_id: "1a5e9594-c112-43d0-8cdd-012f7746c1b1"
  model: "Store Sales"

# Target connector configuration
target:
  type: snowflake  # or fabric
  deploy: true

# Semantic model name
model_name: "Store Sales"

# Optional: Snapshot version to sync
version_tag: "v1.0"

# Optional: Logging configuration
logging:
  level: INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: text  # "text" or "json"

# Optional: Behavior policy path
policy_path: "policies/standard.yaml"
```

### Behavior Policy (`policy.yaml`)

```yaml
snowflake:
  query_tag: "Semabridge_Prod"
  quote_identifiers: true
  create_missing_tables: true
  validate_column_schema: true
  use_transient_tables: false

fabric:
  deploy_overwrite: true
  tmsl_generation_mode: "standard"  # or "compatibility"

semantic_model:
  view_suffix: "_semantic"
  enable_date_dimension: true
  metric_overrides:
    "Revenue SPLY": "LAG(SUM(FACT.\"REVENUE\"), 12) OVER (...)"
  sync_all_attributes: true

compatibility:
  suppress_reserved_words: true
  force_uppercase: true

features:
  enable_cortex_analyst: true
  enable_parallel_execution: false
  skip_validation_on_dry_run: true

legacy:
  drop_deprecated_views: false
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **SML as Intermediate** | Decouples source/target formats for extensibility; supports OSI interop |
| **10-Step Execution Pipeline** | Enforces consistent execution flow with proper error handling and tracking |
| **DuckDB for State** | Embedded, zero-config, native JSON support, SQL-queryable history |
| **Non-destructive Rollback** | Rollback creates new snapshot; full history preserved for audit |
| **Tiered DAX Translation** | Graceful degradation for complex expressions; manual overrides available |
| **Behavior Policy System** | Separates "what to run" from "how to run it"; no code changes needed |
| **File-based Snapshots** | Simple auditing, no additional DB required, JSON format |
| **UV Command Registry** | Optimized command execution with enterprise logging |
| **Qt GUI** | Cross-platform desktop interface for visual operations |
| **Identifier Sanitization** | Automatic handling of reserved words and special characters |

---

## Behavior Policy System

The behavior policy system allows runtime configuration of connector behavior without code changes.

### Policy Categories

1. **Snowflake Behavior**
   - Query tagging for monitoring
   - Identifier quoting rules
   - Auto-create missing tables
   - Schema validation
   - Transient table usage

2. **Fabric Behavior**
   - Deployment overwrite settings
   - TMSL generation mode (standard vs compatibility)

3. **Semantic Model Behavior**
   - View naming suffixes
   - Date dimension auto-generation
   - **Metric overrides** - Manual SQL for complex DAX measures
   - Sync all attributes flag

4. **Compatibility**
   - Reserved word handling (prefix with `L_`)
   - Case forcing (uppercase/preserve)

5. **Feature Flags**
   - Cortex Analyst generation
   - Parallel execution (experimental)
   - Validation skipping on dry-run

6. **Legacy Cleanup**
   - Drop deprecated views

### Policy Files

Three example policies provided:

- `policy_standard.yaml` - Production deployments
- `policy_compat.yaml` - Fabric→Snowflake migrations with complex models
- `policy_cleanup.yaml` - Removing old artifacts

### Metric Overrides

For complex DAX measures that cannot be auto-translated (Tier 3), use metric overrides:

```yaml
semantic_model:
  metric_overrides:
    "Revenue SPLY": "LAG(SUM(FACT.\"REVENUE\"), 12) OVER (PARTITION BY PRODUCT.\"PRODUCT_KEY\" ORDER BY CALENDAR.\"YEARPERIOD\")"
    "YTD Revenue": "SUM(CASE WHEN CALENDAR.\"DATE\" <= CURRENT_DATE THEN FACT.\"REVENUE\" ELSE 0 END)"
```

This provides full control over SQL generation for specific measures.

---

## File Structure

```
semabridge/
├── main.py                      # CLI entry point
├── pyproject.toml               # Package configuration
├── requirements.txt             # Dependencies
├── behavior.yaml                # Default behavior policy
├── semabridge.yaml              # Project configuration
├── src/
│   └── semabridge/
│       ├── cli/                 # CLI commands
│       │   ├── main.py          # Main CLI with Typer
│       │   ├── semantic_commands.py  # Version control commands
│       │   ├── diff_commands.py      # Comparison utilities
│       │   ├── logs_commands.py      # Log management
│       │   └── uv_commands_cli.py    # UV build system
│       ├── core/                # Central orchestration
│       │   ├── execution_engine.py   # 10-step pipeline
│       │   ├── engine.py             # Legacy engine
│       │   ├── settings.py           # Pydantic settings
│       │   ├── behavior.py           # Behavior policies
│       │   ├── config_loader.py      # Config discovery
│       │   ├── project.py            # Project management
│       │   ├── run_summary.py        # Execution tracking
│       │   ├── source_format.py      # Format validation
│       │   ├── uv_registry.py        # Command registry
│       │   └── uv_commands.py        # UV commands
│       ├── connectors/          # Platform integrations
│       │   ├── snowflake_extractor.py
│       │   ├── snowflake_emitter.py
│       │   ├── fabric_extractor.py
│       │   ├── fabric_publisher.py
│       │   ├── tmsl_generator.py
│       │   ├── relationship_detector.py
│       │   ├── hierarchy_detector.py
│       │   ├── measure_detector.py
│       │   └── inference_engine.py
│       ├── converter/           # Transformation logic
│       │   ├── tmsl_to_sml.py
│       │   ├── tmsl_to_osi.py
│       │   ├── sml_to_osi.py
│       │   ├── osi_to_sml.py
│       │   ├── dax_translator.py
│       │   ├── measure_triage.py
│       │   ├── hierarchy_flattener.py
│       │   └── materialization_builder.py
│       ├── intermediate/        # OSI models
│       │   └── models.py
│       ├── sml/                 # SML models
│       │   ├── models.py
│       │   ├── assembler.py
│       │   └── serializer.py
│       ├── repository/          # Version control
│       │   ├── duckdb_manager.py
│       │   ├── semantic_version_manager.py
│       │   ├── semantic_snapshot_manager.py
│       │   ├── semantic_diff_engine.py
│       │   ├── rollback_orchestrator.py
│       │   └── command_logger.py
│       ├── formats/             # Schema definitions
│       │   ├── schema.py
│       │   ├── sanitizer.py
│       │   └── yaml_validator.py
│       ├── ui/                  # Qt GUI
│       │   ├── main_window.py
│       │   ├── diff_viewer.py
│       │   ├── version_history.py
│       │   ├── yaml_editor.py
│       │   ├── source_browser.py
│       │   └── workers.py
│       ├── validation/          # Validation engine
│       │   ├── config_validator.py
│       │   ├── validator.py
│       │   └── inspector.py
│       ├── plugins/             # Plugin system
│       │   ├── base.py
│       │   └── registry.py
│       └── utils/               # Shared utilities
│           ├── logger.py
│           ├── enterprise_logger.py
│           ├── logging_init.py
│           └── cache.py
├── tests/                       # Test suite
│   ├── test_*.py
│   └── conftest.py
├── docs/                        # Documentation
│   ├── ARCHITECTURE.md
│   ├── PRODUCT.md
│   ├── LIMITATIONS.md
│   ├── CLI_REFERENCE.md
│   ├── CONTRIBUTING.md
│   └── agent.md
├── examples/                    # Example configurations
│   ├── policy_standard.yaml
│   ├── policy_compat.yaml
│   └── policy_cleanup.yaml
└── scripts/                     # Utility scripts
    ├── build_exe.py
    └── ...
```


---

## Qt GUI Architecture

SemaBridge includes a cross-platform Qt6-based graphical user interface.

### Main Window (`main_window.py`)

Central application window with:
- Menu bar (File, Edit, View, Tools, Help)
- Toolbar with quick actions
- Status bar with connection indicators
- Tab-based workspace

### Components

#### 1. Diff Viewer (`diff_viewer.py`)
- Side-by-side comparison of versions
- Syntax highlighting for YAML/JSON
- Color-coded changes (additions, deletions, modifications)
- Export diff to file

#### 2. Version History (`version_history.py`)
- Timeline view of all snapshots
- Filter by date range, tag, or status
- Visual branching for rollbacks
- Quick actions (compare, rollback, export)

#### 3. YAML Editor (`yaml_editor.py`)
- Syntax highlighting
- Auto-completion for known keys
- Real-time validation
- Schema-aware editing

#### 4. Source Browser (`source_browser.py`)
- Tree view of source systems
- Snowflake: Database → Schema → Tables → Columns
- Fabric: Workspace → Datasets → Tables → Measures
- Preview data and metadata
- Quick sync actions

#### 5. Workers (`workers.py`)
- Background task execution
- Progress reporting
- Cancellation support
- Error handling

### Launch GUI

```bash
# From CLI
semabridge --ui

# Or directly
python -m semabridge.ui.main_window
```

---

## Performance Considerations

### Extraction Performance

| System | Tables | Time | Notes |
|--------|--------|------|-------|
| Snowflake | <100 | 30-60s | Fast with caching |
| Snowflake | 100-500 | 2-5min | INFORMATION_SCHEMA queries |
| Snowflake | >500 | 5-10min | Consider filters |
| Fabric | <100 | 30-60s | API throttling |
| Fabric | >100 | 1-3min | Row count queries add time |

### Optimization Strategies

1. **Enable Caching**
   ```yaml
   model:
     cache_enabled: true
     cache_dir: .cache
   ```

2. **Use Filters**
   ```yaml
   model:
     include_tables:
       - "SALES_*"
       - "CUSTOMER_*"
     exclude_tables:
       - "*_TEMP"
       - "*_STAGING"
   ```

3. **Incremental Extraction**
   - Cache stores metadata between runs
   - Only changed tables re-extracted
   - Significant speedup for large schemas

4. **Parallel Execution** (Experimental)
   ```yaml
   features:
     enable_parallel_execution: true
   ```

### Memory Usage

| Model Size | Memory | Notes |
|------------|--------|-------|
| <100 tables | <500MB | Normal |
| 100-500 tables | 500MB-2GB | Acceptable |
| >500 tables | >2GB | Consider filters |

---

## Security Considerations

### Credential Management

1. **Environment Variables Only**
   - Never store credentials in YAML files
   - Use `.env` file (add to `.gitignore`)
   - Rotate credentials regularly

2. **Service Principal Authentication**
   - Fabric requires service principal
   - Grant minimum required permissions
   - Use separate principals for dev/prod

3. **Snowflake Role-Based Access**
   - Use dedicated role for SemaBridge
   - Grant only necessary privileges:
     - `USAGE` on database/schema/warehouse
     - `SELECT` on tables
     - `CREATE VIEW` for deployment

### Data Protection

1. **No Data Extraction**
   - Only metadata extracted
   - Row counts only (no actual data)
   - Safe for sensitive environments

2. **Audit Logging**
   - All operations logged
   - Logs stored in `src/.semantic_metadata/logs/`
   - Includes timestamps, user, actions

3. **Version Control**
   - Full audit trail in DuckDB
   - Non-destructive operations
   - Rollback capability

---

## Troubleshooting

### Common Issues

#### 1. Connection Failures

**Symptom:** `AuthenticationError` or `ConnectionError`

**Solutions:**
- Verify environment variables: `semabridge config`
- Test connections: `semabridge validate`
- Check network/firewall settings
- Verify service principal permissions (Fabric)
- Check role privileges (Snowflake)

#### 2. Large Model Timeouts

**Symptom:** Extraction timeout for models >500 tables

**Solutions:**
- Enable caching: `MODEL_CACHE_ENABLED=true`
- Use table filters in `semabridge.yaml`
- Increase timeout (if supported)
- Extract in batches

#### 3. DAX Translation Failures

**Symptom:** Measures marked as "complex" or not translated

**Solutions:**
- Check measure tier (Tier 1/2/3)
- Use metric_overrides for Tier 3 measures
- Simplify DAX if possible
- Review `docs/LIMITATIONS.md`

#### 4. Reserved Word Conflicts

**Symptom:** SQL errors with identifiers like `TABLE`, `DATE`

**Solutions:**
- Enable reserved word suppression:
  ```yaml
  compatibility:
    suppress_reserved_words: true
  ```
- Or manually quote identifiers:
  ```yaml
  snowflake:
    quote_identifiers: true
  ```

#### 5. DuckDB Lock Errors

**Symptom:** `database is locked` error

**Solutions:**
- Close other SemaBridge instances
- Check for orphaned processes
- Restart if necessary
- Ensure single-user access

### Debug Mode

Enable verbose logging:

```bash
# CLI
semabridge -vv semantic-sync snowflake

# Or set log level
semabridge --log-level DEBUG semantic-sync snowflake
```

Check logs:
```bash
semabridge logs show operations --follow
```

---

## Testing

### Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src/semabridge --cov-report=term-missing

# Specific module
pytest tests/test_dax_translator.py -v

# Integration tests (requires credentials)
pytest tests/test_snowflake_extractor.py -v
```

### Test Structure

```
tests/
├── conftest.py              # Fixtures
├── test_dax_translator.py   # DAX translation
├── test_duckdb_manager.py   # Version control
├── test_executor.py         # Execution engine
├── test_sml_models.py       # SML validation
├── test_tmsl_transformer.py # TMSL conversion
└── ...
```

### Mocking

External services (Snowflake, Fabric) are mocked in unit tests:

```python
@pytest.fixture
def mock_snowflake_connection(mocker):
    mock_conn = mocker.Mock()
    mock_cursor = mocker.Mock()
    mock_conn.cursor.return_value = mock_cursor
    return mock_conn
```

---

## Deployment

### Installation Methods

#### 1. Development Install
```bash
pip install -e .
```

#### 2. Production Install
```bash
pip install semabridge
```

#### 3. Standalone Executable
```bash
# Build
python scripts/build_exe.py

# Run
./dist/SemaBridge --help
```

### System Requirements

- **Python:** 3.10+
- **OS:** macOS, Windows 10/11, Ubuntu 20.04+
- **Memory:** 2GB minimum, 4GB recommended
- **Disk:** 1GB for application, additional for DuckDB
- **Network:** HTTPS access to Snowflake and Fabric APIs

### Dependencies

See `pyproject.toml` for complete list. Key dependencies:

- `snowflake-connector-python` - Snowflake integration
- `pydantic` - Configuration validation
- `typer` - CLI framework
- `rich` - Terminal formatting
- `duckdb` - Version control
- `PyQt6` - GUI framework
- `httpx` - HTTP client
- `msal` - Azure AD authentication

---

## Future Enhancements

### Planned Features

1. **Additional Connectors**
   - Databricks (in progress)
   - Redshift (planned)

2. **Incremental Sync**
   - Delta-based synchronization
   - Change data capture
   - Reduced sync times

3. **Advanced Diff**
   - Visual schema diff
   - Impact analysis
   - Dependency tracking

4. **Enhanced UI**
   - Real-time sync monitoring
   - Interactive query builder
   - Metric explorer

5. **Enterprise Features**
   - Multi-tenant support
   - RBAC within SemaBridge
   - Advanced audit logging
   - Compliance reporting

### Experimental Features

Currently experimental (use with caution):

- Parallel execution (`enable_parallel_execution`)
- Advanced caching strategies
- Custom plugin system

---

## References

- [Product Overview](PRODUCT.md)
- [Limitations](LIMITATIONS.md)
- [CLI Reference](CLI_REFERENCE.md)
- [Contributing Guide](CONTRIBUTING.md)
- [Agent Instructions](agent.md)

---

**Last Updated:** February 2026  
**Version:** 1.0.0
