# SemaBridge - Project Structure

This project follows the standard `src`-layout pattern for Python packages with an extensible, plugin-based architecture for multi-platform support.

## Overview

SemaBridge is designed as a **universal semantic layer bridge** with:
- **Extensible connector system**: Easy addition of new platform connectors
- **Standardized intermediate formats**: SML/OSI for platform-agnostic representation
- **Plugin architecture**: Modular design for platform integrations

## Directory Structure

```
semabridge/
├── src/
│   └── semabridge/          # Main package
│       ├── core/            # Execution lifecycle, config loading, logging
│       ├── connectors/      # Platform integrations (Snowflake, Fabric, future: Databricks)
│       ├── formats/         # Schema definitions, YAML validation
│       ├── converter/       # Transformation logic (TMSL ↔ SML ↔ OSI)
│       ├── intermediate/    # OSI models (platform-agnostic intermediate format)
│       ├── sml/             # SML model definitions and serialization
│       ├── repository/      # Persistence, version control, DuckDB
│       ├── cli/             # CLI commands and argument parsing
│       ├── plugins/         # Extension points for new platform connectors
│       └── utils/           # Shared helpers, logging, cache
├── tests/                   # Unit and integration tests
├── docs/                    # Documentation
├── examples/                # Example configurations (Snowflake, Fabric, Databricks)
├── scripts/                 # Build and utility scripts
├── main.py                  # CLI entry point (run: python main.py)
├── pyproject.toml           # Package configuration
└── README.md                # Project README
```

## Connector Architecture

### Current Connectors
- **Snowflake**: Extract from INFORMATION_SCHEMA, deploy to Semantic Views
- **Microsoft Fabric**: Extract via REST API, deploy model.bim

### Planned Connectors
- **Databricks Unity Catalog**: Planned for Q2 2026

### Adding New Connectors
New platform connectors can be easily added by:
1. Implementing the connector interface in `src/semabridge/connectors/`
2. Adding format converters in `src/semabridge/converter/`
3. Registering the connector in the plugin system

## Module Mapping

| Old Location           | New Location                    |
|------------------------|---------------------------------|
| `semabridge/config/`   | `src/semabridge/core/` + `formats/` |
| `semabridge/extract/`  | `src/semabridge/connectors/`    |
| `semabridge/emit/`     | `src/semabridge/connectors/`    |
| `semabridge/transform/`| `src/semabridge/converter/`     |
| `semabridge/state/`    | `src/semabridge/repository/`    |
| `semabridge/api/`      | `src/semabridge/repository/`    |
| `semabridge/sml/`      | `src/semabridge/sml/`           |
| `semabridge/cli/`      | `src/semabridge/cli/`           |
| `semabridge/utils/`    | `src/semabridge/utils/`         |

## Running the CLI

```bash
# From project root
python main.py --help

# Show version
python main.py version

# Sync from any supported platform
python main.py sync --name MyModel

# Platform-specific sync examples
python main.py semantic-sync snowflake --tag v1.0
python main.py semantic-sync fabric --dataset-id <ID> --tag v1.0
```

## Installing as Package

```bash
pip install -e .
semabridge --help
```

## Running Tests

```bash
pytest tests/ -v
```
