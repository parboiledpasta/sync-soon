"""
OSI to SML Converter.

Converts OSI (Open Semantic Interchange) canonical models into the SML (Semantic Modeling Language)
intermediate representation, applying semantic enrichment like DAX translation.

Provides both:
    - A standalone ``convert_osi_to_sml()`` function for direct use
    - The ``OSIToSMLConverter`` class that implements ``BaseConverter``
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIDimension,
    OSIRelationship,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.formats.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    SMLDimension,
    SMLAttribute,
    SMLHierarchy,
    SMLLevel,
    DataType as SMLDataType,
    AggregationType as SMLAggregationType,
    Cardinality as SMLCardinality,
    CrossFilterDirection as SMLCrossFilterDirection,
    SourcePlatform,
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# Standalone function — preferred entry point for callers
# =============================================================================


def convert_osi_to_sml(
    osi_model: OSIModel,
    *,
    llm_config: Any = False,
) -> SMLModel:
    """
    Convert an OSI model to SML in a single function call.

    This is a pure, stateless convenience wrapper around ``OSIToSMLConverter``.
    It is the **recommended entry point** for all callers that need to turn an
    ``OSIModel`` into an ``SMLModel`` without instantiating the converter class
    directly.

    Args:
        osi_model: The OSI semantic model to convert.
        llm_config: LLM configuration forwarded to DAXTranslator.
                    Defaults to ``False`` (no LLM).  Pass an
                    ``LLMProjectConfig`` instance to enable Tier 4.

    Returns:
        Fully converted ``SMLModel`` with DAX-translated SQL expressions.

    Raises:
        ConversionError: If conversion fails.
    """
    converter = OSIToSMLConverter(llm_config=llm_config)
    return converter.from_osi(osi_model)


class OSIToSMLConverter(BaseConverter):
    """
    Transforms OSI Model into SML Model with semantic enrichment.
    """

    def __init__(self, llm_config=False):
        """
        Args:
            llm_config: Forwarded to ``DAXTranslator``.
                        ``False`` = no LLM, ``None`` = load from config.yaml,
                        or pass an ``LLMProjectConfig`` instance.
        """
        self.dax_translator = DAXTranslator(llm_config=llm_config)

    def to_osi(self, sml_model: SMLModel) -> OSIModel:
        """
        Convert SMLModel object to OSIModel object.
        
        Delegates to SMLToOSIConverter for the actual conversion.
        This method exists to satisfy the BaseConverter interface.
        
        Args:
            sml_model: SMLModel object
            
        Returns:
            OSIModel object
        """
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        return SMLToOSIConverter().to_osi(sml_model)


    def from_osi(self, osi_model: OSIModel) -> SMLModel:
        """
        Convert OSIModel object to SMLModel object.
        
        Args:
            osi_model: OSIModel object
            
        Returns:
            SMLModel object
        """
        try:
            sml = SMLModel(
                unique_name=osi_model.unique_name,
                label=osi_model.label,
                description=osi_model.description or "",
                source_system=osi_model.source_platform or "unknown",
                source_platform=SourcePlatform.FABRIC if osi_model.source_platform == "fabric" else SourcePlatform.SNOWFLAKE,
                version=osi_model.version
            )

            # 1. Convert Datasets
            for osi_ds in osi_model.datasets:
                sml.datasets.append(self._convert_dataset(osi_ds))

            # 2. Convert Dimensions
            for osi_dim in osi_model.dimensions:
                sml.dimensions.append(self._convert_dimension(osi_dim))

            # 3. Convert Metrics (with DAX Translation)
            # Create metric context for dependency resolution if needed
            # For now, just iterate
            for osi_metric in osi_model.metrics:
                sml.metrics.append(self._convert_metric(osi_metric))

            # 4. Convert Relationships
            for osi_rel in osi_model.relationships:
                sml_rel = self._convert_relationship(osi_rel)
                if sml_rel:
                    sml.relationships.append(sml_rel)

            # 5. Inject Calendar Dimension if missing (SML Requirement for Cortex)
            self._inject_calendar_dimension(sml)

            # 6. Mandate 4: Validate PK integrity across relationships
            self._validate_pk_integrity(sml)

            return sml

        except Exception as e:
            logger.error(f"OSI to SML conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert OSI to SML: {e}",
                source_format="osi",
                target_format="sml",
                details={"error": str(e)}
            )

    def _convert_dataset(self, osi_ds: OSIDataset) -> SMLDataset:
        columns = [self._convert_column(c) for c in osi_ds.columns]
        
        return SMLDataset(
            unique_name=osi_ds.unique_name,
            label=osi_ds.label,
            description=osi_ds.description or "",
            source_table=osi_ds.source_table,
            source_schema=osi_ds.source_schema or "",
            columns=columns,
            is_hidden=osi_ds.is_hidden,
            is_fact=osi_ds.is_fact,
        )

    def _convert_column(self, osi_col: OSIColumn) -> SMLColumn:
        # Dynamic lookup using name matching (e.g. INTEGER -> INTEGER)
        sml_type = getattr(SMLDataType, osi_col.data_type.name, SMLDataType.STRING)

        # Mandate 3: Detect calculated / computed columns
        is_calc = bool(osi_col.source_expression)
        calc_sql: str | None = None
        if is_calc and osi_col.source_expression:
            # source_expression already holds the SQL form (from DDL extraction)
            calc_sql = osi_col.source_expression

        return SMLColumn(
            unique_name=osi_col.unique_name,
            label=osi_col.label,
            data_type=sml_type,
            description=osi_col.description or "",
            is_hidden=osi_col.is_hidden,
            is_key=osi_col.is_key,
            format_string=osi_col.format_string,
            source_expression=osi_col.source_expression,
            is_calculated=is_calc,
            calculated_sql=calc_sql,
        )

    def _convert_dimension(self, osi_dim: OSIDimension) -> SMLDimension:
        attributes = []
        for attr in osi_dim.attributes:
            attributes.append(SMLAttribute(
                unique_name=attr.unique_name,
                label=attr.label,
                dataset=attr.dataset,
                dataset_column=attr.source_column,
                is_hidden=attr.is_hidden
            ))
            
        return SMLDimension(
            unique_name=osi_dim.unique_name,
            label=osi_dim.label,
            description=osi_dim.description or "",
            dataset=osi_dim.dataset,
            attributes=attributes,
            is_hidden=osi_dim.is_hidden
        )

    def _convert_metric(self, osi_metric: OSIMetric) -> SMLMetric:
        # DAX Translation Logic
        expression = osi_metric.expression or ""
        
        # Analyze Complexity
        complexity = self.dax_translator.analyze_complexity(expression)
        
        sml_agg = getattr(SMLAggregationType, osi_metric.aggregation.name, SMLAggregationType.SUM)
        
        metric = SMLMetric(
            unique_name=osi_metric.unique_name,
            label=osi_metric.label,
            description=osi_metric.description or "",
            dataset=osi_metric.dataset,
            expression=expression,
            aggregation=sml_agg,
            format_string=osi_metric.format_string,
            is_hidden=osi_metric.is_hidden,
            # Sync Metadata
            complexity_tier=complexity["tier"],
            requires_time_intel=complexity["requires_time_intel"],
            group_by_dimensions=complexity["group_by_dimensions"],
            depends_on_measures=complexity["depends_on_measures"],
            sync_enabled=complexity["sync_enabled"],
            sync_failure_reason=complexity["failure_reason"],
            partition_dimension="'Date'[Year]" if complexity["requires_time_intel"] else None,
        )
        
        # If OSI metric already carries an SQL override expression, use it directly
        if osi_metric.sql_expression and osi_metric.is_override:
            metric.sql_expression = osi_metric.sql_expression
            metric.complexity_tier = osi_metric.complexity_tier
            metric.sync_enabled = True
            return metric
        
        # Attempt Translation
        if expression:
            safe_alias = "".join(c if c.isalnum() else "_" for c in osi_metric.dataset).upper()
            translation = self.dax_translator.translate(
                expression,
                safe_alias,
                osi_metric.dataset,
                metric_name=metric.unique_name
            )
            
            if translation.is_success:
                metric.sql_expression = translation.sql
                metric.complexity_tier = translation.tier
                metric.sync_enabled = True
            elif metric.sync_enabled:
                 metric.sync_enabled = False
                 metric.sync_failure_reason = f"DAX translation failed (Tier {translation.tier})"

        return metric

    def _convert_relationship(self, osi_rel: OSIRelationship) -> Optional[SMLRelationship]:
        try:
             sml_card = getattr(SMLCardinality, osi_rel.cardinality.name, SMLCardinality.MANY_TO_ONE)
             sml_cf = getattr(SMLCrossFilterDirection, osi_rel.cross_filter_direction.name, SMLCrossFilterDirection.SINGLE)

             return SMLRelationship(
                 unique_name=osi_rel.unique_name,
                 from_dataset=osi_rel.from_dataset,
                 from_columns=osi_rel.from_columns,
                 to_dataset=osi_rel.to_dataset,
                 to_columns=osi_rel.to_columns,
                 cardinality=sml_card,
                 cross_filter=sml_cf,
                 is_active=osi_rel.is_active
             )
        except Exception as e:
            logger.warning(f"Failed to convert relationship {osi_rel.unique_name}: {e}")
            return None

    def _validate_pk_integrity(self, sml: SMLModel) -> None:
        """Mandate 4: Validate that relationship PK references point to real columns.
        
        For each relationship, verify that ``to_columns`` exist in the target
        dataset's column list.  Missing columns are logged as warnings so that
        downstream semantic-view generation can decide whether to abort
        (strict) or fall back (permissive).
        """
        ds_lookup: dict[str, set[str]] = {}
        for ds in sml.datasets:
            ds_lookup[ds.unique_name.upper()] = {
                c.unique_name.upper() for c in ds.columns
            }

        for rel in sml.relationships:
            target_cols = ds_lookup.get(rel.to_dataset.upper(), set())
            if not target_cols:
                logger.warning(
                    f"Relationship '{rel.unique_name}': target dataset "
                    f"'{rel.to_dataset}' not found in model datasets."
                )
                continue
            for col in rel.to_columns:
                if col.upper() not in target_cols:
                    logger.warning(
                        f"Relationship '{rel.unique_name}': PK column "
                        f"'{col}' not found in dataset '{rel.to_dataset}' "
                        f"columns. Semantic view PK may fall back."
                    )

    def _inject_calendar_dimension(self, sml: SMLModel) -> None:
        # Same logic as TMSLTransformer
        if any("DATE" in ds.unique_name.upper() or "CALENDAR" in ds.unique_name.upper() for ds in sml.datasets):
            return
            
        # Create standard Date column definitions
        cols = [
            SMLColumn(unique_name="Date", data_type=SMLDataType.DATE, is_key=True),
            SMLColumn(unique_name="Year", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="Quarter", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="Month", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="MonthName", data_type=SMLDataType.STRING),
            SMLColumn(unique_name="DayOfWeek", data_type=SMLDataType.INTEGER),
            SMLColumn(unique_name="DayName", data_type=SMLDataType.STRING),
        ]
        
        date_ds = SMLDataset(
            unique_name="Date",
            label="Date",
            description="Auto-generated Calendar Dimension",
            source_table="DIM_DATE",
            columns=cols,
            is_fact=False
        )
        sml.datasets.append(date_ds)

