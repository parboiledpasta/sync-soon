# Formats System

## Overview

The Formats System provides **platform-specific validation and formatting** for identifiers, data types, and metadata values across all supported platforms (Snowflake, Fabric, and future connectors like Databricks).

## User Stories

### US-1: Identifier Validation
**As a** data engineer  
**I want** the system to automatically validate and sanitize table/column names  
**So that** I don't encounter deployment failures due to invalid identifiers  

**Acceptance Criteria:**
- System validates identifiers against platform-specific rules
- Invalid characters are automatically sanitized
- Reserved words are detected and handled
- Users are warned about modifications

### US-2: Type Mapping
**As a** semantic model developer  
**I want** data types to be automatically mapped between platforms  
**So that** I don't need to manually map Fabric types to Snowflake types  

**Acceptance Criteria:**
- All standard data types are mapped
- Custom types fall through to `VARCHAR` (default)
- Precision and scale are preserved
- Mapping is bidirectional

### US-3: Metadata Formatting
**As a** BI analyst  
**I want** date literals and default values to be correctly formatted for each platform  
**So that** deployed models work correctly  

**Acceptance Criteria:**
- Date literals use platform-specific format
- Boolean values use platform-specific format
- Default values are correctly quoted
- Null handling is consistent

## Architecture

```
┌──────────────────┐
│   BaseFormat     │ ◄── Abstract base class
│   (interface)    │
├──────────────────┤
│  validate_id()   │
│  sanitize_id()   │
│  map_type()      │
│  format_literal()│
│  reserved_words()│
└──────┬───────────┘
       │
       ├─────────────────┐─────────────────┐
       ▼                 ▼                 ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│SnowflakeFormat│ │ FabricFormat │ │DatabricksFormat│
│              │ │              │ │  (Planned)   │
├──────────────┤ ├──────────────┤ ├──────────────┤
│ 255 char max │ │ Unicode OK   │ │ Spark rules  │
│ Force upper  │ │ Case preserve│ │ Case preserve│
│ Reserved: 300+│ │ Reserved: ~50│ │ Reserved: ~200│
└──────────────┘ └──────────────┘ └──────────────┘
```

## Base Format Interface

```python
from abc import ABC, abstractmethod
from typing import Optional

class BaseFormat(ABC):
    """Base class for platform-specific format definitions."""

    @abstractmethod
    def validate_identifier(self, name: str) -> bool:
        """Check if an identifier is valid for this platform."""

    @abstractmethod
    def sanitize_identifier(self, name: str) -> str:
        """Sanitize an identifier to be valid for this platform."""

    @abstractmethod
    def map_type(self, source_type: str, source_platform: str) -> str:
        """Map a data type from source platform to this platform."""

    @abstractmethod
    def format_date_literal(self, value: str) -> str:
        """Format a date literal for this platform."""

    @abstractmethod
    def format_boolean_literal(self, value: bool) -> str:
        """Format a boolean literal for this platform."""

    @abstractmethod
    def get_reserved_words(self) -> set[str]:
        """Return the set of reserved words for this platform."""

    @abstractmethod
    def get_max_identifier_length(self) -> int:
        """Return the maximum identifier length for this platform."""

    @abstractmethod
    def get_quoting_strategy(self) -> str:
        """Return the quoting strategy: 'always', 'when_needed', 'never'."""
```

## Format Registry

The Format Registry provides auto-discovery and lookup of platform formats:

```python
class FormatRegistry:
    """Registry for platform-specific format definitions."""

    _formats: dict[str, BaseFormat] = {}

    @classmethod
    def register(cls, platform: str, format_def: BaseFormat):
        """Register a format definition for a platform."""
        cls._formats[platform.lower()] = format_def

    @classmethod
    def get(cls, platform: str) -> BaseFormat:
        """Get the format definition for a platform."""
        return cls._formats[platform.lower()]

    @classmethod
    def available_platforms(cls) -> list[str]:
        """List all registered platforms."""
        return list(cls._formats.keys())
```

## Platform-Specific Implementations

### Snowflake Format

| Feature | Snowflake Rule |
|---------|---------------|
| Max identifier length | 255 characters |
| Case sensitivity | Force uppercase (configurable) |
| Reserved words | 300+ (ANSI SQL + Snowflake-specific) |
| Quoting | Double quotes (`"MyColumn"`) |
| Date format | `'2024-01-15'` or `TO_DATE('2024-01-15')` |
| Boolean format | `TRUE` / `FALSE` |
| Special characters | Allowed in quoted identifiers |

### Fabric Format

| Feature | Fabric Rule |
|---------|------------|
| Max identifier length | 128 characters |
| Case sensitivity | Case-preserving, case-insensitive |
| Reserved words | ~50 (DAX-specific) |
| Quoting | Square brackets (`[MyColumn]`) |
| Date format | `DATE(2024, 1, 15)` |
| Boolean format | `TRUE` / `FALSE` |
| Unicode | Full Unicode support |

### Databricks Format (Planned)

| Feature | Databricks Rule |
|---------|----------------|
| Max identifier length | 255 characters |
| Case sensitivity | Case-preserving |
| Reserved words | ~200 (Spark SQL) |
| Quoting | Backticks (`` `MyColumn` ``) |
| Date format | `'2024-01-15'` |
| Boolean format | `true` / `false` |

## Integration

The Formats System integrates with:

1. **Converters**: Apply format rules during OSI/SML conversion
2. **Emitters**: Validate identifiers before deployment
3. **Extractors**: Normalize identifiers during extraction
4. **Behavior Policy**: Override format behavior via configuration

## Configuration

Format behavior can be customized via the behavior policy:

```yaml
compatibility:
  force_uppercase: true          # Snowflake: force uppercase identifiers
  quote_identifiers: true        # Always quote identifiers
  suppress_reserved_words: true  # Prefix reserved words with L_
  max_identifier_length: 255     # Override max length
```

## Testing

```bash
# Run format-specific tests
pytest tests/test_formats.py -v
```

## Related Documentation

- [Architecture](../Architecture.md)
- [OSI Support](OsiSupport.md)
- [Limitations](../Limitations.md)
