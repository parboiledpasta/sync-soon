"""
TMSL to SML Transformer.

Converts Fabric Model Definitions (TMSL JSON) into the Semantic Modeling Language (SML)
intermediate representation.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship, SMLDimension, SMLAttribute,
    DataType, AggregationType, Cardinality, SourcePlatform
)
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TransformationError(Exception):
    """Raised when transformation fails."""
    pass


class TMSLTransformer:
    """
    Transforms Fabric TMSL JSON into SML Model.
    """
    
    def __init__(self):
        self.dax_translator = DAXTranslator(llm_config=False)
    
    def transform(self, tmsl_json: Dict[str, Any], workspace_id: str, dataset_id: str, row_counts: Dict[str, int] = None, metric_overrides: Dict[str, str] = None, behavior: Optional[ConnectorBehavior] = None) -> SMLModel:
        """
        Transform TMSL dictionary to SML object.
        
        Args:
            tmsl_json: Decoded model.bim JSON
            workspace_id: Fabric workspace ID (for lineage/metadata)
            dataset_id: Fabric dataset ID
            row_counts: Optional dictionary of {count: int}
            
        Returns:
            SMLModel object
        """
        try:
            model_obj = tmsl_json.get("model", {})
            name = model_obj.get("name", "FabricModel")
            
            # Initialize SML Model
            sml = SMLModel(
                unique_name=dataset_id,
                label=name,
                description=model_obj.get("description", ""),
                source_system="fabric",
                source_platform=SourcePlatform.FABRIC
            )
            
            if "tables" not in model_obj:
                return sml
                
            # 1. Process Tables (Datasets)
            for table in model_obj["tables"]:
                # specific handling for partitions/sources can be complex
                # assuming Import mode or DirectLake from common structures
                
                # Check for Calculation Groups - treat specifically?
                # For now, treat as dataset or ignore.
                if table.get("calculationGroup"):
                    continue # Skip Calc Groups for V1
                
                # Hidden internal tables?
                if table.get("name", "").startswith("DateTableTemplate") or table.get("name", "").startswith("LocalDateTable"):
                    continue
                
                ds = self._parse_table(table, model_name=name)
                sml.datasets.append(ds)
                
                # 2. Process Measures (Metrics)
                # Measures in TMSL live under tables
                if "measures" in table:
                    for measure in table["measures"]:
                        metric = self._parse_measure(
                            measure, 
                            ds.unique_name,
                            metric_overrides,
                            sml.metrics
                        )
                        sml.metrics.append(metric)
            
            # 3. Process Relationships
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    sml_rel = self._parse_relationship(rel, sml)
                    if sml_rel:
                        sml.relationships.append(sml_rel)
            
            # --- Auto-Detect Relationships (for implicit models) ---
            # Create a simplified metadata structure for the detector
            tables_meta = {}
            columns_meta = {}
            pks_meta = {}
            
            for ds in sml.datasets:
                tables_meta[ds.unique_name] = {"row_count": 0} # Row count unavail in TMSL
                columns_meta[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
                # Assume columns ending in ID matching the table name are PKs
                pks_meta[ds.unique_name] = [c.unique_name for c in ds.columns if c.is_key]
            
            from semabridge.connectors.relationship_detector import RelationshipDetector
            rel_detector = RelationshipDetector(tables_meta, columns_meta, pks_meta)
            inferred_rels = rel_detector.detect_all()
            
            # Add implicit relationships if not exists
            # Enhanced Check: Check for structural existence (From-To pair), not just name
            existing_rel_signatures = {
                tuple(sorted((r.from_dataset, r.to_dataset))) 
                for r in sml.relationships
            }
            
            for rel in inferred_rels:
                # Check active signature
                sig = tuple(sorted((rel["from_table"], rel["to_table"])))
                
                if sig not in existing_rel_signatures:
                    sml.relationships.append(SMLRelationship(
                        unique_name=rel["name"],
                        from_dataset=rel["from_table"],
                        from_columns=[rel["from_column"]],
                        to_dataset=rel["to_table"],
                        to_columns=[rel["to_column"]],
                        cardinality=Cardinality.MANY_TO_ONE,
                        is_active=True
                    ))
                    existing_rel_signatures.add(sig)

            # 4. Refine Model Structure (Star Schema)
            # Pass relationships for better classification
            # We map SML relationships back to the dict format expected by the engine
            engine_rels = []
            for r in sml.relationships:
                engine_rels.append({
                    "from_table": r.from_dataset,
                    "to_table": r.to_dataset
                })
                
            self._classify_tables(sml, engine_rels, row_counts or {})
            self._inject_calendar_dimension(sml)

            # --- NEW: Populate SML Dimensions from Datasets ---
            # SML Dimensions are required for proper Snowflake/Cortex generation.
            # In the absence of explicit dimensions in TMSL, we map each Table -> Dimension.
            
            for ds in sml.datasets:
                # specific logic to skip internal tables if needed
                if ds.is_hidden: 
                    continue
                    
                attributes = []
                sync_all = behavior.semantic_model.sync_all_attributes if behavior else True
                
                for col in ds.columns:
                    if not sync_all:
                        if col.is_hidden or col.is_measure_candidate:
                            continue

                    # Skip calculated columns — they have no physical backing
                    # in Snowflake and cause "invalid identifier" errors when
                    # referenced in the semantic view DIMENSIONS clause.
                    source_expr = getattr(col, 'source_expression', None)
                    if source_expr and not self._is_physical_source_column(source_expr):
                        logger.debug(
                            f"Skipping calculated column '{col.unique_name}' "
                            f"from dimension attributes"
                        )
                        continue
                        
                    attr = SMLAttribute(
                        unique_name=col.unique_name,
                        label=col.label,
                        dataset=ds.unique_name,
                        dataset_column=col.unique_name,
                        description=col.description,
                        is_hidden=col.is_hidden
                    )
                    attributes.append(attr)
                
                if attributes:
                    dim = SMLDimension(
                        unique_name=ds.unique_name,
                        label=ds.label,
                        dataset=ds.unique_name,
                        attributes=attributes,
                        description=ds.description
                    )
                    sml.dimensions.append(dim)

            
            # --- NEW: Auto-Detect Measures ---
            from semabridge.connectors.measure_detector import MeasureDetector
            
            # Create simplified metadata for detector/inference
            tables_meta = {}
            for ds in sml.datasets:
                rc = (row_counts or {}).get(ds.unique_name, 0)
                tables_meta[ds.unique_name] = {"row_count": rc}
                
            columns_meta = {}
            for ds in sml.datasets:
                columns_meta[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
                
            measure_detector = MeasureDetector(tables_meta, columns_meta, inferred_rels)
            
            # Map classifications for measure detector
            classification_map = {ds.unique_name: ("FACT" if ds.is_fact else "DIMENSION") for ds in sml.datasets}
            
            auto_measures = measure_detector.detect_all_measures(classification=classification_map)
            
            # Add metrics if they don't already exist (by name)
            existing_metrics = {m.unique_name for m in sml.metrics}
            for table_name, measures in auto_measures.items():
                for m in measures:
                    # Fabric often has measures named like 'Total Amount'
                    # If we already have a measure on this table with a similar name, skip
                    if m["name"] not in existing_metrics:
                        # Convert measure candidate to SMLMetric
                        metric = SMLMetric(
                            unique_name=m["name"],
                            label=m["name"],
                            dataset=table_name,
                            source_column=m["column"],
                            aggregation=AggregationType(m["aggregation"]),
                            confidence=m["confidence"]
                        )
                        sml.metrics.append(metric)
                        existing_metrics.add(m["name"])
            
            # --- Validate Metric Column References ---
            # Ensure every metric's source_column exists in its dataset.
            # Prevents invalid identifier errors in downstream DDL generation.
            ds_column_map: dict[str, set[str]] = {}
            for ds in sml.datasets:
                ds_column_map[ds.unique_name] = {
                    c.unique_name for c in ds.columns
                    if not c.unique_name.startswith("RowNumber")
                    and not c.unique_name.startswith("_")
                }

            valid_metrics: list[SMLMetric] = []
            for metric in sml.metrics:
                if metric.source_column:
                    ds_cols = ds_column_map.get(metric.dataset, set())
                    if metric.source_column not in ds_cols:
                        logger.warning(
                            f"Dropping metric '{metric.unique_name}': "
                            f"source_column '{metric.source_column}' not found "
                            f"in dataset '{metric.dataset}'"
                        )
                        continue
                valid_metrics.append(metric)
            sml.metrics = valid_metrics

            return sml
            
        except Exception as e:
            logger.error(f"Transformation failed: {e}")
            raise TransformationError(f"Failed to transform TMSL to SML: {e}")
            
    def _classify_tables(self, sml: SMLModel, relationships: List[Dict[str, Any]] = None, row_counts: Dict[str, int] = None) -> None:
        """
        Classify datasets as Facts or Dimensions using SmlInferenceEngine.
        """
        # Convert SML structure to Engine format
        tables = {}
        for ds in sml.datasets:
            rc = (row_counts or {}).get(ds.unique_name, 0)
            tables[ds.unique_name] = {"row_count": rc}
            # Also update the dataset object itself slightly if we can (optional)
            
        columns = {}
        for ds in sml.datasets:
            columns[ds.unique_name] = [{"name": c.unique_name, "data_type": c.data_type.value} for c in ds.columns]
        
        from semabridge.connectors.inference_engine import SmlInferenceEngine
        engine = SmlInferenceEngine(
            tables=tables,
            columns=columns,
            relationships=relationships or [],
            primary_keys={} 
        )
        scores = engine.classify()
        
        for ds in sml.datasets:
            score = scores.get(ds.unique_name)
            if score:
                # Force Dimensions by regex override still useful
                if any(x in ds.unique_name.upper() for x in ["BU", "BUSINESSUNIT", "DIM", "USER", "CALENDAR"]):
                     ds.is_fact = False
                     continue

                if score.classification == "FACT":
                    ds.is_fact = True
                else:
                    ds.is_fact = False

    def _inject_calendar_dimension(self, sml: SMLModel) -> None:
        """
        Ensure a Calendar/Date dimension exists.
        If missing, inject a standard one.
        """
        # Check if any date dimension exists
        if any("DATE" in ds.unique_name.upper() or "CALENDAR" in ds.unique_name.upper() for ds in sml.datasets):
            return
            
        logger.info("Injecting missing Calendar/Date dimension")
        
        # Create standard Date column definitions
        cols = [
            SMLColumn(unique_name="Date", data_type=DataType.DATE, is_key=True),
            SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Quarter", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
            SMLColumn(unique_name="MonthName", data_type=DataType.STRING),
            SMLColumn(unique_name="DayOfWeek", data_type=DataType.INTEGER),
            SMLColumn(unique_name="DayName", data_type=DataType.STRING),
        ]
        
        date_ds = SMLDataset(
            unique_name="Date",
            label="Date",
            description="Auto-generated Calendar Dimension",
            source_table="DIM_DATE", # Will be auto-generated in Snowflake
            columns=cols,
            is_fact=False
        )
        
        sml.datasets.append(date_ds)

    @staticmethod
    def _is_physical_source_column(source_expression: str) -> bool:
        """Determine if a source_expression represents a plain physical column reference.

        In TMSL, regular columns have a ``sourceColumn`` value that is a
        simple identifier (e.g. ``"Revenue"``).  Calculated columns have an
        ``expression`` that contains DAX formulas (e.g. ``"[Revenue] + [COGS]"``
        or ``"CALCULATE(SUM(...))"``).

        Returns:
            True if the expression looks like a plain column name reference
            (safe to include in physical DDL and DIMENSIONS).
            False if it contains DAX operators / functions (should be excluded).
        """
        if not source_expression:
            return True
        expr = source_expression.strip()
        if not expr:
            return True
        import re
        # Quick reject: DAX function calls, bracket refs, arithmetic
        if re.search(r'[()[\]+\-*/=<>!&|,\n]', expr):
            return False
        # If it's just a simple word (with spaces allowed for names like
        # "Customer Key"), treat it as a physical column.
        return True
    
    def _parse_table(self, table_def: Dict[str, Any], model_name: str = "") -> SMLDataset:
        """Parse a TMSL table into SMLDataset."""
        name = table_def["name"]
        
        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col))
                
        # For Fabric reverse flow, source table might be vague if it's an import query.
        # If the table name is literally 'Table', derive a unique physical table name
        # from the model name so different models don't collide on the same table.
        source_table = name
        if name == "Table":
            if model_name:
                import re as _re
                safe_model = _re.sub(r'[^a-zA-Z0-9]', '_', model_name).strip('_').upper()
                source_table = f"{safe_model}_DATA"
            else:
                source_table = "TABLE_DATA"
            logger.info(f"Generic table name 'Table' mapped to source: {source_table}")
            
        return SMLDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description", ""),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table
        )
    
    def _parse_column(self, col_def: Dict[str, Any]) -> SMLColumn:
        """Parse a TMSL column into SMLColumn."""
        import re
        
        tmsl_type = col_def.get("dataType", "string")
        col_name = col_def.get("name", "")
        name_upper = col_name.upper()
        
        # Simple mapping
        type_map = {
            "int64": DataType.INTEGER,
            "double": DataType.FLOAT,
            "decimal": DataType.DECIMAL,
            "boolean": DataType.BOOLEAN,
            "dateTime": DataType.DATETIME,
            "string": DataType.STRING,
            "binary": DataType.BINARY
        }
        
        mapped_type = type_map.get(tmsl_type, DataType.STRING)
        
        # =====================================================================
        # Capture source_expression for calculated column detection
        # =====================================================================
        # In TMSL, physical columns have "sourceColumn" (plain identifier).
        # Calculated columns have "expression" (DAX formula) and NO sourceColumn.
        # We store the appropriate one so downstream
        # Handle list expressions (common in TMSL)
        source_expr = col_def.get("sourceColumn") or col_def.get("expression")

        # If this is a calculated column, ensure source_expression is treated as such
        # even if it's a simple name (e.g. referencing another measure).
        if (col_def.get("type") == "calculated" or col_def.get("type") == "calculatedTableColumn") and source_expr:
            # Wrap in parentheses to ensure _is_physical_source_column (which uses regex)
            # correctly identifies it as a non-physical expression even if it's just a name.
            # We use re.search with the same bracket/op pattern as below.
            import re as _re
            if isinstance(source_expr, str) and not _re.search(r'[()[\]+\-*/=<>!&|,\n]', source_expr):
                 source_expr = f"({source_expr})"

        if isinstance(source_expr, list):
            source_expr = "\n".join(source_expr)

        # =====================================================================
        # Enhanced Semantic Type Inference (FR-02)
        # =====================================================================
        
        # Patterns indicating the column should be a MEASURE (not dimension)
        MEASURE_NAME_PATTERNS = [
            r"(amount|revenue|cost|price|qty|quantity|sales|total|value|margin|tax|discount|profit|balance|sum|fee|payment)$",
            r"^(total|sum|avg|net|gross)_",
            r"_(amount|revenue|cost|price|qty|quantity|sales|total|value|margin)$",
        ]
        
        # Patterns indicating the column is an ID/Key (should stay as dimension)
        KEY_PATTERNS = [
            r"(id|key|code|num|number)$",
            r"^(pk_|fk_|id_|sk_)",
        ]
        
        # Format patterns indicating measure (currency, percentage)
        MEASURE_FORMAT_PATTERNS = [
            r"\$",            # Currency symbol
            r"#,##0",         # Number formatting
            r"0\.00%",        # Percentage
            r"€|£|¥",         # Other currency symbols
        ]
        
        is_measure_candidate = False
        
        # 1. Explicit TMSL Override: Check summarizeBy property
        #    summarizeBy = "sum" | "avg" | "count" | "max" | "min" | "none"
        summarize_by = col_def.get("summarizeBy", "").lower()
        if summarize_by and summarize_by != "none":
            is_measure_candidate = True
            logger.debug(f"Column '{col_name}' marked as measure via summarizeBy={summarize_by}")
        
        # 2. Format String Detection (currency/percent = measure)
        format_string = col_def.get("formatString", "")
        if format_string:
            for pattern in MEASURE_FORMAT_PATTERNS:
                if re.search(pattern, format_string):
                    is_measure_candidate = True
                    logger.debug(f"Column '{col_name}' marked as measure via format string")
                    break
        
        # 3. Name Pattern Heuristics (if numeric type)
        if mapped_type in (DataType.INTEGER, DataType.FLOAT, DataType.DECIMAL):
            # First check if it's a Key/ID column
            is_key_column = any(re.search(p, name_upper, re.IGNORECASE) for p in KEY_PATTERNS)
            
            if not is_key_column:
                # Check if it matches measure name patterns
                for pattern in MEASURE_NAME_PATTERNS:
                    if re.search(pattern, name_upper, re.IGNORECASE):
                        is_measure_candidate = True
                        logger.debug(f"Column '{col_name}' marked as measure via name pattern")
                        break
        
        # HEURISTIC: If everything is string (common in some sources), try to infer from name
        if mapped_type == DataType.STRING:
            # 1. Date/Time
            if any(x in name_upper for x in ["DATE", "TIME", "_DT", "TIMESTAMP"]) and not name_upper.endswith("ID"):
                 mapped_type = DataType.DATETIME
                 
            # 2. Measures (Numeric)
            elif any(x in name_upper for x in ["AMOUNT", "REVENUE", "COST", "PRICE", "QTY", "QUANTITY", "SALES", "TOTAL", "VALUE", "MARGIN", "TAX"]):
                # Ensure it's not an ID (e.g. TaxID)
                if not (name_upper.endswith("ID") or name_upper.endswith("KEY") or name_upper.endswith("CODE")):
                    mapped_type = DataType.DECIMAL
                    is_measure_candidate = True
            
            # 3. Integers (Counts, Years, etc.)
            elif name_upper in ["YEAR", "MONTH", "QUARTER", "DAY", "ROWNUMBER"]:
                mapped_type = DataType.INTEGER
                
        # DEBUG: Log if we see unexpected types or fallback to string
        if mapped_type == DataType.STRING and tmsl_type != "string":
            logger.debug(f"Column '{col_name}' has type '{tmsl_type}', falling back to STRING")
            
        return SMLColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            description=col_def.get("description", ""),
            is_hidden=col_def.get("isHidden", False),
            is_measure_candidate=is_measure_candidate,
            format_string=format_string,
            folder=col_def.get("displayFolder"),
            source_expression=source_expr,
        )

    def _parse_measure(self, measure_def: Dict[str, Any], table_name: str, overrides: Dict[str, str] = None, metrics_context: List[Any] = None) -> SMLMetric:
        """Parse a TMSL measure into SMLMetric with complexity analysis."""
        dax = measure_def.get("expression", "")
        if isinstance(dax, list):
            dax = "\n".join(dax)  # TMSL expressions can be arrays of strings
        
        # Analyze DAX complexity for sync metadata
        complexity = self.dax_translator.analyze_complexity(dax)
        required_dims = self.dax_translator.get_required_dimensions(dax)
        
        metric = SMLMetric(
            unique_name=measure_def["name"],
            label=measure_def["name"],
            description=measure_def.get("description", ""),
            dataset=table_name,
            expression=dax,
            folder=measure_def.get("displayFolder"),
            is_hidden=measure_def.get("isHidden", False),
            format_string=measure_def.get("formatString"),
            aggregation=AggregationType.NONE,  # Default to NONE for raw DAX
            # Sync metadata from complexity analysis
            complexity_tier=complexity["tier"],
            requires_time_intel=complexity["requires_time_intel"],
            group_by_dimensions=complexity["group_by_dimensions"] or required_dims,
            depends_on_measures=complexity["depends_on_measures"],
            sync_enabled=complexity["sync_enabled"],
            sync_failure_reason=complexity["failure_reason"],
            # Default partition to Year for Time Intelligence measures
            partition_dimension="'Date'[Year]" if complexity["requires_time_intel"] else None,
        )
        
        # Attempt Translation
        safe_alias = "".join(c if c.isalnum() else "_" for c in table_name).upper()
        
        translation = self.dax_translator.translate(
            dax, 
            safe_alias, 
            table_name,
            overrides=overrides,
            metric_name=metric.unique_name,
            metrics_context=metrics_context
        )
        
        if translation.is_success:
            metric.sql_expression = translation.sql
            # Update complexity tier based on successful translation
            metric.complexity_tier = translation.tier
            metric.sync_enabled = True
            metric.sync_failure_reason = None
        elif metric.sync_enabled and not translation.is_success:
            # Translation failed but was expected to work - update status
            metric.sync_enabled = False
            metric.sync_failure_reason = f"DAX translation failed (Tier {translation.tier})"
            
        return metric

    def _parse_relationship(self, rel_def: Dict[str, Any], sml_context: SMLModel) -> Optional[SMLRelationship]:
        """Parse TMSL relationship."""
        # TMSL: fromTable, fromColumn, toTable, toColumn
        
        try:
             name = rel_def.get("name", f"Rel_{rel_def['fromTable']}_{rel_def['toTable']}")
             
             card_map = {
                 "manytoone": Cardinality.MANY_TO_ONE,
                 "onetoone": Cardinality.ONE_TO_ONE,
                 "onetomany": Cardinality.ONE_TO_MANY,
                 "manytomany": Cardinality.MANY_TO_MANY
             }
             raw_card = rel_def.get("cardinality", "ManyToOne").lower()
             
             return SMLRelationship(
                 unique_name=name,
                 from_dataset=rel_def["fromTable"],
                 from_columns=[rel_def["fromColumn"]],
                 to_dataset=rel_def["toTable"],
                 to_columns=[rel_def["toColumn"]],
                 cardinality=card_map.get(raw_card, Cardinality.MANY_TO_ONE),
                 is_active=rel_def.get("isActive", True)
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship: {e}")
            return None
