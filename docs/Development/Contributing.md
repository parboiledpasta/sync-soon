# Contributing to SemaBridge

Thank you for contributing to SemaBridge! This document provides guidelines to ensure consistent, high-quality contributions to our universal semantic layer bridge.

---

## About SemaBridge

SemaBridge is a **universal semantic layer bridge** that enables bi-directional synchronization of semantic models across multiple data platforms. We currently support Snowflake and Microsoft Fabric (Power BI), with an extensible architecture designed for easy integration of additional platforms like Databricks and more.

### Architecture Principles

- **Platform-agnostic design**: All transformations go through standardized SML/OSI intermediate formats
- **Extensible connector system**: Easy addition of new platform connectors via plugin architecture
- **Clean separation of concerns**: Extract → Transform → Load with clear boundaries

---

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- Access to at least one supported platform for testing:
  - Snowflake (recommended for development)
  - Microsoft Fabric (Power BI)
  - Future: Databricks

### Installation

```powershell
# Clone the repository
git clone https://github.com/inarva-solutions-pvt-ltd/sema-bridge.git
cd sema-bridge

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your credentials

# Verify setup
python main.py validate
```

---

## Code Style

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Files/Folders | lowercase, snake_case | `snowflake_extractor.py` |
| Classes | PascalCase | `SnowflakeEmitter` |
| Functions | snake_case | `extract_metadata()` |
| Constants | UPPERCASE_SNAKE | `MAX_RETRY_COUNT` |
| Private | Leading underscore | `_internal_method()` |

### File Organization

```
src/semabridge/
├── cli/                 # CLI commands (Typer)
│   ├── main.py          # Main CLI entry point
│   ├── semantic_commands.py  # Version control commands
│   ├── diff_commands.py      # Comparison utilities
│   ├── logs_commands.py      # Log management
│   └── uv_commands_cli.py    # UV build system
├── core/                # Central orchestration
│   ├── execution_engine.py   # 10-step pipeline
│   ├── settings.py           # Pydantic settings
│   ├── behavior.py           # Behavior policies
│   └── ...
├── connectors/          # Platform integrations (extensible)
│   ├── snowflake_extractor.py    # Snowflake connector
│   ├── snowflake_emitter.py
│   ├── fabric_extractor.py       # Fabric connector
│   ├── fabric_publisher.py
│   ├── tmsl_generator.py
│   └── ...                       # Future: databricks_*, etc.
├── converter/           # Transformation logic (platform-agnostic)
│   ├── tmsl_to_osi.py           # Fabric → OSI
│   ├── osi_to_sml.py            # OSI → SML
│   ├── sml_to_osi.py            # SML → OSI
│   ├── dax_translator.py
│   └── ...
├── intermediate/        # OSI models
├── sml/                 # SML models
├── repository/          # DuckDB version control
├── formats/             # Schema definitions
├── ui/                  # Qt GUI components
├── validation/          # Validation engine
├── plugins/             # Plugin system
└── utils/               # Shared utilities
```

### Code Principles

1. **Maintainability over cleverness** - Write code a new engineer can understand
2. **Explicit over implicit** - Clear variable names, no magic numbers
3. **SML/OSI adapter is mandatory** - All data transformations must validate through SML/OSI
4. **No junk files** - No `temp`, `misc`, or experimental files

---

## Testing

### Requirements

| Metric | Threshold |
|--------|-----------|
| **Coverage** | ≥ 80% for new code |
| **All Tests** | Must pass before merge |

### Running Tests

```powershell
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/semabridge --cov-report=term-missing

# Run specific test file
pytest tests/test_sml_models.py -v
```

### Writing Tests

- Place tests in `tests/` directory
- Name test files `test_<module>.py`
- Use fixtures from `tests/conftest.py`
- Mock external services (Snowflake, Fabric) for unit tests

---

## Pull Request Process

### Before Submitting

- [ ] All tests pass locally
- [ ] Coverage ≥ 80% for new code
- [ ] No debug/print statements
- [ ] Documentation updated (if API changed)
- [ ] Follows naming conventions
- [ ] No vague file names

### PR Description Template

```markdown
## Summary
Brief description of changes

## Changes
- List of specific changes

## Testing
- How this was tested

## Related Issues
Fixes #123
```

### Review Criteria

1. **Correctness**: Does it work as intended?
2. **Tests**: Are changes covered by tests?
3. **Style**: Follows conventions?
4. **Architecture**: Aligns with design principles?
5. **Documentation**: Is it clear what changed?

---

## Architecture Principles

1. **SML as Intermediate Representation** - Decouples source/target for extensibility; supports OSI interop
2. **10-Step Execution Pipeline** - Enforces consistent execution flow with proper error handling
3. **DuckDB for State** - Embedded, zero-config versioning with SQL-queryable history
4. **Non-destructive Rollback** - Creates new snapshot; history preserved for audit
5. **Tiered DAX Translation** - Graceful degradation for complex expressions; manual overrides available
6. **Behavior Policy System** - Separates "what to run" from "how to run it"; no code changes needed
7. **Identifier Sanitization** - Automatic handling of reserved words and special characters
8. **Qt GUI** - Cross-platform desktop interface for visual operations

See `docs/ARCHITECTURE.md` for detailed design documentation.

---

## Adding New Features

### New Connector

1. Create file in `src/semabridge/connectors/`
2. Implement extraction/emission interface
3. Add format definition in `src/semabridge/formats/`
4. Add tests in `tests/`
5. Update `docs/ARCHITECTURE.md`
6. Update `docs/LIMITATIONS.md` with any constraints

### New CLI Command

1. Add to appropriate command file in `src/semabridge/cli/`
2. Follow existing command patterns (use Typer)
3. Add to UV registry if needed (`core/uv_commands.py`)
4. Add tests
5. Update `docs/CLI_REFERENCE.md`

### New Format

1. Add schema in `src/semabridge/formats/`
2. Implement validation in SML layer
3. Add sanitization rules if needed
4. Add tests
5. Update `docs/LIMITATIONS.md`

### New UI Component

1. Create file in `src/semabridge/ui/`
2. Follow Qt6 patterns
3. Use workers for background tasks
4. Add to main window if needed
5. Test on multiple platforms

### New Behavior Policy

1. Add to `ConnectorBehavior` in `core/behavior.py`
2. Create example policy in `examples/`
3. Document in `docs/ARCHITECTURE.md`
4. Add tests

---

## Questions?

- Review existing code in `src/semabridge/`
- Check `docs/ARCHITECTURE.md` for design rationale
- Read `agent.md` for behavioral guidelines
