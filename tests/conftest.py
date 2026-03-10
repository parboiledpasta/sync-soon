"""
Pytest fixtures for semabridge tests.

Provides reusable test data and mock objects for unit testing.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, AggregationType, Cardinality
)


# -----------------------------------------------------------------------------
# SML Model Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def sample_column():
    """Create a sample SML column."""
    return SMLColumn(
        unique_name="REVENUE",
        label="Revenue",
        data_type=DataType.DECIMAL,
        description="Total revenue amount",
        is_key=False
    )


@pytest.fixture
def sample_dataset(sample_column):
    """Create a sample SML dataset with columns."""
    return SMLDataset(
        unique_name="FACT_SALES",
        label="Sales Fact Table",
        description="Contains sales transactions",
        source_table="FACT_SALES",
        is_fact=True,
        columns=[
            sample_column,
            SMLColumn(unique_name="CUSTOMER_ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="PRODUCT_ID", data_type=DataType.INTEGER),
            SMLColumn(unique_name="ORDER_DATE", data_type=DataType.DATE),
        ]
    )


@pytest.fixture
def sample_dimension_dataset():
    """Create a sample dimension dataset."""
    return SMLDataset(
        unique_name="DIM_CUSTOMER",
        label="Customer Dimension",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="NAME", data_type=DataType.STRING),
            SMLColumn(unique_name="CITY", data_type=DataType.STRING),
        ]
    )


@pytest.fixture
def sample_metric():
    """Create a sample SML metric."""
    return SMLMetric(
        unique_name="Total Revenue",
        label="Total Revenue",
        dataset="FACT_SALES",
        source_column="REVENUE",
        aggregation=AggregationType.SUM,
        description="Sum of all revenue"
    )


@pytest.fixture
def sample_relationship():
    """Create a sample SML relationship."""
    return SMLRelationship(
        unique_name="Sales_to_Customer",
        from_dataset="FACT_SALES",
        from_columns=["CUSTOMER_ID"],
        to_dataset="DIM_CUSTOMER",
        to_columns=["ID"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True
    )


@pytest.fixture
def sample_sml_model(sample_dataset, sample_dimension_dataset, sample_metric, sample_relationship):
    """Create a complete sample SML model."""
    return SMLModel(
        unique_name="test_model",
        label="Test Semantic Model",
        description="A test model for unit testing",
        datasets=[sample_dataset, sample_dimension_dataset],
        metrics=[sample_metric],
        relationships=[sample_relationship]
    )


# -----------------------------------------------------------------------------
# TMSL Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def sample_tmsl_json():
    """Create a sample TMSL JSON structure (Fabric model.bim format)."""
    return {
        "model": {
            "name": "SalesModel",
            "description": "Sales analytics model",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "Revenue", "dataType": "double"},
                        {"name": "Quantity", "dataType": "int64"},
                        {"name": "CustomerID", "dataType": "int64"},
                    ],
                    "measures": [
                        {
                            "name": "Total Revenue",
                            "expression": "SUM([Revenue])",
                            "description": "Sum of revenue"
                        }
                    ]
                },
                {
                    "name": "Customer",
                    "columns": [
                        {"name": "ID", "dataType": "int64"},
                        {"name": "Name", "dataType": "string"},
                    ]
                }
            ],
            "relationships": [
                {
                    "name": "Sales_Customer",
                    "fromTable": "Sales",
                    "fromColumn": "CustomerID",
                    "toTable": "Customer",
                    "toColumn": "ID",
                    "cardinality": "ManyToOne",
                    "isActive": True
                }
            ]
        }
    }


# -----------------------------------------------------------------------------
# DuckDB Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def temp_db_path():
    """Create a temporary database file path.
    
    Note: We don't pre-create the file to avoid Windows file locking issues.
    DuckDB will create it on first connection.
    """
    import uuid
    import tempfile
    # Generate unique path without creating/opening the file
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, f"semabridge_test_{uuid.uuid4().hex}.db")
    yield db_path
    # Cleanup after test (may fail if still open, that's ok)
    try:
        # Give DuckDB time to release the file
        import time
        time.sleep(0.1)
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Also try to remove WAL and other temp files
        for ext in ['.wal', '.tmp']:
            if os.path.exists(db_path + ext):
                os.unlink(db_path + ext)
    except OSError:
        pass  # File cleanup is best-effort


@pytest.fixture
def duckdb_manager(temp_db_path):
    """Create a DuckDBManager with a temporary database."""
    from semabridge.repository.duckdb_manager import DuckDBManager
    manager = DuckDBManager(db_path=temp_db_path)
    yield manager
    # Cleanup: close any open connections by deleting the manager
    del manager


# -----------------------------------------------------------------------------
# DAX Translation Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def dax_expressions():
    """Sample DAX expressions for testing translation."""
    return {
        # Tier 1: Direct aggregations
        "tier1_sum": "SUM([Revenue])",
        "tier1_avg": "AVERAGE([Quantity])",
        "tier1_count": "COUNT([OrderID])",
        "tier1_distinctcount": "DISTINCTCOUNT([CustomerID])",
        "tier1_min": "MIN([Price])",
        "tier1_max": "MAX([Price])",
        "tier1_with_table": "SUM('Sales'[Revenue])",
        
        # Tier 2: CALCULATE (partial support)
        "tier2_calculate": "CALCULATE(SUM([Revenue]), Product[Color] = \"Red\")",
        
        # Tier 3: Complex (no translation)
        "tier3_time_intel": "TOTALYTD(SUM([Revenue]), 'Date'[Date])",
        "tier3_iterator": "SUMX(Sales, Sales[Quantity] * Sales[Price])",
    }
