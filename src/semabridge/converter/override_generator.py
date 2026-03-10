"""
SQL Override File Generator.

Generates Manual SQL Override files for Tier 3 and Tier 4 DAX measures.
Produces both YAML configuration files and executable Snowflake SQL DDL
across the three-layer architecture:

    Layer 1 — Dynamic Table DDL (materialized pre-computation)
    Layer 2 — Semantic View DDL (business metric projection)
    Layer 3 — Cortex Analyst Metadata Extension (ALTER SEMANTIC VIEW)
"""

from __future__ import annotations

import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.converter.tiered_safety import (
    HazardCategory,
    OverrideLayerType,
    SafetyClassification,
    SafetyTier,
    TieredSafetyClassifier,
)
from semabridge.converter.override_schema import (
    CortexMetadataLayer,
    CortexSynonym,
    DynamicTableLayer,
    OverrideStatus,
    SemanticDimension,
    SemanticFact,
    SemanticMetricSpec,
    SemanticViewLayer,
    SQLOverrideFile,
    WindowFunctionSpec,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# Custom YAML Dumper for block-style strings
class _OverrideDumper(yaml.SafeDumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

def _str_presenter(dumper, data):
    if "\n" in data or len(data) > 80:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)

_OverrideDumper.add_representer(str, _str_presenter)


class OverrideGenerator:
    """Generates Manual SQL Override files for hazardous DAX measures.

    This generator creates:
    1. YAML override specification files
    2. Executable Snowflake SQL DDL scripts
    3. Cortex Analyst metadata extensions

    Usage::

        generator = OverrideGenerator(
            database="ANALYTICS_DB",
            schema="SEMANTIC",
            warehouse="COMPUTE_WH",
        )
        override = generator.generate_override(classification, source_model="MyModel")
        generator.write_override_yaml(override, output_dir)
        sql = generator.render_sql(override)
    """

    def __init__(
        self,
        database: str = "ANALYTICS_DB",
        schema: str = "SEMANTIC",
        warehouse: str = "ANALYTICS_COMPUTE_WH",
    ):
        self.database = database
        self.schema = schema
        self.warehouse = warehouse

    def generate_override(
        self,
        classification: SafetyClassification,
        source_model: str = "",
        source_dataset: str = "",
    ) -> SQLOverrideFile:
        """Generate a complete SQL Override file from a safety classification.

        Args:
            classification: The SafetyClassification result from TieredSafetyClassifier.
            source_model: Name of the source semantic model.
            source_dataset: Name of the source dataset/table.

        Returns:
            A fully populated SQLOverrideFile with template layers.
        """
        if not classification.requires_override:
            logger.warning(
                f"Override not required for '{classification.metric_name}' "
                f"(Tier {classification.tier.value}). Generating anyway."
            )

        metric_safe = self._sanitize_name(classification.metric_name)
        primary_hazard = classification.primary_hazard

        # Build Layer 1: Dynamic Table
        layer_1 = self._build_dynamic_table_layer(
            metric_safe, primary_hazard, classification, source_dataset
        )

        # Build Layer 2: Semantic View
        layer_2 = self._build_semantic_view_layer(
            metric_safe, primary_hazard, classification, layer_1
        )

        # Build Layer 3: Cortex Metadata
        layer_3 = self._build_cortex_metadata_layer(
            metric_safe, classification, layer_2
        )

        override = SQLOverrideFile(
            metric_name=classification.metric_name,
            original_dax=classification.original_dax,
            safety_tier=classification.tier.value,
            hazard_category=primary_hazard.value,
            hazard_description=(
                classification.hazards[0].description
                if classification.hazards
                else ""
            ),
            source_model=source_model,
            source_dataset=source_dataset,
            layer_1_dynamic_table=layer_1,
            layer_2_semantic_view=layer_2,
            layer_3_cortex_metadata=layer_3,
            snowflake_database=self.database,
            snowflake_schema=self.schema,
            snowflake_warehouse=self.warehouse,
        )

        return override

    def generate_batch_overrides(
        self,
        classifications: Dict[str, SafetyClassification],
        source_model: str = "",
        source_dataset: str = "",
    ) -> Dict[str, SQLOverrideFile]:
        """Generate override files for all measures requiring overrides.

        Args:
            classifications: Dict of metric_name -> SafetyClassification.
            source_model: Source model name.
            source_dataset: Source dataset name.

        Returns:
            Dict of metric_name -> SQLOverrideFile (only for Tier 3/4).
        """
        overrides: Dict[str, SQLOverrideFile] = {}
        for name, classification in classifications.items():
            if classification.requires_override:
                overrides[name] = self.generate_override(
                    classification, source_model, source_dataset
                )
        logger.info(f"Generated {len(overrides)} override files from batch.")
        return overrides

    # =========================================================================
    # YAML Output
    # =========================================================================

    def write_override_yaml(
        self,
        override: SQLOverrideFile,
        output_dir: Path,
    ) -> Path:
        """Write an override file as YAML to the specified directory.

        Args:
            override: The SQLOverrideFile to serialize.
            output_dir: Directory to write the YAML file.

        Returns:
            Path to the written YAML file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"override_{self._sanitize_name(override.metric_name)}.yaml"
        filepath = output_dir / filename

        data = override.model_dump(mode="json", exclude_none=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# SemaBridge Manual SQL Override File\n")
            f.write(f"# Metric: {override.metric_name}\n")
            f.write(f"# Safety Tier: {override.safety_tier}\n")
            f.write(f"# Hazard: {override.hazard_category}\n")
            f.write(f"# Generated: {override.created_at}\n")
            f.write(f"# Status: {override.status.value}\n")
            f.write("---\n")
            yaml.dump(
                data,
                f,
                Dumper=_OverrideDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        logger.info(f"Override YAML written: {filepath}")
        return filepath

    def write_batch_yaml(
        self,
        overrides: Dict[str, SQLOverrideFile],
        output_dir: Path,
    ) -> List[Path]:
        """Write multiple override files to a directory.

        Returns:
            List of paths to written files.
        """
        paths: List[Path] = []
        for name, override in overrides.items():
            path = self.write_override_yaml(override, output_dir)
            paths.append(path)
        return paths

    # =========================================================================
    # SQL Rendering
    # =========================================================================

    def render_sql(self, override: SQLOverrideFile) -> str:
        """Render the complete SQL DDL for an override file.

        Produces a single SQL script containing all three layers:
        1. CREATE OR REPLACE DYNAMIC TABLE
        2. CREATE OR REPLACE SEMANTIC VIEW
        3. ALTER SEMANTIC VIEW (Cortex Analyst metadata)

        Returns:
            Complete SQL DDL string.
        """
        sections: List[str] = []
        db = override.snowflake_database or self.database
        schema = override.snowflake_schema or self.schema

        # Header
        sections.append(self._render_header(override))

        # Layer 1: Dynamic Table
        if override.layer_1_dynamic_table:
            sections.append(self._render_dynamic_table(override.layer_1_dynamic_table, db, schema))

        # Layer 2: Semantic View
        if override.layer_2_semantic_view:
            sections.append(self._render_semantic_view(override.layer_2_semantic_view, db, schema))

        # Layer 3: Cortex Metadata
        if override.layer_3_cortex_metadata and override.layer_2_semantic_view:
            sections.append(
                self._render_cortex_metadata(
                    override.layer_3_cortex_metadata,
                    override.layer_2_semantic_view.view_name,
                    db,
                    schema,
                )
            )

        return "\n\n".join(sections)

    def write_sql(
        self,
        override: SQLOverrideFile,
        output_dir: Path,
    ) -> Path:
        """Write rendered SQL to a file.

        Returns:
            Path to the written SQL file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"override_{self._sanitize_name(override.metric_name)}.sql"
        filepath = output_dir / filename

        sql = self.render_sql(override)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(sql)

        logger.info(f"Override SQL written: {filepath}")
        return filepath

    # =========================================================================
    # Private Layer Builders
    # =========================================================================

    def _build_dynamic_table_layer(
        self,
        metric_safe: str,
        hazard: HazardCategory,
        classification: SafetyClassification,
        source_dataset: str,
    ) -> DynamicTableLayer:
        """Build a template Dynamic Table layer based on hazard category."""
        table_name = f"{metric_safe}_dt"
        source = source_dataset or "source_table"

        # Default window functions based on hazard category
        window_functions: List[WindowFunctionSpec] = []
        filter_conditions: List[str] = []
        select_columns: List[str] = ["*"]
        description = ""

        if hazard == HazardCategory.SPECIALIZED_BUILTIN_LOGIC:
            # RANKX / EARLIER — needs RANK() window function
            window_functions.append(
                WindowFunctionSpec(
                    function="RANK",
                    partition_by=["-- TODO: specify partition columns (e.g., user_id, type_id)"],
                    order_by=["-- TODO: specify ordering column (e.g., date_received ASC)"],
                    order_direction="ASC",
                    alias=f"{metric_safe}_rank",
                    filter_condition="-- TODO: add optional CASE WHEN date filter",
                )
            )
            description = (
                "Converts nested loop requirements of EARLIER/RANKX into "
                "set-based analytical partition using RANK() OVER (...)."
            )

        elif hazard == HazardCategory.ROW_CONTEXT_ITERATOR:
            # SUMX / AVERAGEX — pre-aggregate row-level computation
            select_columns = [
                "-- TODO: specify dimension columns",
                "-- TODO: specify computed expression (e.g., price * qty)",
            ]
            description = (
                "Pre-computes row-level iterator logic (SUMX/AVERAGEX) "
                "as a standard SQL column expression."
            )

        elif hazard == HazardCategory.COMPLEX_FILTER_CONTEXT:
            # CALCULATE + FILTER — pre-compute filtered aggregation
            filter_conditions = [
                "-- TODO: translate DAX FILTER conditions to SQL WHERE clause",
            ]
            description = (
                "Materializes the complex filter context into a standard "
                "WHERE-clause-based subset."
            )

        elif hazard == HazardCategory.ADVANCED_TIME_INTELLIGENCE:
            # Time intelligence — calendar-joined self-referencing
            select_columns = [
                "src.*",
                "cal.prior_year_date",
                "cal.prior_month_date",
                "-- TODO: add calendar offset columns as needed",
            ]
            description = (
                "Joins fact data to materialized calendar dimension with "
                "temporal offset columns for time-shifted calculations."
            )

        elif hazard == HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER:
            description = (
                "Creates aliased table references to replace dynamic "
                "USERELATIONSHIP/CROSSFILTER with static join paths."
            )

        return DynamicTableLayer(
            table_name=table_name,
            target_lag="1 hour",
            warehouse=self.warehouse,
            source_table=source,
            select_columns=select_columns,
            window_functions=window_functions,
            filter_conditions=filter_conditions,
            description=description,
        )

    def _build_semantic_view_layer(
        self,
        metric_safe: str,
        hazard: HazardCategory,
        classification: SafetyClassification,
        layer_1: DynamicTableLayer,
    ) -> SemanticViewLayer:
        """Build a template Semantic View layer."""
        view_name = f"{metric_safe}_sv"
        source_table = layer_1.table_name

        # Template metric
        override_metric = SemanticMetricSpec(
            name=metric_safe,
            expression="-- TODO: define the aggregate SQL expression",
            description=f"Manual override for: {classification.metric_name}",
            is_override=True,
        )

        # Template dimension and fact
        dimensions = [
            SemanticDimension(
                name="-- TODO: dimension_name",
                source_column=f"events.-- TODO: column",
                description="-- TODO: describe this dimension",
            ),
        ]
        facts = [
            SemanticFact(
                name="-- TODO: fact_name",
                source_column=f"events.-- TODO: column",
                description="-- TODO: describe this fact",
            ),
        ]

        return SemanticViewLayer(
            view_name=view_name,
            source_table=source_table,
            table_alias="events",
            dimensions=dimensions,
            facts=facts,
            metrics=[override_metric],
            description=f"Semantic View for overridden metric: {classification.metric_name}",
        )

    def _build_cortex_metadata_layer(
        self,
        metric_safe: str,
        classification: SafetyClassification,
        layer_2: SemanticViewLayer,
    ) -> CortexMetadataLayer:
        """Build a template Cortex Analyst metadata layer."""
        metric_synonyms = [
            CortexSynonym(
                column_name=metric_safe,
                synonyms=["-- TODO: add natural language synonyms"],
                description=(
                    f"Manual override metric for: {classification.metric_name}. "
                    f"TODO: provide a business description."
                ),
                sample_values=[],
            ),
        ]

        return CortexMetadataLayer(
            table_name=layer_2.table_alias,
            metric_synonyms=metric_synonyms,
        )

    # =========================================================================
    # Private SQL Renderers
    # =========================================================================

    def _render_header(self, override: SQLOverrideFile) -> str:
        """Render the SQL file header comment block."""
        return (
            f"-- ==============================================================================\n"
            f"-- SEMABRIDGE MANUAL SQL OVERRIDE FILE: {override.metric_name.upper()}\n"
            f"-- TYPE: TIER {override.safety_tier} HAZARD ({override.hazard_category.upper()})\n"
            f"-- GENERATED: {override.created_at}\n"
            f"-- STATUS: {override.status.value.upper()}\n"
            f"-- ==============================================================================\n"
            f"-- Original DAX:\n"
            f"-- {override.original_dax}\n"
            f"-- ==============================================================================\n"
            f"-- Hazard Description:\n"
            f"-- {override.hazard_description}\n"
            f"-- =============================================================================="
        )

    def _render_dynamic_table(
        self,
        layer: DynamicTableLayer,
        database: str,
        schema: str,
    ) -> str:
        """Render Layer 1: CREATE OR REPLACE DYNAMIC TABLE DDL."""
        fq_name = f"{database}.{schema}.{layer.table_name}"
        
        lines = [
            f"-- LAYER 1: MATERIALIZED DYNAMIC TABLE FOR WINDOW PROCESSING",
            f"-- {layer.description}",
            f"CREATE OR REPLACE DYNAMIC TABLE {fq_name}",
            f"  TARGET_LAG = '{layer.target_lag}'",
            f"  WAREHOUSE = '{layer.warehouse}'",
            f"AS",
            f"SELECT",
        ]

        # SELECT columns
        select_parts: List[str] = []
        for col in layer.select_columns:
            select_parts.append(f"    {col}")

        # Window function columns
        for wf in layer.window_functions:
            partition = ", ".join(wf.partition_by) if wf.partition_by else "-- TODO"
            order = ", ".join(wf.order_by) if wf.order_by else "-- TODO"

            if wf.filter_condition:
                wf_expr = (
                    f"    CASE\n"
                    f"        WHEN {wf.filter_condition} THEN\n"
                    f"            {wf.function}() OVER (\n"
                    f"                PARTITION BY {partition}\n"
                    f"                ORDER BY {order} {wf.order_direction}\n"
                    f"            )\n"
                    f"        ELSE NULL\n"
                    f"    END AS {wf.alias}"
                )
            else:
                wf_expr = (
                    f"    {wf.function}() OVER (\n"
                    f"        PARTITION BY {partition}\n"
                    f"        ORDER BY {order} {wf.order_direction}\n"
                    f"    ) AS {wf.alias}"
                )
            select_parts.append(wf_expr)

        lines.append(",\n".join(select_parts))

        # FROM
        lines.append(f"FROM\n    {layer.source_table}")

        # WHERE
        if layer.filter_conditions:
            conditions = "\n    AND ".join(layer.filter_conditions)
            lines.append(f"WHERE\n    {conditions}")

        lines.append(";")
        return "\n".join(lines)

    def _render_semantic_view(
        self,
        layer: SemanticViewLayer,
        database: str,
        schema: str,
    ) -> str:
        """Render Layer 2: CREATE OR REPLACE SEMANTIC VIEW DDL."""
        fq_name = f"{database}.{schema}.{layer.view_name}"
        source_fq = f"{database}.{schema}.{layer.source_table}" if layer.source_table else "-- TODO: source table"

        lines = [
            f"-- LAYER 2: NATIVE SEMANTIC VIEW DEFINITION",
            f"-- {layer.description}",
            f"CREATE OR REPLACE SEMANTIC VIEW {fq_name}",
            f"    TABLES (",
            f"        {layer.table_alias} AS {source_fq}",
            f"    )",
        ]

        # DIMENSIONS
        if layer.dimensions:
            dim_lines = []
            for dim in layer.dimensions:
                dim_lines.append(f"        {dim.source_column} AS {dim.name}")
            lines.append(f"    DIMENSIONS (")
            lines.append(",\n".join(dim_lines))
            lines.append(f"    )")

        # FACTS
        if layer.facts:
            fact_lines = []
            for fact in layer.facts:
                fact_lines.append(f"        {fact.source_column} AS {fact.name}")
            lines.append(f"    FACTS (")
            lines.append(",\n".join(fact_lines))
            lines.append(f"    )")

        # METRICS
        if layer.metrics:
            metric_lines = []
            for metric in layer.metrics:
                comment = f"  -- Override" if metric.is_override else ""
                metric_lines.append(
                    f"        {metric.name} AS {metric.expression}{comment}"
                )
            lines.append(f"    METRICS (")
            lines.append(",\n".join(metric_lines))
            lines.append(f"    )")

        lines.append(";")
        return "\n".join(lines)

    def _render_cortex_metadata(
        self,
        layer: CortexMetadataLayer,
        view_name: str,
        database: str,
        schema: str,
    ) -> str:
        """Render Layer 3: ALTER SEMANTIC VIEW for Cortex Analyst metadata."""
        fq_view = f"{database}.{schema}.{view_name}"

        # Build the JSON metadata structure
        dimensions_json: List[Dict[str, Any]] = []
        for syn in layer.dimension_synonyms:
            dim_entry: Dict[str, Any] = {
                "name": syn.column_name,
                "synonyms": syn.synonyms,
                "description": syn.description,
            }
            if syn.sample_values:
                dim_entry["sample_values"] = syn.sample_values
            dimensions_json.append(dim_entry)

        metrics_json: List[Dict[str, Any]] = []
        for syn in layer.metric_synonyms:
            metrics_json.append({
                "name": syn.column_name,
                "synonyms": syn.synonyms,
                "description": syn.description,
            })

        import json

        ca_payload = {
            "tables": [{
                "name": layer.table_name,
                "dimensions": dimensions_json,
                "metrics": metrics_json,
            }],
        }

        ca_json = json.dumps(ca_payload, indent=6)

        lines = [
            f"-- LAYER 3: CORTEX ANALYST METADATA EXTENSION",
            f"-- Injects synonyms and contextual understanding for natural language queries",
            f"ALTER SEMANTIC VIEW {fq_view} SET EXTENSION",
            f"  (CA='{ca_json}');",
        ]

        return "\n".join(lines)

    # =========================================================================
    # Utilities
    # =========================================================================

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a metric name for use in SQL identifiers."""
        clean = re.sub(r"[^a-zA-Z0-9]", "_", name)
        clean = re.sub(r"_+", "_", clean).strip("_").lower()
        return clean
