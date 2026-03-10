"""
SQL Override Schema & Models.

Defines the data models for the Manual SQL Override file format.
An override file represents a three-layer architecture:
    Layer 1 — Dynamic Table (materialized pre-computation)
    Layer 2 — Semantic View (business-consumable metric projection)
    Layer 3 — Cortex Analyst Metadata (NLP synonyms and descriptions)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from semabridge.converter.tiered_safety import (
    HazardCategory,
    OverrideLayerType,
    SafetyTier,
)


# ---------------------------------------------------------------------------
# Layer 1: Dynamic Table Configuration
# ---------------------------------------------------------------------------

class WindowFunctionSpec(BaseModel):
    """Specification for a SQL window function used in a Dynamic Table."""
    function: str = Field(..., description="Window function name (RANK, ROW_NUMBER, DENSE_RANK, SUM, etc.)")
    partition_by: List[str] = Field(default_factory=list, description="PARTITION BY columns")
    order_by: List[str] = Field(default_factory=list, description="ORDER BY columns")
    order_direction: str = Field(default="ASC", description="Sort direction (ASC/DESC)")
    alias: str = Field(default="", description="Output column alias")
    filter_condition: Optional[str] = Field(default=None, description="Optional CASE WHEN condition wrapping the window function")


class DynamicTableLayer(BaseModel):
    """Layer 1: Dynamic Table pre-computation configuration.
    
    Translates DAX nested row contexts (EARLIER, RANKX) and complex filter
    contexts into Snowflake set-based operations using window functions
    and standard aggregations.
    """
    table_name: str = Field(..., description="Dynamic table name (e.g., optout_event_sequence_dt)")
    target_lag: str = Field(default="1 hour", description="Snowflake Dynamic Table TARGET_LAG")
    warehouse: str = Field(default="ANALYTICS_COMPUTE_WH", description="Compute warehouse")
    source_table: str = Field(..., description="Source table to read from")
    select_columns: List[str] = Field(default_factory=list, description="Columns to SELECT from source")
    window_functions: List[WindowFunctionSpec] = Field(default_factory=list, description="Window function specifications")
    filter_conditions: List[str] = Field(default_factory=list, description="WHERE clause conditions")
    description: str = Field(default="", description="Human-readable description of what this layer computes")


# ---------------------------------------------------------------------------
# Layer 2: Semantic View Configuration 
# ---------------------------------------------------------------------------

class SemanticDimension(BaseModel):
    """A dimension column exposed in the Semantic View."""
    name: str = Field(..., description="Business-friendly dimension name")
    source_column: str = Field(..., description="Physical column reference (e.g., events.user_id)")
    description: str = Field(default="", description="Dimension description")


class SemanticFact(BaseModel):
    """A fact column exposed in the Semantic View."""
    name: str = Field(..., description="Business-friendly fact name")
    source_column: str = Field(..., description="Physical column reference")
    description: str = Field(default="", description="Fact description")


class SemanticMetricSpec(BaseModel):
    """A metric definition within the Semantic View METRICS clause."""
    name: str = Field(..., description="Metric name")
    expression: str = Field(..., description="SQL aggregate expression (e.g., COUNT(CASE WHEN ... THEN ... END))")
    description: str = Field(default="", description="Metric description")
    is_override: bool = Field(default=True, description="Whether this is a manual override metric")


class SemanticViewLayer(BaseModel):
    """Layer 2: Native Semantic View definition.
    
    Projects materialized Dynamic Table logic into business-consumable
    dimensions, facts, and metrics.
    """
    view_name: str = Field(..., description="Semantic View name (e.g., marketing_optout_analytics_sv)")
    source_table: str = Field(default="", description="Source table (Dynamic Table from Layer 1)")
    table_alias: str = Field(default="events", description="SQL alias for the source table")
    dimensions: List[SemanticDimension] = Field(default_factory=list)
    facts: List[SemanticFact] = Field(default_factory=list)
    metrics: List[SemanticMetricSpec] = Field(default_factory=list)
    description: str = Field(default="", description="View description")


# ---------------------------------------------------------------------------
# Layer 3: Cortex Analyst Metadata Extension
# ---------------------------------------------------------------------------

class CortexSynonym(BaseModel):
    """Synonym mapping for Cortex Analyst natural language queries."""
    column_name: str = Field(..., description="Column or metric name in the Semantic View")
    synonyms: List[str] = Field(default_factory=list, description="List of natural language synonyms")
    description: str = Field(default="", description="Contextual description for AI interpretation")
    sample_values: List[str] = Field(default_factory=list, description="Sample values for context")


class CortexMetadataLayer(BaseModel):
    """Layer 3: Cortex Analyst metadata extension.
    
    Provides synonyms, descriptions, and sample values to enable
    accurate natural language to SQL translation by Cortex Analyst.
    """
    table_name: str = Field(default="", description="Logical table name in the Semantic View")
    dimension_synonyms: List[CortexSynonym] = Field(default_factory=list)
    metric_synonyms: List[CortexSynonym] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Override File Root Model
# ---------------------------------------------------------------------------

class OverrideStatus(str, Enum):
    """Status of an override file."""
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    DEPLOYED = "deployed"


class SQLOverrideFile(BaseModel):
    """Complete Manual SQL Override file for a Tier 3/4 DAX measure.
    
    This model represents the three-layer architecture required to safely
    translate hazardous DAX patterns into governed Snowflake artifacts.
    
    Structure:
        - metadata: Override context (tier, hazard, source DAX)
        - layer_1_dynamic_table: Pre-computation via Dynamic Table
        - layer_2_semantic_view: Business metric projection
        - layer_3_cortex_metadata: AI query metadata
    """
    
    # File metadata
    version: str = Field(default="1.0", description="Override file schema version")
    created_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="Creation timestamp",
    )
    updated_at: Optional[str] = Field(default=None, description="Last update timestamp")
    status: OverrideStatus = Field(default=OverrideStatus.DRAFT, description="Approval status")
    author: str = Field(default="semabridge-auto", description="Author of the override")
    
    # Source context
    metric_name: str = Field(..., description="Name of the overridden DAX measure")
    original_dax: str = Field(default="", description="Original DAX expression")
    safety_tier: int = Field(default=3, description="Safety tier (3 or 4)")
    hazard_category: str = Field(
        default=HazardCategory.NONE.value,
        description="Primary hazard category",
    )
    hazard_description: str = Field(default="", description="Detailed hazard explanation")
    
    # Source model context
    source_model: str = Field(default="", description="Source semantic model name")
    source_dataset: str = Field(default="", description="Source dataset/table name")
    
    # Override layers
    layer_1_dynamic_table: Optional[DynamicTableLayer] = Field(
        default=None,
        description="Layer 1: Materialized Dynamic Table for window processing",
    )
    layer_2_semantic_view: Optional[SemanticViewLayer] = Field(
        default=None,
        description="Layer 2: Native Semantic View definition",
    )
    layer_3_cortex_metadata: Optional[CortexMetadataLayer] = Field(
        default=None,
        description="Layer 3: Cortex Analyst metadata extension",
    )
    
    # Deployment metadata
    snowflake_database: str = Field(default="", description="Target Snowflake database")
    snowflake_schema: str = Field(default="", description="Target Snowflake schema")
    snowflake_warehouse: str = Field(default="", description="Compute warehouse for Dynamic Tables")

    def get_required_layers(self) -> List[OverrideLayerType]:
        """Determine which layers are required based on hazard category."""
        category = HazardCategory(self.hazard_category)
        from semabridge.converter.tiered_safety import _CATEGORY_OVERRIDE_LAYERS
        return _CATEGORY_OVERRIDE_LAYERS.get(category, [])

    def validate_completeness(self) -> List[str]:
        """Validate that all required layers are populated.
        
        Returns:
            List of validation error messages. Empty = valid.
        """
        errors: List[str] = []
        required = self.get_required_layers()
        
        if OverrideLayerType.DYNAMIC_TABLE in required and not self.layer_1_dynamic_table:
            errors.append(
                "Layer 1 (Dynamic Table) is required for hazard category "
                f"'{self.hazard_category}' but is not defined."
            )
        if OverrideLayerType.SEMANTIC_VIEW in required and not self.layer_2_semantic_view:
            errors.append(
                "Layer 2 (Semantic View) is required for hazard category "
                f"'{self.hazard_category}' but is not defined."
            )
        if OverrideLayerType.CORTEX_METADATA in required and not self.layer_3_cortex_metadata:
            errors.append(
                "Layer 3 (Cortex Metadata) is required for hazard category "
                f"'{self.hazard_category}' but is not defined."
            )
        
        # Validate layer internal consistency
        if self.layer_1_dynamic_table:
            if not self.layer_1_dynamic_table.source_table:
                errors.append("Layer 1: source_table is required.")
            if not self.layer_1_dynamic_table.table_name:
                errors.append("Layer 1: table_name is required.")
        
        if self.layer_2_semantic_view:
            if not self.layer_2_semantic_view.view_name:
                errors.append("Layer 2: view_name is required.")
            if not self.layer_2_semantic_view.metrics:
                errors.append("Layer 2: at least one metric must be defined.")
        
        return errors
