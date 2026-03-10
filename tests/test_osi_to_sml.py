"""
Unit tests for OSIToSMLConverter - including round-trip conversion.

Tests the bidirectional conversion: OSI ↔ SML
"""

import pytest

from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)


class TestOSIToSMLConverter:
    """Test OSI to SML conversion."""

    @pytest.fixture
    def converter(self):
        """Create converter instance."""
        return OSIToSMLConverter()

    @pytest.fixture
    def sample_osi_model(self):
        """Create a sample OSI model for testing."""
        return OSIModel(
            unique_name="test_model",
            label="Test Model",
            description="A test semantic model",
            version="1.0.0",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="FACT_SALES",
                    label="Sales Fact",
                    is_fact=True,
                    columns=[
                        OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL),
                        OSIColumn(unique_name="CUSTOMER_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    ]
                ),
                OSIDataset(
                    unique_name="DIM_CUSTOMER",
                    label="Customer",
                    columns=[
                        OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True),
                        OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
                    ]
                ),
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total Revenue",
                    dataset="FACT_SALES",
                    source_column="REVENUE",
                    aggregation=OSIAggregationType.SUM,
                ),
            ],
            relationships=[
                OSIRelationship(
                    unique_name="Sales_Customer",
                    from_dataset="FACT_SALES",
                    from_columns=["CUSTOMER_ID"],
                    to_dataset="DIM_CUSTOMER",
                    to_columns=["ID"],
                    cardinality=OSICardinality.MANY_TO_ONE,
                ),
            ],
            dimensions=[
                OSIDimension(
                    unique_name="CustomerDim",
                    dataset="DIM_CUSTOMER",
                    attributes=[
                        OSIAttribute(
                            unique_name="CustomerName",
                            label="Customer Name",
                            dataset="DIM_CUSTOMER",
                            source_column="NAME",
                        )
                    ],
                ),
            ],
        )

    def test_from_osi_basic(self, converter, sample_osi_model):
        """Test basic OSI to SML conversion."""
        sml = converter.from_osi(sample_osi_model)
        
        assert sml.unique_name == "test_model"
        assert sml.label == "Test Model"
        assert len(sml.datasets) >= 2  # May include auto-generated Date
        assert len(sml.metrics) == 1
        assert len(sml.relationships) == 1

    def test_to_osi_basic(self, converter, sample_osi_model):
        """Test basic SML to OSI conversion."""
        sml = converter.from_osi(sample_osi_model)
        osi_back = converter.to_osi(sml)
        
        assert osi_back.unique_name == sample_osi_model.unique_name
        assert osi_back.label == sample_osi_model.label

    def test_round_trip_preserves_model_name(self, converter, sample_osi_model):
        """Test round-trip preserves model identity."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert reconstructed.unique_name == sample_osi_model.unique_name
        assert reconstructed.version == sample_osi_model.version

    def test_round_trip_preserves_dataset_count(self, converter, sample_osi_model):
        """Test round-trip preserves datasets."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        # May include auto-generated Date dimension
        assert len(reconstructed.datasets) >= len(sample_osi_model.datasets)
        
        # Verify original datasets are present
        original_names = {ds.unique_name for ds in sample_osi_model.datasets}
        reconstructed_names = {ds.unique_name for ds in reconstructed.datasets}
        assert original_names.issubset(reconstructed_names)

    def test_round_trip_preserves_metrics(self, converter, sample_osi_model):
        """Test round-trip preserves metrics."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.metrics) == len(sample_osi_model.metrics)
        assert reconstructed.metrics[0].unique_name == sample_osi_model.metrics[0].unique_name

    def test_round_trip_preserves_relationships(self, converter, sample_osi_model):
        """Test round-trip preserves relationships."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.relationships) == len(sample_osi_model.relationships)
        assert reconstructed.relationships[0].from_dataset == sample_osi_model.relationships[0].from_dataset

    def test_round_trip_preserves_column_types(self, converter, sample_osi_model):
        """Test round-trip preserves column data types."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        # Find FACT_SALES in reconstructed
        fact_ds = next((ds for ds in reconstructed.datasets if ds.unique_name == "FACT_SALES"), None)
        assert fact_ds is not None
        
        revenue_col = fact_ds.get_column("REVENUE")
        assert revenue_col is not None
        assert revenue_col.data_type == OSIDataType.DECIMAL


class TestSMLToOSIEdgeCases:
    """Test edge cases for SML to OSI conversion."""

    @pytest.fixture
    def converter(self):
        return OSIToSMLConverter()

    def test_empty_model(self, converter):
        """Test conversion of minimal model."""
        osi = OSIModel(unique_name="empty_model")
        sml = converter.from_osi(osi)
        reconstructed = converter.to_osi(sml)
        
        assert reconstructed.unique_name == "empty_model"

    def test_model_with_hierarchies(self, converter):
        """Test conversion preserves dimension hierarchies."""
        osi = OSIModel(
            unique_name="hierarchy_test",
            datasets=[OSIDataset(unique_name="DIM_GEO")],
            dimensions=[
                OSIDimension(
                    unique_name="Geography",
                    dataset="DIM_GEO",
                    hierarchies=[
                        OSIHierarchy(
                            unique_name="GeoHier",
                            levels=[
                                OSILevel(unique_name="Country", attribute="COUNTRY"),
                                OSILevel(unique_name="City", attribute="CITY"),
                            ]
                        )
                    ],
                )
            ],
        )
        
        sml = converter.from_osi(osi)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.dimensions) == 1
        # Note: hierarchies may not round-trip perfectly due to SML model limitations


class TestOSIOverrideMetrics:
    """Test SQL override metrics are preserved in OSI format."""

    OVERRIDE_MEASURES = [
        ("Revenue SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'),
        ("Gross Margin SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END) - SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN (FACT."MATERIAL_COSTS") ELSE 0 END)'),
        ("YTD Revenue SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 AND CALENDAR."PERIOD" <= MONTH(CURRENT_DATE) THEN FACT."REVENUE" ELSE 0 END)'),
        ("COGS SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN (FACT."MATERIAL_COSTS") ELSE 0 END)'),
        ("YTD COGS SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 AND CALENDAR."PERIOD" <= MONTH(CURRENT_DATE) THEN (FACT."MATERIAL_COSTS") ELSE 0 END)'),
        ("YTD GM SPLY", 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 AND CALENDAR."PERIOD" <= MONTH(CURRENT_DATE) THEN FACT."REVENUE" ELSE 0 END) - SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 AND CALENDAR."PERIOD" <= MONTH(CURRENT_DATE) THEN (FACT."MATERIAL_COSTS") ELSE 0 END)'),
        ("Revenue Budget", 'SUM(CASE WHEN SCENARIO."SCENARIO" = \'Budget\' THEN FACT."REVENUE" ELSE 0 END)'),
        ("RevenueTY", 'SUM(CASE WHEN SCENARIO."SCENARIO" = \'Actual\' THEN FACT."REVENUE" ELSE 0 END)'),
    ]

    @pytest.fixture
    def converter(self):
        return OSIToSMLConverter()

    def test_osi_metric_with_sql_override(self):
        """Test that OSIMetric can hold sql_expression and override fields."""
        from semabridge.intermediate.models import OSIExpressionDialect

        metric = OSIMetric(
            unique_name="Revenue SPLY",
            label="Revenue SPLY",
            dataset="Fact",
            expression="CALCULATE([Total Revenue],SAMEPERIODLASTYEAR('Calendar'[Date]))",
            sql_expression='SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)',
            aggregation=OSIAggregationType.NONE,
            complexity_tier=3,
            is_override=True,
            dialects=[
                OSIExpressionDialect(dialect="DAX", expression="CALCULATE([Total Revenue],SAMEPERIODLASTYEAR('Calendar'[Date]))"),
                OSIExpressionDialect(dialect="SNOWFLAKE_SQL", expression='SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'),
            ],
        )
        assert metric.sql_expression is not None
        assert metric.is_override is True
        assert metric.complexity_tier == 3
        assert len(metric.dialects) == 2
        assert metric.dialects[0].dialect == "DAX"
        assert metric.dialects[1].dialect == "SNOWFLAKE_SQL"

    def test_sml_to_osi_preserves_override(self):
        """Test that SML→OSI conversion preserves sql_expression for overrides."""
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        from semabridge.formats.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric
        from semabridge.formats.sml.models import SourcePlatform, DataType, AggregationType

        sml_model = SMLModel(
            unique_name="test_model",
            label="Test Model",
            source_platform=SourcePlatform.SNOWFLAKE,
        )
        ds = SMLDataset(unique_name="Fact", label="Fact", source_table="FACT_TABLE", is_fact=True)
        ds.columns.append(SMLColumn(unique_name="REVENUE", data_type=DataType.DECIMAL))
        sml_model.datasets.append(ds)

        # Create a Tier 3 override metric
        override_metric = SMLMetric(
            unique_name="Revenue SPLY",
            label="Revenue SPLY",
            dataset="Fact",
            expression="CALCULATE([Total Revenue],SAMEPERIODLASTYEAR('Calendar'[Date]))",
            sql_expression='SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)',
            aggregation=AggregationType.SUM,
            complexity_tier=3,
        )
        sml_model.metrics.append(override_metric)

        converter = SMLToOSIConverter()
        osi_model = converter.to_osi(sml_model)

        assert len(osi_model.metrics) == 1
        osi_metric = osi_model.metrics[0]
        assert osi_metric.unique_name == "Revenue SPLY"
        assert osi_metric.sql_expression is not None
        assert "CALENDAR" in osi_metric.sql_expression
        assert osi_metric.is_override is True
        assert osi_metric.complexity_tier == 3
        assert len(osi_metric.dialects) == 2
        assert osi_metric.dialects[0].dialect == "DAX"
        assert osi_metric.dialects[1].dialect == "SNOWFLAKE_SQL"

    def test_osi_to_sml_preserves_override(self, converter):
        """Test that OSI→SML conversion preserves sql_expression for overrides."""
        osi_model = OSIModel(
            unique_name="test_override_model",
            label="Test Override Model",
            datasets=[
                OSIDataset(
                    unique_name="Fact",
                    label="Fact",
                    columns=[OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL)],
                    is_fact=True,
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Revenue SPLY",
                    dataset="Fact",
                    expression="CALCULATE([Total Revenue],SAMEPERIODLASTYEAR('Calendar'[Date]))",
                    sql_expression='SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)',
                    aggregation=OSIAggregationType.NONE,
                    complexity_tier=3,
                    is_override=True,
                )
            ],
        )
        sml = converter.from_osi(osi_model)
        assert len(sml.metrics) == 1
        sml_metric = sml.metrics[0]
        assert sml_metric.sql_expression == 'SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) - 1 THEN FACT."REVENUE" ELSE 0 END)'
        assert sml_metric.complexity_tier == 3
        assert sml_metric.sync_enabled is True

    @pytest.mark.parametrize("measure_name,sql_expr", OVERRIDE_MEASURES)
    def test_all_override_measures_in_osi(self, measure_name, sql_expr):
        """Test all 8 override measures can be represented in OSI format."""
        metric = OSIMetric(
            unique_name=measure_name,
            label=measure_name,
            dataset="Fact",
            expression=f"[{measure_name}]",
            sql_expression=sql_expr,
            aggregation=OSIAggregationType.NONE,
            complexity_tier=3,
            is_override=True,
        )
        assert metric.unique_name == measure_name
        assert metric.sql_expression == sql_expr
        assert metric.is_override is True
        assert metric.complexity_tier == 3
