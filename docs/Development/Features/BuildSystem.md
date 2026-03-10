# Build System

## Overview

SemaBridge uses a **UV-based build system** for fast dependency management and **PyInstaller** for generating standalone executables. This enables distribution of SemaBridge as a single binary without requiring end users to install Python.

## UV Package Management

### Why UV?
- **Speed**: Dependency resolution in < 5 seconds (vs. minutes with pip)
- **Reproducibility**: Deterministic lock files
- **Compatibility**: Drop-in replacement for pip

### Installation

```bash
# Install UV
pip install uv

# Or via curl (Unix)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via PowerShell (Windows)
irm https://astral.sh/uv/install.ps1 | iex
```

### Usage

```bash
# Install dependencies from pyproject.toml
uv pip install -e .

# Install with dev dependencies
uv pip install -e ".[dev]"

# Sync dependencies (fast)
uv pip sync requirements.txt

# Add a new dependency
uv pip install <package>
```

## PyInstaller Build

### Single Executable Generation

SemaBridge can be packaged as a standalone executable:

```bash
# Build the CLI executable
python scripts/build_exe.py

# Build the GUI (Dashboard) executable
python scripts/build_gui.py
```

### Build Specifications

**CLI Build** (`SemaBridge.spec`):
- Entry point: `main.py`
- Includes: All `semabridge` packages, YAML configs
- Output: `dist/SemaBridge` (or `SemaBridge.exe` on Windows)

**GUI Build** (`SemaBridgeDashboard.spec`):
- Entry point: `scripts/gui_launcher.py`
- Includes: All `semabridge` packages, PyQt6, YAML configs
- Output: `dist/SemaBridgeDashboard` (or `.exe` on Windows)

### Build Requirements

| Requirement | Target |
|------------|--------|
| Build time | < 5 minutes |
| Executable size | < 100MB |
| Startup time | < 3 seconds |
| Platforms | Windows, macOS, Linux |

### Build Configuration

The `pyproject.toml` defines all package metadata and dependencies:

```toml
[project]
name = "semabridge"
version = "1.0.0"
requires-python = ">=3.10"

[project.scripts]
semabridge = "semabridge.cli.main:app"

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"
```

## CI/CD Integration

### Build Pipeline

```yaml
# Example GitHub Actions workflow
name: Build
on: [push, pull_request]

jobs:
  build:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.13'
      - run: pip install uv
      - run: uv pip install -e ".[dev]"
      - run: pytest tests/ -v
      - run: python scripts/build_exe.py
      - uses: actions/upload-artifact@v4
        with:
          name: semabridge-${{ matrix.os }}
          path: dist/
```

### Release Process

1. Update version in `pyproject.toml`
2. Run full test suite
3. Build executables for all platforms
4. Create GitHub release with binaries
5. Update documentation

## Development Workflow

```bash
# Clone and setup
git clone <repo>
cd sema-bridge
uv pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Build executable
python scripts/build_exe.py

# Run from source
python main.py --help

# Run as installed package
semabridge --help
```

## Related Documentation

- [Getting Started](../../Usage/GettingStarted.md)
- [Contributing](../Contributing.md)
- [Project Structure](../ProjectStructure.md)
