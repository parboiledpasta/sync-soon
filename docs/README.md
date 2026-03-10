# SemaBridge Documentation

Welcome to the SemaBridge documentation. This guide will help you understand, use, and contribute to SemaBridge.

---

## 📚 Documentation Index

### 🚀 Usage Documentation

Get started with using SemaBridge for semantic model synchronization.

- **[Getting Started](Usage/GettingStarted.md)** - Installation, configuration, and basic usage
- **[CLI Reference](Usage/CliReference.md)** - Complete command-line interface documentation
- **Setup Guides**
  - [Snowflake Setup](Usage/Setup/SnowflakeSetup.md) - Configure Snowflake connection
  - [Fabric Setup](Usage/Setup/FabricSetup.md) - Configure Microsoft Fabric connection

---

### 🛠️ Development Documentation

Technical documentation for developers and contributors.

#### Core Documentation
- **[Architecture](Development/Architecture.md)** - System design, components, and execution pipeline
- **[Project Structure](Development/ProjectStructure.md)** - Directory layout and module organization
- **[Contributing](Development/Contributing.md)** - Development guidelines and best practices
- **[Roadmap](Development/Roadmap.md)** - Feature plans and implementation timeline
- **[Limitations](Development/Limitations.md)** - Known constraints and workarounds

#### Feature Documentation
- **[OSI Support](Development/Features/OsiSupport.md)** - Open Semantic Interchange implementation
- **[Formats System](Development/Features/FormatsSystem.md)** - Platform-specific format validation
- **[Concurrency Engine](Development/Features/ConcurrencyEngine.md)** - Parallel processing (planned)
- **[Build System](Development/Features/BuildSystem.md)** - UV-based build and packaging (planned)

---

### 📖 Reference Documentation

Specifications and technical references.

- **[OSI Specification](Reference/OsiSpecification/README.md)** - Open Semantic Interchange format specification

---

### 📊 Product Documentation

- **[Product Overview](Product.md)** - Business value, use cases, and success metrics

---

## 🎯 Quick Links by Role

### For End Users
1. Start with [Getting Started](Usage/GettingStarted.md)
2. Review [CLI Reference](Usage/CliReference.md) for commands
3. Check [Limitations](Development/Limitations.md) for known constraints

### For Developers
1. Read [Architecture](Development/Architecture.md) to understand the system
2. Review [Contributing](Development/Contributing.md) for development guidelines
3. Check [Roadmap](Development/Roadmap.md) for planned features
4. Explore [Feature Documentation](Development/Features/) for specific components

### For Platform Engineers
1. Review [Product Overview](Product.md) for business context
2. Check [Setup Guides](Usage/Setup/) for platform configuration
3. Read [Architecture](Development/Architecture.md) for integration points

---

## 📂 Documentation Structure

```
docs/
├── README.md                          # This file
├── Product.md                         # Product overview
│
├── Usage/                             # User-facing documentation
│   ├── GettingStarted.md             # Quick start guide
│   ├── CliReference.md               # CLI commands
│   └── Setup/                        # Platform setup guides
│       ├── SnowflakeSetup.md
│       └── FabricSetup.md
│
├── Development/                       # Developer documentation
│   ├── Architecture.md               # System design
│   ├── ProjectStructure.md           # Directory layout
│   ├── Contributing.md               # Development guidelines
│   ├── Roadmap.md                    # Feature roadmap
│   ├── Limitations.md                # Known constraints
│   └── Features/                     # Feature-specific docs
│       ├── OsiSupport.md
│       ├── FormatsSystem.md
│       ├── ConcurrencyEngine.md
│       └── BuildSystem.md
│
└── Reference/                         # Technical references
    └── OsiSpecification/             # OSI format spec
        └── README.md
```

---

## 🔍 Finding What You Need

### I want to...

**...install and use SemaBridge**
→ [Getting Started](Usage/GettingStarted.md)

**...understand how SemaBridge works**
→ [Architecture](Development/Architecture.md)

**...understand the project structure**
→ [Project Structure](Development/ProjectStructure.md)

**...contribute code**
→ [Contributing](Development/Contributing.md)

**...know what's planned**
→ [Roadmap](Development/Roadmap.md)

**...understand limitations**
→ [Limitations](Development/Limitations.md)

**...learn about OSI format**
→ [OSI Support](Development/Features/OsiSupport.md)

**...see all CLI commands**
→ [CLI Reference](Usage/CliReference.md)

**...set up Snowflake**
→ [Snowflake Setup](Usage/Setup/SnowflakeSetup.md)

**...set up Fabric**
→ [Fabric Setup](Usage/Setup/FabricSetup.md)

---

## 📝 Documentation Conventions

### File Naming
- All documentation files use **PascalCase** (e.g., `GettingStarted.md`)
- Folders use **PascalCase** (e.g., `Usage/`, `Development/`)

### Document Structure
- Each document starts with a title and brief description
- Sections use markdown headers (`##`, `###`)
- Code examples use fenced code blocks with language tags
- Cross-references use relative links

### Categories
- **Usage**: End-user documentation for using SemaBridge
- **Development**: Technical documentation for developers
- **Reference**: Specifications and technical references
- **Product**: Business and product documentation

---

## 🤝 Contributing to Documentation

Documentation improvements are welcome! See [Contributing](Development/Contributing.md) for guidelines.

### Documentation Standards
- Use clear, concise language
- Include code examples where appropriate
- Keep cross-references up to date
- Follow the existing structure and conventions

---

## 📞 Getting Help

- **Usage Questions**: Check [Getting Started](Usage/GettingStarted.md) and [CLI Reference](Usage/CliReference.md)
- **Technical Questions**: Review [Architecture](Development/Architecture.md) and [Feature Documentation](Development/Features/)
- **Known Issues**: See [Limitations](Development/Limitations.md)
- **Feature Requests**: Check [Roadmap](Development/Roadmap.md)

---

**Last Updated:** February 15, 2026  
**Version:** 2.0 (Reorganized Structure)
