# SemaBridge - CLI Command Reference

**Last Updated: February 2026**

Complete reference for all SemaBridge CLI commands. SemaBridge is a universal semantic layer bridge supporting multiple data platforms.

---

## Supported Platforms

- ✅ **Snowflake** - Source & Target
- ✅ **Microsoft Fabric (Power BI)** - Source & Target
- 📋 **Databricks** - Planned Q2 2026

---

## Table of Contents

1. [Core Commands](#core-commands)
2. [Semantic Commands](#semantic-commands)
3. [Diff Commands](#diff-commands)
4. [Logs Commands](#logs-commands)
5. [UV Build System Commands](#uv-build-system-commands)
6. [Global Options](#global-options)

---

## Core Commands

### `validate`

Test connections to configured platforms (Snowflake, Fabric, etc.).

```bash
semabridge validate
```

**What it does:**
- Validates environment variables
- Tests platform connections (Snowflake, Fabric)
- Checks DuckDB accessibility

**Exit codes:**
- `0`: All validations passed
- `1`: Validation failed

**Example output:**
```
✓ Snowflake connection successful
✓ Fabric authentication successful
✓ DuckDB accessible
All validations passed!
```

---

### `semantic-sync`

Synchronize semantic models between platforms.

```bash
# Sync from Snowflake to Fabric
semabridge semantic-sync snowflake

# Sync from Fabric to Snowflake
semabridge semantic-sync fabric --dataset-id <ID>

# Dry run (no deployment)
semabridge semantic-sync snowflake --dry-run

# With custom name
semabridge semantic-sync snowflake --name "My Sales Model"

# With version tag
semabridge semantic-sync snowflake --tag v1.0
```

**Arguments:**
- `source`: Source type (`snowflake` or `fabric`)

**Options:**
- `--dataset-id TEXT`: Fabric dataset ID (required for Fabric source)
- `--workspace-id TEXT`: Override Fabric workspace ID
- `--name TEXT`: Override model name
- `--tag TEXT`: Version tag for snapshot
- `--dry-run`: Generate artifacts without deploying
- `--no-deploy`: Skip deployment step
- `--output-dir PATH`: Custom output directory

**Configuration:**
Uses `semabridge.yaml` in current directory or falls back to `.env` settings.

**Example `semabridge.yaml`:**
```yaml
source:
  type: fabric
  dataset_id: "1cb616cc-52b7-4268-b458-b092d64c84a6"

target:
  type: snowflake
  deploy: true

model_name: "Store Sales"
version_tag: "v1.0"
policy_path: "policies/standard.yaml"
```

---

### `init`

Initialize SemaBridge configuration.

```bash
semabridge init
```

**What it does:**
- Creates `~/.semabridge/config.yaml`
- Sets up DuckDB repository path
- Creates default behavior policy
- Initializes logging directories

**Interactive prompts:**
- Repository location
- Default intermediate format (SML/OSI)
- Logging preferences

---

### `version`

Display SemaBridge version information.

```bash
semabridge version
```

**Output:**
```
SemaBridge v1.0.0
Python: 3.10.12
DuckDB: 0.9.2
```

---

### `config`

Show current configuration.

```bash
semabridge config
```

**Output:**
- Environment variables (redacted)
- Active configuration file
- Behavior policy settings
- Repository location

---

## Semantic Commands

### `snapshot create`

Create a manual snapshot of current state.

```bash
semabridge snapshot create --tag v1.0 --message "Production release"
```

**Options:**
- `--tag TEXT`: Version tag (required)
- `--message TEXT`: Snapshot description
- `--project-id TEXT`: Project identifier

---

### `snapshot list`

List all snapshots for a project.

```bash
semabridge snapshot list --project-id <ID>

# With limit
semabridge snapshot list --project-id <ID> --limit 10
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--limit INTEGER`: Maximum snapshots to show (default: 20)

**Output:**
```
Snapshots for project: Store Sales
┌──────────┬─────────┬─────────────────────┬──────────────┐
│ ID       │ Tag     │ Timestamp           │ Status       │
├──────────┼─────────┼─────────────────────┼──────────────┤
│ abc123   │ v1.0    │ 2026-02-14 10:30:00 │ success      │
│ def456   │ v0.9    │ 2026-02-13 15:20:00 │ success      │
└──────────┴─────────┴─────────────────────┴──────────────┘
```

---

### `compare`

Compare two snapshots.

```bash
semabridge compare --project-id <ID> --from v0.9 --to v1.0
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--from TEXT`: Source version tag (required)
- `--to TEXT`: Target version tag (required)
- `--format TEXT`: Output format (`table`, `json`, `yaml`)

**Output:**
Shows differences in:
- Datasets (added/removed/modified)
- Columns (added/removed/type changes)
- Metrics (added/removed/formula changes)
- Relationships (added/removed)

---

### `compare-current`

Compare current state against a snapshot.

```bash
semabridge compare-current --project-id <ID> --baseline v1.0
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--baseline TEXT`: Baseline version tag (required)

---

### `history`

View version history for a project.

```bash
semabridge history --project-id <ID>

# With details
semabridge history --project-id <ID> --verbose
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--verbose`: Show detailed change information
- `--limit INTEGER`: Maximum versions to show

---

### `rollback`

Rollback to a previous version.

```bash
semabridge rollback --project-id <ID> --tag v1.0

# With confirmation skip
semabridge rollback --project-id <ID> --tag v1.0 --yes
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--tag TEXT`: Target version tag (required)
- `--yes`: Skip confirmation prompt

**Behavior:**
- Creates new snapshot (non-destructive)
- Re-deploys to target system
- Preserves full history

---

## Diff Commands

### `diff source`

Compare source system against SML model.

```bash
# Compare Snowflake against SML
semabridge diff source snowflake --sml-path output/model.yaml

# Compare Fabric against SML
semabridge diff source fabric --dataset-id <ID> --sml-path output/model.yaml
```

**Arguments:**
- `source_type`: Source type (`snowflake` or `fabric`)

**Options:**
- `--sml-path PATH`: Path to SML model file (required)
- `--dataset-id TEXT`: Fabric dataset ID (for Fabric source)
- `--output PATH`: Save diff to file
- `--format TEXT`: Output format (`table`, `json`, `yaml`)

---

### `diff versions`

Compare two versions in repository.

```bash
semabridge diff versions --project-id <ID> --from v0.9 --to v1.0

# With detailed output
semabridge diff versions --project-id <ID> --from v0.9 --to v1.0 --verbose
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--from TEXT`: Source version (required)
- `--to TEXT`: Target version (required)
- `--verbose`: Show detailed changes
- `--output PATH`: Save diff to file

---

### `diff stats`

Show statistics for version differences.

```bash
semabridge diff stats --project-id <ID>
```

**Options:**
- `--project-id TEXT`: Project identifier (required)
- `--from TEXT`: Start version (optional)
- `--to TEXT`: End version (optional)

**Output:**
```
Diff Statistics
───────────────────────────
Datasets:     +5 / -2 / ~3
Columns:      +15 / -8 / ~12
Metrics:      +10 / -3 / ~5
Relationships: +7 / -1 / ~2
```

---

## Logs Commands

### `logs list`

List available log files.

```bash
semabridge logs list

# Filter by type
semabridge logs list --type operations
semabridge logs list --type errors
semabridge logs list --type audit
```

**Options:**
- `--type TEXT`: Log type filter (`operations`, `errors`, `audit`)
- `--date TEXT`: Filter by date (YYYY-MM-DD)

---

### `logs show`

Display log file contents.

```bash
# Show latest operations log
semabridge logs show operations

# Show specific date
semabridge logs show operations --date 2026-02-14

# Follow log (tail -f)
semabridge logs show operations --follow
```

**Arguments:**
- `log_type`: Log type (`operations`, `errors`, `audit`)

**Options:**
- `--date TEXT`: Specific date (YYYY-MM-DD)
- `--follow`: Follow log in real-time
- `--lines INTEGER`: Number of lines to show (default: 50)

---

### `logs stats`

Show log statistics.

```bash
semabridge logs stats

# For specific date range
semabridge logs stats --from 2026-02-01 --to 2026-02-14
```

**Options:**
- `--from TEXT`: Start date (YYYY-MM-DD)
- `--to TEXT`: End date (YYYY-MM-DD)

**Output:**
```
Log Statistics
──────────────────────────
Total Operations: 150
Errors:          5
Warnings:        23
Sync Operations: 45
Rollbacks:       2
```

---

## UV Build System Commands

These commands use the UV build system for optimized execution.

### `uv sync`

Execute sync using UV command registry.

```bash
semabridge uv sync
```

**Advantages:**
- Faster execution
- Better error handling
- Enterprise logging integration

---

### `uv validate`

Validate using UV system.

```bash
semabridge uv validate
```

---

### `uv build`

Build SML model from source.

```bash
semabridge uv build
```

---

### `uv discover`

Discover available models.

```bash
semabridge uv discover --pattern "Sales*"
```

**Options:**
- `--pattern TEXT`: Filter pattern (glob syntax)

---

### `uv rollback`

Rollback using UV system.

```bash
semabridge uv rollback --project-id <ID> --tag v1.0
```

---

### `uv status`

Check system health.

```bash
semabridge uv status
```

**Output:**
```
System Status
─────────────────────────
DuckDB:      ✓ Healthy
Snowflake:   ✓ Connected
Fabric:      ✓ Authenticated
Cache:       ✓ Available
```

---

## Global Options

These options work with all commands:

### `--help`

Show command help.

```bash
semabridge --help
semabridge semantic-sync --help
```

---

### `--verbose` / `-v`

Enable verbose logging.

```bash
semabridge -v semantic-sync snowflake
```

**Levels:**
- No flag: INFO level
- `-v`: DEBUG level
- `-vv`: TRACE level (very verbose)

---

### `--log-level`

Set specific log level.

```bash
semabridge --log-level DEBUG semantic-sync snowflake
```

**Options:**
- `DEBUG`
- `INFO` (default)
- `WARNING`
- `ERROR`
- `CRITICAL`

---

### `--config`

Specify custom config file.

```bash
semabridge --config /path/to/config.yaml semantic-sync snowflake
```

---

### `--ui`

Launch Qt GUI.

```bash
semabridge --ui
```

**Features:**
- Visual diff viewer
- Version history browser
- YAML editor
- Source browser
- Configuration manager

---

## Environment Variables

SemaBridge reads configuration from environment variables:

### Snowflake
```bash
SNOWFLAKE_ACCOUNT=myaccount.region
SNOWFLAKE_USER=myuser
SNOWFLAKE_PASSWORD=mypassword
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=MYDB
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=SYSADMIN  # Optional
```

### Fabric
```bash
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-client-id
FABRIC_CLIENT_SECRET=your-client-secret
FABRIC_WORKSPACE_ID=your-workspace-id
```

### Model Configuration
```bash
MODEL_NAME="My Semantic Model"
MODEL_DESCRIPTION="Sales and inventory data"
MODEL_CACHE_ENABLED=true
MODEL_CACHE_DIR=.cache
```

### LLM Configuration (Optional)
```bash
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Configuration error |
| 3 | Connection error |
| 4 | Validation error |
| 5 | Deployment error |

---

## Examples

### Complete Workflow

```bash
# 1. Initialize
semabridge init

# 2. Validate connections
semabridge validate

# 3. Sync from Snowflake to Fabric
semabridge semantic-sync snowflake --tag v1.0

# 4. View history
semabridge history --project-id <ID>

# 5. Compare versions
semabridge diff versions --project-id <ID> --from v0.9 --to v1.0

# 6. Rollback if needed
semabridge rollback --project-id <ID> --tag v0.9
```

### Reverse Sync (Fabric → Snowflake)

```bash
# Extract from Fabric and deploy to Snowflake
semabridge semantic-sync fabric \
  --dataset-id "1cb616cc-52b7-4268-b458-b092d64c84a6" \
  --tag v1.0

# Check generated artifacts
ls output/reverse/
# semantic_view.sql
# cortex_analyst.yaml
```

### Dry Run Testing

```bash
# Test without deploying
semabridge semantic-sync snowflake --dry-run

# Review generated files
ls output/
# model.bim
# model.yaml
```

---

## Tips and Best Practices

1. **Always validate first:** Run `validate` before sync operations
2. **Use version tags:** Tag important snapshots for easy rollback
3. **Test with dry-run:** Use `--dry-run` to preview changes
4. **Monitor logs:** Check logs after operations
5. **Use behavior policies:** Customize behavior without code changes
6. **Enable caching:** Set `MODEL_CACHE_ENABLED=true` for large schemas
7. **Use filters:** Limit scope with inclusion/exclusion lists

---

## Troubleshooting

### Command not found
```bash
# Ensure installed
pip install -e .

# Or use python -m
python -m semabridge --help
```

### Connection errors
```bash
# Validate environment variables
semabridge config

# Test connections
semabridge validate
```

### Permission errors
```bash
# Check Snowflake role
SNOWFLAKE_ROLE=SYSADMIN semabridge validate

# Check Fabric permissions
# Ensure service principal has Dataset.ReadWrite.All
```

---

For more information, see:
- [Architecture Documentation](ARCHITECTURE.md)
- [Limitations](LIMITATIONS.md)
- [Contributing Guide](CONTRIBUTING.md)
