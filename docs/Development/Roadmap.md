# SemaBridge Roadmap

**Version:** 1.0  
**Last Updated:** February 15, 2026  
**Status:** Active Development

---

## Overview

This roadmap outlines the planned features and improvements for SemaBridge, a **universal semantic layer bridge** for multi-platform synchronization. The project follows a phased approach with clear priorities and dependencies, focusing on extensibility and easy onboarding of new platform connectors.

---

## Vision

SemaBridge aims to be the **universal bridge** for semantic layer synchronization across all major data platforms:

- ✅ **Currently Supported:** Snowflake, Microsoft Fabric (Power BI)
- 🔄 **In Development:** Formats system, concurrency engine
- 📋 **Planned:** Databricks Unity Catalog
- 🔌 **Future:** Additional platforms via extensible plugin system

---

## Current Status

### Completed Features ✅
- Core architecture and interfaces
- OSI/SML intermediate models
- OSI ↔ SML bidirectional conversion (fully implemented)
- TMSL → OSI → SML conversion pipeline
- Snowflake and Fabric connectors
- DuckDB-based version control
- Basic CLI commands
- Semantic diff engine
- Rollback orchestrator
- Qt-based GUI for configuration and version management

### In Progress 🔄
- Format system (partially implemented)
- Logging configuration
- Output location management

### Planned Features 📋
- Databricks Unity Catalog connector
- Additional platform connectors via plugin system
- Concurrency engine for parallel processing
- Build system with UV
- Wildcard/regex model selection
- Multi-target broadcasting

---

## Implementation Phases

### Phase 1: Core Infrastructure (Weeks 1-4)
**Goal:** Complete foundational systems that other features depend on

#### ✅ OSI ↔ SML Conversion (Complete)
- **Priority:** High
- **Effort:** 60 hours
- **Status:** Complete

Bidirectional conversion between OSI and SML formats is fully implemented and in production use.

**Completed:**
- TMSL → OSI → SML conversion pipeline
- SML ↔ OSI bidirectional converters
- Round-trip conversion with data preservation
- Type mapping and enum conversions
- Integration with execution engine

#### Formats System
- **Priority:** High
- **Effort:** 74 hours
- **Status:** In Progress

Platform-specific identifier validation, type mapping, and metadata value formatting for Snowflake, Fabric, and Databricks.

**Key Deliverables:**
- Base format interface
- Snowflake format implementation
- Fabric format implementation
- Format registry with auto-discovery
- Integration with converters

**Requirements:**
- System validates identifiers against platform-specific rules
- Invalid identifiers are automatically sanitized
- Data types map correctly between platforms
- Date/boolean literals format correctly for each platform

#### OSI ↔ SML Conversion
- **Priority:** High
- **Effort:** 60 hours
- **Status:** ✅ Complete

Bidirectional conversion between OSI and SML formats.

**Completed Deliverables:**
- ✅ SML to OSI converter (`SMLToOSIConverter`)
- ✅ OSI to SML converter (`OSIToSMLConverter`)
- ✅ TMSL to OSI converter (`TMSLToOSIConverter`)
- ✅ Round-trip conversion tests
- ✅ Integration with execution pipeline
- ✅ Type mapping between OSI and SML enums
- ✅ DAX translation during OSI → SML conversion

**Implementation:**
- Round-trip conversion preserves all data
- Full support for datasets, columns, metrics, relationships, dimensions, hierarchies
- Integrated into main execution engine
- Used in Fabric → Snowflake sync pipeline

#### Logging System
- **Priority:** Low
- **Effort:** 20 hours
- **Status:** In Progress

Configurable logging with hierarchical configuration.

**Key Deliverables:**
- Configurable log levels
- Hierarchical configuration (CLI > project > global)
- Log file management
- Structured logging

---

### Phase 2: Performance & Scalability (Weeks 5-8)
**Goal:** Enable high-throughput processing and parallel execution

#### Concurrency Engine
- **Priority:** High
- **Effort:** 124 hours
- **Status:** Not Started

Parallel model processing with intelligent retry logic and progress dashboard.

**Key Deliverables:**
- Execution engine with worker pool management
- Retry logic with exponential backoff
- Live progress dashboard (Rich library)
- Failure strategies (best-effort, strict)
- Multi-target broadcasting

**Requirements:**
- Process multiple models in parallel
- Utilize available CPU cores efficiently
- Retry transient failures automatically
- Display real-time progress
- Support one source → multiple targets

#### Wildcard Selection
- **Priority:** Medium
- **Effort:** 40 hours
- **Status:** Not Started

Pattern-based model selection and exclusion rules.

**Key Deliverables:**
- Wildcard pattern matching
- Model exclusion rules
- Integration with concurrency engine

**Requirements:**
- Support glob patterns (*, prefix*, *suffix)
- Support exclusion rules
- Work with parallel processing

---

### Phase 3: User Experience (Weeks 9-12)
**Goal:** Improve usability with better distribution

#### Build System
- **Priority:** Medium
- **Effort:** 60 hours
- **Status:** Not Started

UV-based build system for single executable generation.

**Key Deliverables:**
- UV package management integration
- PyInstaller build script
- Single executable (Windows/Linux/macOS)
- Build documentation

**Requirements:**
- Dependencies resolve in < 5 seconds
- Single executable file
- File size < 100MB
- Startup time < 3 seconds

---

### Phase 4: Platform Expansion (Weeks 13-16)
**Goal:** Add support for additional platforms via extensible connector system

#### Databricks Unity Catalog Connector
- **Priority:** Medium
- **Effort:** 80 hours
- **Status:** Planned (Q2 2026)

Support for Databricks Unity Catalog as both source and target.

**Key Deliverables:**
- Databricks extractor (Unity Catalog metadata)
- Databricks emitter (deploy semantic models)
- Databricks format definition
- OSI/SML converters for Databricks
- Integration tests

**Requirements:**
- Extract from Databricks Unity Catalog
- Deploy semantic models to Databricks
- Format validation works correctly
- Seamless integration via SML/OSI pipeline

#### Additional Platform Connectors
- **Priority:** Low
- **Effort:** Variable
- **Status:** Future

The extensible plugin architecture makes it easy to add new platform connectors:
- Redshift
- Azure Synapse
- Teradata
- Oracle
- Other data platforms

**Connector Development Process:**
1. Implement extractor interface
2. Implement emitter interface
3. Add format definition
4. Create OSI/SML converters
5. Register in plugin system
6. Add integration tests

---

## Feature Details

### Formats System

**User Stories:**
- As a data engineer, I want the system to automatically validate and sanitize table/column names so that I don't encounter deployment failures
- As a semantic model developer, I want data types to be automatically mapped between platforms
- As a BI analyst, I want date literals and default values to be correctly formatted for each platform

**Technical Requirements:**
- Every supported connector must have a dedicated Format Definition
- System must provide identifier validation and sanitization
- System must format date/boolean literals correctly for each platform
- Type mappings must be bidirectional

### Concurrency Engine

**User Stories:**
- As a data engineer, I want to process multiple semantic models in parallel so that I can sync 50+ models in minutes
- As a platform engineer, I want to deploy one model to multiple targets simultaneously
- As a system administrator, I want transient failures to be automatically retried
- As a user, I want to see real-time progress of parallel operations

**Technical Requirements:**
- Process multiple models in parallel when wildcard selection matches multiple models
- Support one source → multiple targets architecture
- Retry transient failures automatically with exponential backoff
- Display live progress dashboard
- Auto-detect CPU core count and adapt worker pool

### Build System

**User Stories:**
- As a developer, I want extremely fast dependency resolution so that I can iterate quickly
- As an end user, I want a single executable file so that I don't need to install Python

**Technical Requirements:**
- Project must use UV for dependency management
- Build must produce standalone binary
- Binary must bundle Python interpreter and all dependencies
- Build must be reproducible

---

## Timeline Summary

| Phase | Duration | Features | Status |
|-------|----------|----------|--------|
| Phase 1 | 4 weeks | Formats System, ~~OSI ↔ SML~~, Logging | OSI ✅ Complete |
| Phase 2 | 4 weeks | Concurrency Engine, Wildcard Selection | Not Started |
| Phase 3 | 4 weeks | Build System | Not Started |
| Phase 4 | 4 weeks | Databricks Connector | Planned Q2 2026 |
| **Total** | **16 weeks** | | |

---

## Success Metrics

### Technical Metrics
- 80%+ code coverage
- All tests passing
- Build time < 5 minutes
- Executable size < 100MB
- Parallel speedup > 5x (8 cores)

### User Metrics
- Configuration wizard completion rate > 90%
- UI usability score > 4/5
- Documentation clarity score > 4/5

### Business Metrics
- Sync time reduction > 60%
- User adoption > 80%
- Support tickets < 10 per month

---

## Dependencies

```
Formats System
    └── Concurrency Engine
        └── Wildcard Selection

OSI ↔ SML Conversion (✅ Complete - No dependencies)

Databricks Connector
    └── Formats System

Build System (Independent)
Logging System (Independent)
```

---

## Out of Scope

The following features are explicitly out of scope for the current roadmap:

- Web-based deployment (cloud hosting)
- Multi-user support
- Real-time collaboration
- Mobile app
- Distributed execution across multiple machines
- GPU acceleration
- Sentinel system (monitoring, analysis, governance)

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| PyInstaller bundling issues | Medium | High | Test early, use Nuitka as backup |
| Concurrency bugs | High | High | Extensive testing, gradual rollout |
| Format incompatibilities | Medium | Medium | Comprehensive format tests |
| Databricks API changes | Low | Low | Version lock dependencies |

---

## Contact

For questions about the roadmap:
- Review feature documentation in `docs/features/`
- Check implementation status in task lists
- See `docs/ARCHITECTURE.md` for technical details

---

**Last Updated:** February 14, 2026
