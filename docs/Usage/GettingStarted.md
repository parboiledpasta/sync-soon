# Getting Started with SemaBridge

**Quick start guide for using SemaBridge - a universal semantic layer bridge for multi-platform synchronization**

---

## Overview

SemaBridge enables bi-directional synchronization of semantic models across multiple data platforms. Currently supporting Snowflake and Microsoft Fabric (Power BI), with an extensible architecture for easy addition of new platforms like Databricks.

---

## Installation

### Option 1: Using UV (Recommended)

UV is a fast Python package installer and dependency manager (10-100x faster than pip).

```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup
cd sema-bridge

# Create virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On macOS/Linux
# Or: .venv\Scripts\activate  # On Windows

# Install project with dev dependencies
uv pip install -e ".[dev]"
```

### Option 2: Traditional pip

```bash
cd sema-bridge

# Install in development mode
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

---

## Configuration

Create a `.env` file (copy from `.env.example`):

```env
# Snowflake
SNOWFLAKE_ACCOUNT=your-account.region
SNOWFLAKE_USER=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=your-warehouse
SNOWFLAKE_DATABASE=your-database
SNOWFLAKE_SCHEMA=PUBLIC

# Fabric
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-client-id
FABRIC_CLIENT_SECRET=your-client-secret
FABRIC_WORKSPACE_ID=your-workspace-id
```

For detailed setup instructions:
- [Snowflake Setup](Setup/SnowflakeSetup.md)
- [Fabric Setup](Setup/FabricSetup.md)

**Note:** Additional platform connectors (Databricks) are planned. The extensible architecture makes it easy to add new platforms.

---

## Basic Usage

### Launch Qt GUI

```bash
semabridge --ui
```

### Validate Connections

```bash
semabridge validate
```

### Sync from Snowflake to Target Platform

```bash
# Sync from Snowflake (can target any supported platform)
semabridge semantic-sync snowflake --tag v1.0
```

### Sync from Fabric to Target Platform

```bash
# Sync from Fabric (can target any supported platform)
semabridge semantic-sync fabric --dataset-id <ID> --tag v1.0
```

### View Version History

```bash
semabridge history --project-id <ID>
```

### Compare Versions

```bash
semabridge diff versions --project-id <ID> --from v0.9 --to v1.0
```

### Rollback to Previous Version

```bash
semabridge rollback --project-id <ID> --tag v0.9
```

---

## Common Options

```bash
# Dry run (don't deploy)
semabridge semantic-sync snowflake --dry-run

# Custom model name
semabridge semantic-sync snowflake --name "My Sales Model"

# With version tag
semabridge semantic-sync snowflake --tag v1.0

# Verbose logging
semabridge -v semantic-sync snowflake

# Custom output directory
semabridge semantic-sync snowflake --output-dir ./my-output
```

---

## Next Steps

- [CLI Reference](CliReference.md) - Complete command documentation
- [Architecture](../Development/Architecture.md) - System design
- [Limitations](../Development/Limitations.md) - Known constraints

---

**See Also:**
- [Product Overview](../Product.md)
- [Contributing Guide](../Development/Contributing.md)
