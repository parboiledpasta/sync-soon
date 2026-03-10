"""
MetricFlow Serializer (Module 4).

Generates dbt-compatible MetricFlow YAML from OSI models, enabling
integration with dbt-snowflake's semantic layer.

Output files:

- ``semantic_models.yml`` — entities, measures, dimensions per dataset
- ``metrics.yml``         — derived metrics referencing measures

MetricFlow spec reference:
  https://docs.getdbt.com/docs/build/semantic-models

Usage::

    from semabridge.converter.metricflow_serializer import MetricFlowSerializer

    serializer = MetricFlowSerializer()
    files = serializer.serialize(osi_model)
    # files == {"semantic_models.yml": "...", "metrics.yml": "..."}
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import yaml

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Type Mappings
# =========================================================================

# OSI aggregation type → MetricFlow measure type
_MF_AGG_MAP = {
    "sum": "sum",
    "count": "count",
    "count_distinct": "count_distinct",
    "avg": "average",
    "min": "min",
    "max": "max",
    "none": "sum",  # fallback
}

# OSI data type → MetricFlow dimension type
_MF_DIM_TYPE_MAP = {
    "date": "time",
    "datetime": "time",
    "time": "time",
    "string": "categorical",
    "integer": "categorical",
    "decimal": "categorical",
    "float": "categorical",
    "boolean": "categorical",
    "binary": "categorical",
    "variant": "categorical",
    "unknown": "categorical",
}

# Time granularity defaults
_TIME_GRANULARITIES = {"day", "week", "month", "quarter", "year"}


# =========================================================================
# Helpers
# =========================================================================


def _safe_name(name: str) -> str:
    """Convert a display name to a MetricFlow-safe identifier."""
    if not name:
        return "unknown"
    s = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "unknown"


def _infer_time_granularity(col_name: str) -> str:
    """Best-effort inference of time_granularity from column name."""
    upper = col_name.upper()
    if "YEAR" in upper:
        return "year"
    if "QUARTER" in upper:
        return "quarter"
    if "MONTH" in upper:
        return "month"
    if "WEEK" in upper:
        return "week"
    return "day"


# =========================================================================
# MetricFlow Serializer
# =========================================================================


class MetricFlowSerializer:
    """Serialize an OSI model to dbt MetricFlow YAML.

    Produces two YAML documents:

    1. ``semantic_models.yml`` — one semantic model per OSI dataset
       with entities (from PKs/FKs), measures (from metrics), and
       dimensions (from columns).

    2. ``metrics.yml`` — one metric entry per OSI metric, referencing
       the measures declared in semantic_models.yml.
    """

    def __init__(
        self,
        *,
        default_schema: str = "SEMANTIC_LAYER",
        default_database: str = "ANALYTICS_DB",
    ) -> None:
        self.default_schema = default_schema
        self.default_database = default_database

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def serialize(self, osi_model: Any) -> Dict[str, str]:
        """Convert an OSI model to MetricFlow YAML files.

        Args:
            osi_model: An ``OSIModel`` instance.

        Returns:
            Dict mapping filename → YAML content string.
        """
        sem_models = self._build_semantic_models(osi_model)
        metrics = self._build_metrics(osi_model)

        result: Dict[str, str] = {}

        if sem_models:
            result["semantic_models.yml"] = yaml.dump(
                {"semantic_models": sem_models},
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        if metrics:
            result["metrics.yml"] = yaml.dump(
                {"metrics": metrics},
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

        return result

    def serialize_to_dir(
        self,
        osi_model: Any,
        output_dir: str,
    ) -> List[str]:
        """Serialize and write YAML files to a directory.

        Args:
            osi_model: OSI model.
            output_dir: Target directory path.

        Returns:
            List of written file paths.
        """
        from pathlib import Path

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        files = self.serialize(osi_model)
        written: List[str] = []
        for fname, content in files.items():
            fpath = out / fname
            fpath.write_text(content, encoding="utf-8")
            written.append(str(fpath))
            logger.info(f"Wrote MetricFlow YAML: {fpath}")

        return written

    # ------------------------------------------------------------------
    # Semantic Models Builder
    # ------------------------------------------------------------------

    def _build_semantic_models(self, osi_model: Any) -> List[Dict]:
        """Build MetricFlow semantic_models entries from OSI datasets."""
        semantic_models: List[Dict] = []
        model_name = _safe_name(
            getattr(osi_model, "unique_name", "") or "model"
        )

        # Build PK/FK index from relationships
        pk_columns, fk_columns = self._extract_entity_columns(osi_model)

        for ds in getattr(osi_model, "datasets", []):
            ds_name = _safe_name(ds.unique_name)
            source_table = getattr(ds, "source_table", None) or ds.unique_name
            safe_table = re.sub(r"[^A-Za-z0-9_]", "_", source_table).upper()

            sem_model: Dict[str, Any] = {
                "name": ds_name,
                "description": getattr(ds, "description", "") or f"Semantic model for {ds.unique_name}",
                "model": f"ref('{safe_table}')",
                "defaults": {
                    "agg_time_dimension": self._find_time_dimension(ds),
                },
            }

            # Entities
            entities = self._build_entities(ds, pk_columns, fk_columns)
            if entities:
                sem_model["entities"] = entities

            # Measures (metrics bound to this dataset)
            measures = self._build_measures(osi_model, ds)
            if measures:
                sem_model["measures"] = measures

            # Dimensions (non-PK, non-FK columns)
            dimensions = self._build_dimensions(ds, pk_columns, fk_columns)
            if dimensions:
                sem_model["dimensions"] = dimensions

            semantic_models.append(sem_model)

        return semantic_models

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def _extract_entity_columns(self, osi_model: Any):
        """Extract PK and FK column sets from relationships."""
        pk_cols: Dict[str, List[str]] = {}  # dataset → [pk_col, ...]
        fk_cols: Dict[str, List[str]] = {}  # dataset → [fk_col, ...]

        for rel in getattr(osi_model, "relationships", []):
            from_ds = getattr(rel, "from_dataset", "")
            to_ds = getattr(rel, "to_dataset", "")
            for col in getattr(rel, "from_columns", []):
                fk_cols.setdefault(from_ds, []).append(col)
            for col in getattr(rel, "to_columns", []):
                pk_cols.setdefault(to_ds, []).append(col)

        # Also include columns marked as is_key
        for ds in getattr(osi_model, "datasets", []):
            for col in getattr(ds, "columns", []):
                if getattr(col, "is_key", False):
                    pk_cols.setdefault(ds.unique_name, []).append(
                        col.unique_name
                    )

        return pk_cols, fk_cols

    def _build_entities(
        self, ds: Any, pk_cols: Dict, fk_cols: Dict
    ) -> List[Dict]:
        """Build MetricFlow entities for a dataset."""
        entities: List[Dict] = []
        ds_name = ds.unique_name

        # Primary keys
        for col_name in pk_cols.get(ds_name, []):
            entities.append({
                "name": _safe_name(col_name),
                "type": "primary",
                "expr": col_name.upper(),
            })

        # Foreign keys
        for col_name in fk_cols.get(ds_name, []):
            entities.append({
                "name": _safe_name(col_name),
                "type": "foreign",
                "expr": col_name.upper(),
            })

        # Deduplicate by name
        seen = set()
        unique_entities = []
        for e in entities:
            if e["name"] not in seen:
                seen.add(e["name"])
                unique_entities.append(e)

        return unique_entities

    # ------------------------------------------------------------------
    # Measures
    # ------------------------------------------------------------------

    def _build_measures(self, osi_model: Any, ds: Any) -> List[Dict]:
        """Build MetricFlow measures from metrics bound to this dataset."""
        measures: List[Dict] = []
        ds_name = ds.unique_name

        for metric in getattr(osi_model, "metrics", []):
            if getattr(metric, "dataset", "") != ds_name:
                continue

            agg_raw = str(
                getattr(metric, "aggregation", "sum")
            ).lower()
            # Handle enum values
            if hasattr(metric.aggregation, "value"):
                agg_raw = metric.aggregation.value.lower()

            mf_agg = _MF_AGG_MAP.get(agg_raw, "sum")
            src_col = getattr(metric, "source_column", None)

            measure: Dict[str, Any] = {
                "name": _safe_name(metric.unique_name),
                "agg": mf_agg,
                "description": getattr(metric, "description", "") or "",
            }
            if src_col:
                measure["expr"] = src_col.upper()

            # create_metric: true enables auto-metric creation in dbt
            measure["create_metric"] = True

            measures.append(measure)

        return measures

    # ------------------------------------------------------------------
    # Dimensions
    # ------------------------------------------------------------------

    def _build_dimensions(
        self, ds: Any, pk_cols: Dict, fk_cols: Dict
    ) -> List[Dict]:
        """Build MetricFlow dimensions from dataset columns."""
        dimensions: List[Dict] = []
        ds_name = ds.unique_name

        # Columns that are entities (PK/FK) — skip as dimensions
        entity_cols = set()
        for c in pk_cols.get(ds_name, []):
            entity_cols.add(c.upper())
        for c in fk_cols.get(ds_name, []):
            entity_cols.add(c.upper())

        for col in getattr(ds, "columns", []):
            col_name = col.unique_name
            if col_name.upper() in entity_cols:
                continue
            if col_name.startswith("RowNumber") or col_name.startswith("_"):
                continue

            # Check if it's a calculated column — skip
            src_expr = getattr(col, "source_expression", None)
            if src_expr and not self._is_physical(src_expr):
                continue

            # Determine dimension type
            data_type = str(
                getattr(col, "data_type", "unknown")
            ).lower()
            if hasattr(col, "data_type") and hasattr(col.data_type, "value"):
                data_type = col.data_type.value.lower()

            mf_type = _MF_DIM_TYPE_MAP.get(data_type, "categorical")

            dim: Dict[str, Any] = {
                "name": _safe_name(col_name),
                "type": mf_type,
            }

            if mf_type == "time":
                dim["type_params"] = {
                    "time_granularity": _infer_time_granularity(col_name),
                }

            dimensions.append(dim)

        return dimensions

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _build_metrics(self, osi_model: Any) -> List[Dict]:
        """Build top-level MetricFlow metrics from OSI metrics."""
        metrics: List[Dict] = []

        for metric in getattr(osi_model, "metrics", []):
            safe = _safe_name(metric.unique_name)

            # Check if there's a sql_expression (derived metric)
            has_expr = bool(
                getattr(metric, "sql_expression", None)
                or getattr(metric, "expression", None)
            )
            src_col = getattr(metric, "source_column", None)

            if has_expr and not src_col:
                # Derived metric
                m: Dict[str, Any] = {
                    "name": safe,
                    "type": "derived",
                    "label": getattr(metric, "label", "") or metric.unique_name,
                    "description": getattr(metric, "description", "") or "",
                }
                # For derived metrics, we'd need to parse the expression
                # into MetricFlow's type_params.expr format. For now,
                # emit a placeholder that references the raw SQL.
                raw_expr = (
                    getattr(metric, "sql_expression", "")
                    or getattr(metric, "expression", "")
                )
                m["type_params"] = {
                    "expr": raw_expr or safe,
                }
            else:
                # Simple metric — references a measure
                m = {
                    "name": safe,
                    "type": "simple",
                    "label": getattr(metric, "label", "") or metric.unique_name,
                    "description": getattr(metric, "description", "") or "",
                    "type_params": {
                        "measure": safe,
                    },
                }

            metrics.append(m)

        return metrics

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_time_dimension(ds: Any) -> str:
        """Find a suitable default time dimension column in a dataset."""
        for col in getattr(ds, "columns", []):
            dt = str(getattr(col, "data_type", "")).lower()
            if hasattr(col, "data_type") and hasattr(col.data_type, "value"):
                dt = col.data_type.value.lower()
            if dt in ("date", "datetime"):
                return _safe_name(col.unique_name)

        # Fallback: look for common time column names
        for col in getattr(ds, "columns", []):
            name_upper = col.unique_name.upper()
            if any(
                t in name_upper
                for t in ("DATE", "TIMESTAMP", "CREATED", "UPDATED", "TIME")
            ):
                return _safe_name(col.unique_name)

        return "ds__default_time"

    @staticmethod
    def _is_physical(source_expression: str) -> bool:
        """Check if a source_expression is a physical column reference."""
        if not source_expression:
            return True
        expr = source_expression.strip()
        if not expr:
            return True
        if re.search(r"[()[\]+\-*/=<>!&|,\n]", expr):
            return False
        return True
