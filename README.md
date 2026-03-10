# 🌉 Semabridge

> *Snowflake → SML → Fabric Semantic Model Pipeline*

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## ✨ Overview

**Semabridge** automates the creation of Microsoft Fabric Power BI semantic models from Snowflake metadata. It follows a clean pipeline architecture:

```
Snowflake → Extract → SML (YAML) → Emit (TMSL) → Fabric
```

### Key Features

- 🔄 **Fully Automated**: No manual modeling required
- 📊 **Complete Metadata**: Tables, columns, relationships, measures, hierarchies
- 📝 **SML Intermediate Format**: Human-readable YAML semantic layer
- 🚀 **REST API**: No XMLA endpoint required
- ⚡ **Incremental Processing**: Only process changed tables
- 🔍 **Auto-Detection**: Relationships, hierarchies, and measures

---

## 🚀 Quick Start

### Installation

**Option 1: Using UV (Recommended for Development)**

UV is a fast Python package installer and dependency manager. It's 10-100x faster than pip.

```bash
# Install UV (if not already installed)
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

**Option 2: Traditional pip**

```bash
cd sema-bridge

# Install in development mode
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

### Building Executable

Create a standalone executable for distribution:

```bash
# Build executable (requires PyInstaller)
uv run python scripts/build_exe.py

# The executable will be created at:
# - macOS/Linux: dist/SemaBridge
# - Windows: dist/SemaBridge.exe

# Run the executable
./dist/SemaBridge --help
./dist/SemaBridge --ui    # Launch Qt GUI
```

**Executable Features:**
- ✅ Single-file distribution (no Python installation required)
- ✅ All dependencies bundled (Qt6, Snowflake connector, DuckDB, etc.)
- ✅ Cross-platform (build on your target OS)
- ✅ Full Qt GUI included

### Configuration

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

### Usage

```bash
# Launch Qt GUI
semabridge --ui

# Sync from Snowflake to Fabric
semabridge semantic-sync snowflake

# Sync from Fabric to Snowflake (Reverse)
semabridge semantic-sync fabric --dataset-id <ID>

# Or step by step:
semabridge extract     # Extract Snowflake metadata
semabridge build       # Build SML model
semabridge emit        # Generate model.bim
semabridge publish     # Deploy to Fabric

# Validate connections
semabridge validate

# Show configuration
semabridge config
```

---

## 📖 Commands

| Command | Description |
|---------|-------------|
| `semantic-sync` | Synchronize semantic model between platforms |
| `compare` | Compare semantic models (Diff) |
| `rollback` | Rollback to a previous version |
| `history` | View version history |
| `extract` | Extract metadata from Snowflake |
| `build` | Build SML model from extracted metadata |
| `emit` | Generate model.bim from SML |
| `publish` | Deploy to Fabric via REST API |
| `validate` | Test Snowflake and Fabric connections |
| `config` | Show current configuration |

### Options

```bash
# Dry run (don't publish)
semabridge semantic-sync snowflake --dry-run

# Custom model name
semabridge semantic-sync snowflake --name "My Sales Model"

# Verbose logging
semabridge -v semantic-sync snowflake

# Custom output directory
semabridge semantic-sync snowflake --output-dir ./my-output
```

---

## 🏗️ Architecture

```
semabridge/
├── config/           # Configuration (Snowflake, Fabric, Model)
├── extract/          # Snowflake metadata extraction
│   ├── snowflake_extractor.py   # Table/column extraction
│   ├── relationship_detector.py # FK detection
│   ├── hierarchy_detector.py    # Date/geo/category patterns
│   └── measure_detector.py      # Numeric column detection
├── sml/              # Semantic Modeling Language (YAML)
│   ├── models.py     # Pydantic models
│   ├── serializer.py # YAML read/write
│   └── assembler.py  # Build SML from metadata
├── emit/             # Fabric model emission
│   ├── tmsl_generator.py   # model.bim generation
│   └── fabric_publisher.py # REST API integration
├── utils/            # Logging, caching
└── main.py           # CLI entry point
```

---

## 📊 Auto-Detection

### Relationships
- Foreign key constraints from Snowflake
- Naming conventions (e.g., `customer_id` → `customers.id`)

### Hierarchies
- Date patterns: Year → Quarter → Month → Day
- Geography: Country → State → City
- Categories: Category → Subcategory

### Measures
- Numeric columns in fact tables
- Common patterns: amount, quantity, total, price

---

## 🔧 Development

### Setup Development Environment

```bash
# Using UV (recommended)
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or using pip
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/semabridge --cov-report=term-missing

# Run specific test file
pytest tests/test_snowflake_extractor.py -v
```

### Code Quality

```bash
# Format code
black src/semabridge/

# Lint
ruff check src/semabridge/

# Type checking
mypy src/semabridge/
```

### Building Distribution

```bash
# Build standalone executable
uv run python scripts/build_exe.py

# The build process:
# 1. Cleans previous build artifacts
# 2. Bundles Python + all dependencies
# 3. Includes Qt6 libraries and plugins
# 4. Creates single-file executable in dist/
```

---

## 📜 License

MIT License

---

Made with ❤️ for the Platform Engineering Team
