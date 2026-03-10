"""
OSI to SQL Converter.

Generates SQL DDL/DML from OSI (Open Semantic Interchange) models,
enabling direct deployment to target databases without an intermediate
SML step.

Supported dialects:
    - ``snowflake``  — Snowflake Semantic Views + Cortex Analyst YAML
    - ``ansi``       — Portable ANSI SQL (CREATE VIEW)

Architecture:
    This module sits alongside the existing ``osi_to_sml`` converter
    and provides an alternative **emission** path:

        Source → OSI → SQL   (this module)
        Source → OSI → SML   (existing osi_to_sml)

    The two paths share the same ``OSIModel`` input, ensuring a single
    canonical representation is the source of truth.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDataType,
    OSIAggregationType,
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer

logger = get_logger(__name__)


# =============================================================================
# SQL Dialect Registry
# =============================================================================

# Maps OSI data types → SQL column types per dialect
_TYPE_MAP: Dict[str, Dict[OSIDataType, str]] = {
    "snowflake": {
        OSIDataType.STRING: "VARCHAR",
        OSIDataType.INTEGER: "NUMBER(38,0)",
        OSIDataType.DECIMAL: "NUMBER(38,6)",
        OSIDataType.FLOAT: "FLOAT",
        OSIDataType.BOOLEAN: "BOOLEAN",
        OSIDataType.DATE: "DATE",
        OSIDataType.DATETIME: "TIMESTAMP_NTZ",
        OSIDataType.TIME: "TIME",
        OSIDataType.BINARY: "BINARY",
        OSIDataType.VARIANT: "VARIANT",
        OSIDataType.UNKNOWN: "VARCHAR",
    },
    "ansi": {
        OSIDataType.STRING: "VARCHAR",
        OSIDataType.INTEGER: "INTEGER",
        OSIDataType.DECIMAL: "DECIMAL(38,6)",
        OSIDataType.FLOAT: "DOUBLE PRECISION",
        OSIDataType.BOOLEAN: "BOOLEAN",
        OSIDataType.DATE: "DATE",
        OSIDataType.DATETIME: "TIMESTAMP",
        OSIDataType.TIME: "TIME",
        OSIDataType.BINARY: "BLOB",
        OSIDataType.VARIANT: "VARCHAR",
        OSIDataType.UNKNOWN: "VARCHAR",
    },
}

# Maps OSI aggregation type → SQL function name
_AGG_MAP: Dict[OSIAggregationType, str] = {
    OSIAggregationType.SUM: "SUM",
    OSIAggregationType.COUNT: "COUNT",
    OSIAggregationType.COUNT_DISTINCT: "COUNT(DISTINCT {})",
    OSIAggregationType.AVG: "AVG",
    OSIAggregationType.MIN: "MIN",
    OSIAggregationType.MAX: "MAX",
    OSIAggregationType.NONE: "{}",
}


# =============================================================================
# Public API
# =============================================================================


class OSIToSQLResult:
    """Container for the SQL generation output."""

    def __init__(
        self,
        semantic_view_ddl: str,
        source_table_ddls: List[str],
        metric_expressions: Dict[str, str],
        dialect: str,
        warnings: List[str],
    ):
        self.semantic_view_ddl = semantic_view_ddl
        self.source_table_ddls = source_table_ddls
        self.metric_expressions = metric_expressions
        self.dialect = dialect
        self.warnings = warnings

    @property
    def all_ddl(self) -> str:
        """Return all DDL statements concatenated."""
        parts = list(self.source_table_ddls)
        if self.semantic_view_ddl:
            parts.append(self.semantic_view_ddl)
        return "\n\n".join(parts)

    def __repr__(self) -> str:
        return (
            f"OSIToSQLResult(dialect={self.dialect!r}, "
            f"tables={len(self.source_table_ddls)}, "
            f"metrics={len(self.metric_expressions)}, "
            f"warnings={len(self.warnings)})"
        )


def convert_osi_to_sql(
    osi_model: OSIModel,
    *,
    dialect: str = "snowflake",
    database: str = "ANALYTICS_DB",
    schema: str = "SEMANTIC_LAYER",
    include_source_tables: bool = True,
    llm_config: Any = False,
    idempotent_ddl: bool = True,
) -> OSIToSQLResult:
    """
    Convert an OSI model directly to SQL DDL.

    This is the primary entry point for OSI → SQL conversion.  It produces
    ``CREATE TABLE`` statements for source tables (optional) and a
    ``CREATE VIEW`` that expresses the semantic model as a SQL artefact.

    Args:
        osi_model:  The OSI semantic model.
        dialect:    Target SQL dialect (``snowflake`` | ``ansi``).
        database:   Target database name (used in fully-qualified names).
        schema:     Target schema name.
        include_source_tables:  Whether to generate DDL for backing tables.
        llm_config: Forwarded to ``DAXTranslator`` for metric translation.
        idempotent_ddl: When True (default), use CREATE OR REPLACE for tables (Mandate 2).

    Returns:
        ``OSIToSQLResult`` containing generated DDL and metadata.

    Raises:
        ConversionError: If the dialect is unsupported or conversion fails.
    """
    dialect = dialect.lower()
    if dialect not in _TYPE_MAP:
        raise ConversionError(
            f"Unsupported SQL dialect '{dialect}'. "
            f"Supported: {', '.join(_TYPE_MAP.keys())}",
            source_format="osi",
            target_format="sql",
        )

    converter = _OSIToSQLConverter(
        osi_model=osi_model,
        dialect=dialect,
        database=database,
        schema=schema,
        llm_config=llm_config,
        idempotent_ddl=idempotent_ddl,
    )
    return converter.generate(include_source_tables=include_source_tables)


# =============================================================================
# Internal Converter
# =============================================================================


class _OSIToSQLConverter:
    """Internal stateful converter (not exported)."""

    def __init__(
        self,
        osi_model: OSIModel,
        dialect: str,
        database: str,
        schema: str,
        llm_config: Any = False,
        idempotent_ddl: bool = True,
    ):
        self.model = osi_model
        self.dialect = dialect
        self.database = database
        self.schema = schema
        self.type_map = _TYPE_MAP[dialect]
        self.warnings: List[str] = []
        self.dax_translator = DAXTranslator(llm_config=llm_config)
        # Unified identifier sanitizer (Mandate 1)
        self._id = IdentifierSanitizer()
        # Mandate 2: Idempotent DDL
        self.idempotent_ddl = idempotent_ddl

    # ------------------------------------------------------------------
    # Top-level generation
    # ------------------------------------------------------------------

    def generate(self, *, include_source_tables: bool = True) -> OSIToSQLResult:
        """Run the full generation pipeline."""
        source_ddls: List[str] = []
        if include_source_tables:
            for ds in self.model.datasets:
                source_ddls.append(self._generate_table_ddl(ds))

        metric_exprs = self._translate_metrics()
        view_ddl = self._generate_semantic_view(metric_exprs)

        return OSIToSQLResult(
            semantic_view_ddl=view_ddl,
            source_table_ddls=source_ddls,
            metric_expressions=metric_exprs,
            dialect=self.dialect,
            warnings=list(self.warnings),
        )

    # ------------------------------------------------------------------
    # Source table DDL
    # ------------------------------------------------------------------

    def _generate_table_ddl(self, ds: OSIDataset) -> str:
        """Generate CREATE TABLE for a single dataset.

        Uses CREATE OR REPLACE when idempotent_ddl is enabled (Mandate 2).
        """
        table_name = self._fqn(self._safe_name(ds.source_table or ds.unique_name))
        col_defs: List[str] = []
        for col in ds.columns:
            sql_type = self.type_map.get(col.data_type, "VARCHAR")
            col_defs.append(f'    "{self._safe_col(col.unique_name)}" {sql_type}')

        cols_block = ",\n".join(col_defs)
        verb = "CREATE OR REPLACE TABLE" if self.idempotent_ddl else "CREATE TABLE IF NOT EXISTS"
        return f"{verb} {table_name} (\n{cols_block}\n);"

    # ------------------------------------------------------------------
    # Metric translation
    # ------------------------------------------------------------------

    def _translate_metrics(self) -> Dict[str, str]:
        """Translate all OSI metrics to SQL expressions."""
        exprs: Dict[str, str] = {}

        # Build a list of dataset names for cross-reference
        ds_names = {ds.unique_name for ds in self.model.datasets}

        for metric in self.model.metrics:
            sql = self._translate_one_metric(metric, ds_names)
            if sql:
                exprs[metric.unique_name] = sql
            else:
                self.warnings.append(
                    f"Metric '{metric.unique_name}' could not be translated to SQL"
                )
        return exprs

    def _translate_one_metric(
        self, metric: OSIMetric, ds_names: set
    ) -> Optional[str]:
        """Translate a single metric, using override / dialect / DAX path."""

        # 1. Already has an explicit SQL expression (from override or prior pass)
        if metric.sql_expression:
            return metric.sql_expression

        # 2. Check multi-dialect list for target dialect
        target_dialect_tag = self._dialect_tag()
        for d in metric.dialects:
            if d.dialect.upper() == target_dialect_tag:
                return d.expression

        # 3. Simple aggregation on source_column (no DAX needed)
        if metric.source_column and not metric.expression:
            return self._simple_agg_sql(metric)

        # 4. DAX expression → translate via DAXTranslator
        if metric.expression:
            safe_alias = self._safe_name(metric.dataset)
            result = self.dax_translator.translate(
                metric.expression,
                safe_alias,
                metric.dataset,
                metric_name=metric.unique_name,
            )
            if result.is_success:
                return result.sql

        return None

    def _simple_agg_sql(self, metric: OSIMetric) -> str:
        """Build SQL for a simple column aggregation."""
        alias = self._safe_name(metric.dataset)
        col = self._safe_col(metric.source_column)
        agg = _AGG_MAP.get(metric.aggregation, "SUM")

        if "{}" in agg:
            # COUNT DISTINCT or NONE
            return agg.format(f'{alias}."{col}"')
        return f'{agg}({alias}."{col}")'

    # ------------------------------------------------------------------
    # Semantic view DDL
    # ------------------------------------------------------------------

    def _generate_semantic_view(self, metric_exprs: Dict[str, str]) -> str:
        """Generate the semantic view combining tables, joins, and metrics."""
        view_name = self._fqn(f"V_{self._safe_name(self.model.unique_name)}")

        select_parts: List[str] = []
        from_parts: List[str] = []
        join_parts: List[str] = []

        # Identify the fact dataset(s) as the driving FROM table
        fact_datasets = [ds for ds in self.model.datasets if ds.is_fact]
        dim_datasets = [ds for ds in self.model.datasets if not ds.is_fact]

        if not fact_datasets:
            # Fallback: use first dataset
            if self.model.datasets:
                fact_datasets = [self.model.datasets[0]]
            else:
                return f"-- No datasets in model '{self.model.unique_name}'"

        primary_fact = fact_datasets[0]
        primary_alias = self._safe_name(primary_fact.unique_name)
        from_parts.append(
            f"  {self._fqn(self._safe_name(primary_fact.source_table or primary_fact.unique_name))} AS {primary_alias}"
        )

        # Build JOIN clauses from relationships
        joined_datasets: set = {primary_fact.unique_name}
        for rel in self.model.relationships:
            target_ds_name = None
            if rel.from_dataset in joined_datasets and rel.to_dataset not in joined_datasets:
                target_ds_name = rel.to_dataset
                left_alias = self._safe_name(rel.from_dataset)
                right_alias = self._safe_name(rel.to_dataset)
                left_cols = rel.from_columns
                right_cols = rel.to_columns
            elif rel.to_dataset in joined_datasets and rel.from_dataset not in joined_datasets:
                target_ds_name = rel.from_dataset
                left_alias = self._safe_name(rel.to_dataset)
                right_alias = self._safe_name(rel.from_dataset)
                left_cols = rel.to_columns
                right_cols = rel.from_columns

            if target_ds_name:
                target_ds = self.model.get_dataset(target_ds_name)
                if target_ds:
                    table_ref = self._fqn(
                        self._safe_name(target_ds.source_table or target_ds.unique_name)
                    )
                    on_clause = " AND ".join(
                        f'{left_alias}."{self._safe_col(lc)}" = {right_alias}."{self._safe_col(rc)}"'
                        for lc, rc in zip(left_cols, right_cols)
                    )
                    join_parts.append(
                        f"  LEFT JOIN {table_ref} AS {right_alias}\n    ON {on_clause}"
                    )
                    joined_datasets.add(target_ds_name)

        # Dimension columns (non-hidden, from dimensions)
        group_cols: List[str] = []
        for dim in self.model.dimensions:
            for attr in dim.attributes:
                if not attr.is_hidden:
                    ds_alias = self._safe_name(attr.dataset)
                    col_name = self._safe_col(attr.source_column)
                    ref = f'{ds_alias}."{col_name}"'
                    select_parts.append(f"  {ref}")
                    group_cols.append(ref)

        # Metric select expressions
        for metric_name, sql_expr in metric_exprs.items():
            safe_metric = self._safe_col(metric_name)
            select_parts.append(f'  {sql_expr} AS "{safe_metric}"')

        if not select_parts:
            return f"-- No columns or metrics for view '{self.model.unique_name}'"

        select_block = ",\n".join(select_parts)
        from_block = "\n".join(from_parts)
        join_block = "\n".join(join_parts) if join_parts else ""
        group_block = ",\n  ".join(group_cols) if group_cols else ""

        ddl_parts = [
            f"CREATE OR REPLACE VIEW {view_name} AS",
            f"SELECT\n{select_block}",
            f"FROM\n{from_block}",
        ]
        if join_block:
            ddl_parts.append(join_block)
        if group_block:
            ddl_parts.append(f"GROUP BY\n  {group_block}")

        return "\n".join(ddl_parts) + ";"

    # ------------------------------------------------------------------
    # Naming helpers
    # ------------------------------------------------------------------

    def _fqn(self, name: str) -> str:
        """Fully-qualified name: DATABASE.SCHEMA."NAME"."""
        return f'{self.database}.{self.schema}."{name}"'

    def _safe_name(self, name: str) -> str:
        """Sanitize a name for use as a SQL identifier (unified sanitizer)."""
        return self._id.sanitize_table_name(name)

    def _safe_col(self, name: str) -> str:
        """Sanitize a column name (unified sanitizer)."""
        return self._id.sanitize_column(name)

    def _dialect_tag(self) -> str:
        """Return the OSIExpressionDialect tag for the target."""
        return {
            "snowflake": "SNOWFLAKE_SQL",
            "ansi": "ANSI_SQL",
        }.get(self.dialect, self.dialect.upper())
