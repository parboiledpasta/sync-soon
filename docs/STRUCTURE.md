# SemaBridge - Project Structure

This project follows the standard `src`-layout pattern for Python packages.

## Directory Structure

```
semabridge/
├── src/
│   └── semabridge/          # Main package
│       ├── core/            # Execution lifecycle, config loading, logging
│       │   └── behavior.py  # YAML-driven behavioral config (DDLStrategy, PKResolutionMode)
│       ├── connectors/      # Source/target integrations (Snowflake, Fabric)
│       │   ├── snowflake_emitter.py  # Snowflake deployment (semantic views, DDL, Cortex)
│       │   └── aggregate_advisor.py  # Mandate 6: Aggregate materialization recommendations
│       ├── formats/         # Schema definitions, YAML validation
│       ├── converter/       # Transformation logic (TMSL ↔ SML)
│       │   ├── dax_translator.py  # DAX→SQL 5-tier cascade
│       │   ├── osi_to_sml.py      # OSI→SML with calculated column detection & PK validation
│       │   └── osi_to_sql.py      # OSI→SQL direct emission with idempotent DDL
│       ├── sml/             # SML model definitions and serialization
│       │   └── models.py    # Pydantic models (extends/imports inheritance fields)
│       ├── intermediate/    # OSI canonical intermediate representation
│       ├── concurrency/     # Parallel processing and orchestration
│       │   ├── orchestrator.py     # ProcessPool-based batch orchestrator
│       │   └── delta_detector.py   # Mandate 5: Hash/git-based change detection
│       ├── validation/      # Model and schema validators
│       │   └── cortex_validator.py # Mandate 6: Cortex YAML grounding validator
│       ├── repository/      # Persistence, version control, DuckDB
│       ├── cli/             # CLI commands and argument parsing
│       ├── plugins/         # Extension points (future)
│       └── utils/           # Shared helpers, logging, cache
│           └── identifiers.py  # Mandate 1: Unified IdentifierSanitizer
├── tests/                   # Unit and integration tests
├── .github/workflows/       # Mandate 5: CI/CD GitOps pipeline
├── docs/                    # Documentation
├── examples/                # Example configurations
├── scripts/                 # Utility scripts
├── main.py                  # CLI entry point (run: python main.py)
├── behavior.yaml            # Behavioral configuration
├── pyproject.toml           # Package configuration
└── README.md                # Project README
```

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

# Full pipeline
python main.py sync --name MyModel

# Reverse sync (Fabric → Snowflake)
python main.py reverse-sync --dataset-id <ID>
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
