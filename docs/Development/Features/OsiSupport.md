# OSI Support in SemaBridge

## Overview

SemaBridge implements the [Open Semantic Interchange (OSI)](../Reference/OsiSpecification/README.md) specification as a platform-agnostic intermediate format for semantic model conversion. OSI serves as the **universal interchange layer** between different semantic model platforms.

## Architecture

```
┌─────────────────┐                              ┌─────────────────┐
│  Microsoft      │                              │   Snowflake     │
│  Fabric (TMSL)  │──┐                      ┌──▶ │  Semantic Views │
└─────────────────┘  │                      │    └─────────────────┘
                     ▼                      │
              ┌─────────────┐    ┌──────────┐
              │     OSI     │───▶│   SML    │
              │  (Neutral)  │    │(SemaBridge│
              └─────────────┘    │  Native) │
                     ▲           └──────────┘
                     │                      │
┌─────────────────┐  │                      │    ┌─────────────────┐
│  Databricks     │──┘                      └──▶ │  Future         │
│  Unity Catalog  │                              │  Platforms      │
└─────────────────┘                              └─────────────────┘
```

### Conversion Pipeline

1. **Extract** from source platform (Fabric, Snowflake, etc.)
2. **Convert** to OSI (platform-specific → neutral)
3. **Transform** OSI → SML (SemaBridge internal format)
4. **Emit** from SML to target platform

### Why Two Intermediate Formats?

- **OSI**: Platform-agnostic interchange format (community standard)
- **SML**: SemaBridge-specific format with extra metadata (enrichment, versioning, tiers)

OSI ensures interoperability with other tools; SML provides SemaBridge-specific features.

## Converters

### TMSLToOSIConverter

Converts Microsoft Fabric TMSL (Tabular Model Scripting Language) to OSI format.

**Location:** `src/semabridge/intermediate/tmsl_to_osi.py`

**Key Mappings:**
| TMSL Concept | OSI Concept |
|-------------|-------------|
| Database | SemanticModel |
| Table | Dataset |
| Column | Field |
| Measure | Metric |
| Relationship | Relationship |
| Hierarchy | Dimension |
| Level | HierarchyLevel |

**Usage:**
```python
from semabridge.intermediate.tmsl_to_osi import TMSLToOSIConverter

converter = TMSLToOSIConverter()
osi_model = converter.convert(tmsl_data)
```

### SMLToOSIConverter

Converts SemaBridge SML to OSI format.

**Location:** `src/semabridge/intermediate/sml_to_osi.py`

**Key Mappings:**
| SML Concept | OSI Concept |
|------------|-------------|
| SMLModel | SemanticModel |
| SMLTable | Dataset |
| SMLColumn | Field |
| SMLMeasure | Metric |
| SMLRelationship | Relationship |
| SMLHierarchy | Dimension |

**Usage:**
```python
from semabridge.intermediate.sml_to_osi import SMLToOSIConverter

converter = SMLToOSIConverter()
osi_model = converter.convert(sml_model)
```

### OSIToSMLConverter

Converts OSI format back to SemaBridge SML.

**Location:** `src/semabridge/intermediate/osi_to_sml.py`

**Key Mappings:**
| OSI Concept | SML Concept |
|------------|-------------|
| SemanticModel | SMLModel |
| Dataset | SMLTable |
| Field | SMLColumn |
| Metric | SMLMeasure |
| Relationship | SMLRelationship |
| Dimension | SMLHierarchy |

**Usage:**
```python
from semabridge.intermediate.osi_to_sml import OSIToSMLConverter

converter = OSIToSMLConverter()
sml_model = converter.convert(osi_model)
```

## Type Mappings

### Data Type Conversion

| OSI Type | SML Type | Snowflake Type | TMSL Type |
|----------|----------|---------------|-----------|
| `string` | `STRING` | `VARCHAR` | `String` |
| `int64` | `NUMBER` | `NUMBER(38,0)` | `Int64` |
| `double` | `FLOAT` | `FLOAT` | `Double` |
| `decimal` | `NUMBER` | `NUMBER(p,s)` | `Decimal` |
| `boolean` | `BOOLEAN` | `BOOLEAN` | `Boolean` |
| `dateTime` | `TIMESTAMP` | `TIMESTAMP_NTZ` | `DateTime` |
| `date` | `DATE` | `DATE` | - |
| `binary` | `BINARY` | `BINARY` | `Binary` |

### Aggregation Mapping

| OSI Aggregation | SML Aggregation | DAX Function |
|----------------|-----------------|--------------|
| `sum` | `SUM` | `SUM()` |
| `average` | `AVG` | `AVERAGE()` |
| `count` | `COUNT` | `COUNT()` |
| `distinctCount` | `COUNT_DISTINCT` | `DISTINCTCOUNT()` |
| `min` | `MIN` | `MIN()` |
| `max` | `MAX` | `MAX()` |
| `none` | `NONE` | - |

### Relationship Cardinality

| OSI Cardinality | SML Cardinality |
|----------------|-----------------|
| `one_to_many` | `ONE_TO_MANY` |
| `many_to_one` | `MANY_TO_ONE` |
| `one_to_one` | `ONE_TO_ONE` |
| `many_to_many` | `MANY_TO_MANY` |

## Usage Examples

### Full Pipeline: Fabric → Snowflake

```python
from semabridge.intermediate.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.osi_to_sml import OSIToSMLConverter

# Step 1: Extract TMSL from Fabric
tmsl_data = fabric_extractor.extract("MyModel")

# Step 2: Convert TMSL → OSI
tmsl_converter = TMSLToOSIConverter()
osi_model = tmsl_converter.convert(tmsl_data)

# Step 3: Convert OSI → SML
sml_converter = OSIToSMLConverter()
sml_model = sml_converter.convert(osi_model)

# Step 4: Deploy SML → Snowflake
snowflake_emitter.deploy(sml_model)
```

### Round-Trip Conversion

```python
from semabridge.intermediate.sml_to_osi import SMLToOSIConverter
from semabridge.intermediate.osi_to_sml import OSIToSMLConverter

# SML → OSI → SML (round-trip)
sml_to_osi = SMLToOSIConverter()
osi_to_sml = OSIToSMLConverter()

osi_model = sml_to_osi.convert(original_sml)
round_tripped = osi_to_sml.convert(osi_model)

# Data should be preserved
assert len(round_tripped.tables) == len(original_sml.tables)
```

### Export OSI YAML

```python
from semabridge.intermediate.sml_to_osi import SMLToOSIConverter
import yaml

converter = SMLToOSIConverter()
osi_model = converter.convert(sml_model)

# Export as YAML
osi_dict = osi_model.to_dict()
with open("model.osi.yaml", "w") as f:
    yaml.dump(osi_dict, f, default_flow_style=False)
```

## Benefits

### For SemaBridge Users
- **Multi-platform support**: Convert between any supported platforms through OSI
- **Future-proof**: New platforms just need OSI converters
- **Lossless conversion**: Round-trip conversion preserves all data

### For the Ecosystem
- **Interoperability**: OSI models can be shared between tools
- **Standardization**: Common format for semantic models
- **Open**: Based on the open OSI specification (Apache 2.0)

## Testing

### Unit Tests
- Type mapping tests for all converters
- Round-trip conversion tests
- Edge case handling (empty models, null values)

### Integration Tests
- Full pipeline tests (Fabric → OSI → SML → Snowflake)
- Version control integration (snapshot OSI models)

### Running OSI Tests
```bash
pytest tests/test_osi_models.py -v
pytest tests/test_osi_to_sml.py -v
```

## Performance

### Conversion Speed
- Small models (<50 tables): < 1 second
- Medium models (50-200 tables): 1-5 seconds
- Large models (200+ tables): 5-15 seconds

### Memory Usage
- OSI model overhead: ~2KB per table
- SML model overhead: ~3KB per table
- Conversion buffer: ~1.5x model size

## Related Documentation

- [OSI Specification](../Reference/OsiSpecification/README.md)
- [Architecture](Architecture.md)
- [Formats System](Features/FormatsSystem.md)
