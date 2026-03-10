"""
Behavioral Configuration.

Defines the YAML schema for controlling connector behavior without changing code.
This separates "what to run" (ExecutionConfig) from "how to run it" (ConnectorBehavior).
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional
from pathlib import Path
import yaml

from pydantic import BaseModel, Field


class DDLStrategy(str, Enum):
    """DDL deployment strategy (Mandate 2)."""
    IDEMPOTENT = "idempotent"   # CREATE OR REPLACE — default
    EVOLVE = "evolve"           # ALTER TABLE ADD/RENAME — backward-compat


class PKResolutionMode(str, Enum):
    """Primary key resolution strictness (Mandate 4)."""
    STRICT = "strict"         # Abort deployment on non-physical PKs
    PERMISSIVE = "permissive" # Warn + fallback to first physical column


class SnowflakeBehavior(BaseModel):
    """Snowflake-specific behavior controls."""
    query_tag: str = Field(
        default="Semabridge_Connector",
        description="Query tag to set for all sessions"
    )
    quote_identifiers: bool = Field(
        default=True,
        description="Whether to quote all identifiers in DDL"
    )
    create_missing_tables: bool = Field(
        default=True,
        description="Auto-create source tables if missing"
    )
    validate_column_schema: bool = Field(
        default=True,
        description="Verify snowflake columns match semantic model"
    )
    use_transient_tables: bool = Field(
        default=False,
        description="Create transient tables (no fail-safe) for staging"
    )
    # Mandate 2: Idempotent DDL State Management
    ddl_strategy: DDLStrategy = Field(
        default=DDLStrategy.IDEMPOTENT,
        description="DDL strategy: 'idempotent' uses CREATE OR REPLACE, 'evolve' uses ALTER TABLE"
    )
    # Mandate 4: Physical PK Materialization
    pk_resolution_mode: PKResolutionMode = Field(
        default=PKResolutionMode.PERMISSIVE,
        description="PK resolution: 'strict' aborts on non-physical PKs, 'permissive' warns + fallback"
    )
    # Mandate 5: Warehouse Isolation
    warehouse_mapping: Dict[str, str] = Field(
        default_factory=dict,
        description="Workload-type to warehouse mapping: sync, analytics, ai"
    )

class FabricBehavior(BaseModel):
    """Fabric/PowerBI behavior controls."""
    deploy_overwrite: bool = Field(
        default=True,
        description="Overwrite existing semantic models by default"
    )
    tmsl_generation_mode: str = Field(
        default="standard",
        description="TMSL generation strategy: 'standard' or 'compatibility'"
    )

class SemanticModelBehavior(BaseModel):
    """Semantic modeling rules."""
    view_suffix: str = Field(
        default="_semantic",
        description="Suffix for generated semantic views"
    )
    enable_date_dimension: bool = Field(
        default=True,
        description="Auto-generate date dimension if needed"
    )
    fact_detection_threshold: int = Field(
        default=1,
        description="Minimum cardinality setting, currently unused but reserved"
    )
    metric_overrides: Dict[str, str] = Field(
        default_factory=dict,
        description="Manual SQL overrides for complex measures (Name -> SQL)"
    )
    sync_all_attributes: bool = Field(
        default=True,
        description="Whether to include all attributes (including measure candidates and hidden columns) in Snowflake Semantic Views"
    )
    # Mandate 3: DAX Logical Unspooling
    unspool_calculated_columns: bool = Field(
        default=True,
        description="Unspool embedded DAX calculated columns into SML dataset definitions"
    )

class CompatibilityBehavior(BaseModel):
    """SQL compatibility fixes."""
    suppress_reserved_words: bool = Field(
        default=True,
        description="Prefix reserved words (e.g. TABLE -> L_TABLE)"
    )
    force_uppercase: bool = Field(
        default=True,
        description="Force all identifiers to uppercase"
    )
    # Mandate 1: Extended reserved word support
    additional_reserved_words: List[str] = Field(
        default_factory=list,
        description="Extra reserved words to supplement the built-in Snowflake list"
    )

class FeatureFlags(BaseModel):
    """Safe toggles for new/experimental features."""
    enable_cortex_analyst: bool = Field(
        default=True,
        description="Generate Cortex Analyst YAML artifacts"
    )
    enable_parallel_execution: bool = Field(
        default=False,
        description="Experimental: Parallel execution of unrelated tasks"
    )
    skip_validation_on_dry_run: bool = Field(
        default=True,
        description="Skip deep validation during dry runs"
    )
    # Mandate 6: Cortex Grounding
    cortex_require_descriptions: bool = Field(
        default=True,
        description="Require non-empty descriptions on all metrics/dimensions for Cortex YAML"
    )
    # Mandate 6: Aggregate Awareness
    auto_materialize_aggregates: bool = Field(
        default=False,
        description="Auto-execute recommended aggregate dynamic table DDL after deployment"
    )

class LegacyCleanup(BaseModel):
    """Cleanup options for old features."""
    drop_deprecated_views: bool = Field(
        default=False,
        description="Drop old _SV views if detected"
    )

class ConnectorBehavior(BaseModel):
    """
    Root configuration object for Connector Policy.
    Controls behavior, feature flags, and compatibility settings.
    """
    snowflake: SnowflakeBehavior = Field(default_factory=SnowflakeBehavior)
    fabric: FabricBehavior = Field(default_factory=FabricBehavior)
    semantic_model: SemanticModelBehavior = Field(default_factory=SemanticModelBehavior)
    compatibility: CompatibilityBehavior = Field(default_factory=CompatibilityBehavior)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    legacy: LegacyCleanup = Field(default_factory=LegacyCleanup)

    @classmethod
    def from_yaml(cls, path: Path) -> "ConnectorBehavior":
        """Load behavior policy from YAML file."""
        if not path or not path.exists():
             # Return defaults if no file provided
            return cls()
        
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            
        return cls(**raw)
