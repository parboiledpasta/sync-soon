"""
TMSL to OSI Converter.

Converts Fabric Model Definitions (TMSL JSON) into the OSI (Open Semantic Interchange)
canonical intermediate representation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
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
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TMSLToOSIConverter(BaseConverter):
    """
    Transforms Fabric TMSL JSON into OSI Model.
    """

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Convert TMSL dictionary to OSIModel object.

        Args:
            source_data: Dictionary containing:
                - tmsl: Decoded model.bim JSON
                - workspace_id: Fabric workspace ID
                - dataset_id: Fabric dataset ID (unique_name for OSIModel)

        Returns:
            OSIModel object
        
        Raises:
            ConversionError: If transformation fails.
        """
        try:
            tmsl_json = source_data.get("tmsl", {})
            workspace_id = source_data.get("workspace_id")
            dataset_id = source_data.get("dataset_id")

            if not tmsl_json or not dataset_id:
                raise ConversionError(
                    "Missing 'tmsl' or 'dataset_id' in source_data",
                    source_format="tmsl",
                    target_format="osi"
                )

            model_obj = tmsl_json.get("model", {})
            name = model_obj.get("name", "FabricModel")

            osi_model = OSIModel(
                unique_name=dataset_id,
                label=name,
                description=model_obj.get("description", ""),
                source_platform="fabric",
                metadata={"workspace_id": workspace_id}
            )

            # Process Datasets (Tables)
            self._current_model_name = name
            if "tables" in model_obj:
                for table in model_obj["tables"]:
                    # Skip internal tables
                    t_name = table.get("name", "")
                    if t_name.startswith("DateTableTemplate") or t_name.startswith("LocalDateTable"):
                        continue
                    
                    dataset = self._parse_dataset(table)
                    osi_model.datasets.append(dataset)

                    # Create corresponding Dimension for each Dataset
                    # In TMSL/Power BI, every table is potentially a dimension
                    dim = self._create_dimension_from_dataset(dataset)
                    if dim:
                        osi_model.dimensions.append(dim)

                    # Process Measures (Metrics)
                    if "measures" in table:
                        for measure in table["measures"]:
                            metric = self._parse_metric(measure, dataset.unique_name)
                            osi_model.metrics.append(metric)

            # Process Relationships
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    osi_rel = self._parse_relationship(rel)
                    if osi_rel:
                        osi_model.relationships.append(osi_rel)

            return osi_model

        except Exception as e:
            logger.error(f"TMSL to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert TMSL: {e}",
                source_format="tmsl",
                target_format="osi",
                details={"error": str(e)}
            )

    def from_osi(self, osi_model: OSIModel) -> Any:
        # Implementing simple TMSL generation or raising NotImplemented
        # Ideally, we should use a separate OSIToTMSLConverter or TmslGenerator
        raise NotImplementedError("OSI to TMSL conversion is handled by TmslGenerator")

    def _create_dimension_from_dataset(self, dataset: OSIDataset) -> Optional[OSIDimension]:
        """Create an implicit dimension from a dataset."""
        if dataset.is_hidden:
            return None
            
        attributes = []
        for col in dataset.columns:
            # Skip hidden columns or potential measures (metrics usually come separately, but columns might be hidden)
            if col.is_hidden:
                continue

            # Skip calculated columns — they have no physical backing in
            # Snowflake and cause "invalid identifier" errors when referenced
            # in the semantic view DIMENSIONS clause.
            if col.source_expression and not self._is_physical_source_column(col.source_expression):
                logger.debug(
                    f"Skipping calculated column '{col.unique_name}' "
                    f"from dimension '{dataset.unique_name}'"
                )
                continue
                
            attr = OSIAttribute(
                unique_name=col.unique_name,
                label=col.label,
                dataset=dataset.unique_name,
                source_column=col.unique_name,
                is_hidden=col.is_hidden
            )
            attributes.append(attr)
            
        if not attributes:
            return None
            
        return OSIDimension(
            unique_name=dataset.unique_name,
            label=dataset.label,
            description=dataset.description,
            dataset=dataset.unique_name,
            attributes=attributes,
            is_hidden=dataset.is_hidden
        )

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        """Determine if a source_expression represents a plain physical column.

        Returns:
            True for simple identifiers (physical columns).
            False for DAX expressions (calculated columns).
        """
        if not source_expression:
            return True
        expr = source_expression.strip()
        if not expr:
            return True
        if re.search(r'[()[\]+\-*/=<>!&|,\n]', expr):
            return False
        return True

    def _parse_dataset(self, table_def: Dict[str, Any]) -> OSIDataset:
        """Parse a TMSL table into OSIDataset."""
        name = table_def["name"]
        
        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col))
        
        # Source table logic: derive unique physical table name for generic
        # 'Table' names using model context to avoid cross-model collisions.
        source_table = name
        if name == "Table":
            if hasattr(self, '_current_model_name') and self._current_model_name:
                import re as _re
                safe_model = _re.sub(r'[^a-zA-Z0-9]', '_', self._current_model_name).strip('_').upper()
                source_table = f"{safe_model}_DATA"
            else:
                source_table = "TABLE_DATA"

        return OSIDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description"),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table
        )

    def _parse_column(self, col_def: Dict[str, Any]) -> OSIColumn:
        """Parse a TMSL column into OSIColumn."""
        tmsl_type = col_def.get("dataType", "string")
        col_name = col_def.get("name", "")
        
        type_map = {
            "int64": OSIDataType.INTEGER,
            "double": OSIDataType.FLOAT,
            "decimal": OSIDataType.DECIMAL,
            "boolean": OSIDataType.BOOLEAN,
            "dateTime": OSIDataType.DATETIME,
            "string": OSIDataType.STRING,
            "binary": OSIDataType.BINARY
        }
        
        mapped_type = type_map.get(tmsl_type, OSIDataType.STRING)
        
        # Determine if key (simple heuristic)
        # In TMSL, keys are implicit unless we check relationships or annotations.
        # Here we just check name pattern like in legacy
        is_key = False
        upper_name = col_name.upper()
        if upper_name.endswith("ID") or upper_name.endswith("KEY") or upper_name.startswith("PK_") or upper_name.startswith("FK_"):
            is_key = True

        
        # Handle list expressions (common in TMSL)
        source_expr = col_def.get("sourceColumn") or col_def.get("expression")
        
        # If this is a calculated column, ensure source_expression is treated as such
        # even if it's a simple name (e.g. referencing another measure).
        if (col_def.get("type") == "calculated" or col_def.get("type") == "calculatedTableColumn") and source_expr:
            # Wrap in parentheses to ensure _is_physical_source_column (which uses regex)
            # correctly identifies it as a non-physical expression even if it's just a name.
            if isinstance(source_expr, str) and not re.search(r'[()[\]+\-*/=<>!&|,\n]', source_expr):
                 source_expr = f"({source_expr})"

        if isinstance(source_expr, list):
            source_expr = "\n".join(source_expr)

        return OSIColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            description=col_def.get("description"),
            is_hidden=col_def.get("isHidden", False),
            format_string=col_def.get("formatString"),
            is_key=is_key,
            source_expression=source_expr
        )

    def _parse_metric(self, measure_def: Dict[str, Any], dataset_name: str) -> OSIMetric:
        """Parse a TMSL measure into OSIMetric."""
        dax = measure_def.get("expression", "")
        if isinstance(dax, list):
            dax = "\n".join(dax)
            
        return OSIMetric(
            unique_name=measure_def["name"],
            label=measure_def["name"],
            dataset=dataset_name,
            expression=dax,
            aggregation=OSIAggregationType.NONE, # Raw DAX implies explicit calc
            description=measure_def.get("description"),
            format_string=measure_def.get("formatString"),
            is_hidden=measure_def.get("isHidden", False)
        )

    def _parse_relationship(self, rel_def: Dict[str, Any]) -> Optional[OSIRelationship]:
        """Parse TMSL relationship."""
        try:
             name = rel_def.get("name", f"Rel_{rel_def['fromTable']}_{rel_def['toTable']}")
             
             card_map = {
                 "manytoone": OSICardinality.MANY_TO_ONE,
                 "onetoone": OSICardinality.ONE_TO_ONE,
                 "onetomany": OSICardinality.ONE_TO_MANY,
                 "manytomany": OSICardinality.MANY_TO_MANY
             }
             raw_card = rel_def.get("cardinality", "ManyToOne").lower()
             
             cf_map = {
                 "single": OSICrossFilterDirection.SINGLE,
                 "both": OSICrossFilterDirection.BOTH
             }
             raw_cf = rel_def.get("crossFilteringBehavior", "Single").lower()

             return OSIRelationship(
                 unique_name=name,
                 from_dataset=rel_def["fromTable"],
                 from_columns=[rel_def["fromColumn"]],
                 to_dataset=rel_def["toTable"],
                 to_columns=[rel_def["toColumn"]],
                 cardinality=card_map.get(raw_card, OSICardinality.MANY_TO_ONE),
                 cross_filter_direction=cf_map.get(raw_cf, OSICrossFilterDirection.SINGLE),
                 is_active=rel_def.get("isActive", True)
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship '{rel_def.get('name')}': {e}")
            return None
