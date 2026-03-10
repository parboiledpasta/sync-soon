"""
Materialization Query Builder.

Constructs dynamic DAX SUMMARIZECOLUMNS queries based on the Triage Protocol
classification. Produces a single batch query that harvests Tier 1, 2, and 3
measures at the configured grain.
"""

from __future__ import annotations

from typing import Any, List

from semabridge.converter.measure_triage import (
    MaterializationStrategy,
    TriageResult,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MaterializationQueryBuilder:
    """Builds dynamic DAX queries for the Universal Sync Protocol.

    Usage::

        builder = MaterializationQueryBuilder()
        dax = builder.build_query(
            metrics=sml_model.metrics,
            triage_results=triage_results,
            grain_dimensions=["'Date'[Month]", "'Product'[Category]"],
        )
    """

    def build_query(
        self,
        metrics: List[Any],
        triage_results: dict[str, TriageResult],
        grain_dimensions: list[str],
    ) -> str:
        """Build a SUMMARIZECOLUMNS DAX query for all classifiable metrics.

        Args:
            metrics: List of SMLMetric or OSIMetric objects.
            triage_results: Dict of metric_name → TriageResult from MeasureTriage.
            grain_dimensions: Dimension columns defining the materialization
                grain (e.g., ["'Date'[Month]", "'Customer'[Region]"]).

        Returns:
            A complete DAX EVALUATE statement string.

        Raises:
            ValueError: If grain_dimensions is empty.
        """
        if not grain_dimensions:
            raise ValueError("grain_dimensions must contain at least one dimension")

        measure_lines: list[str] = []

        for metric in metrics:
            triage = triage_results.get(metric.unique_name)
            if triage is None:
                logger.warning(
                    f"Metric '{metric.unique_name}' has no triage result — skipping"
                )
                continue

            if metric.is_hidden and not metric.sync_enabled:
                continue

            lines = self._lines_for_metric(metric, triage)
            measure_lines.extend(lines)

        if not measure_lines:
            logger.warning("No measures produced — returning empty query")
            return ""

        dim_block = ",\n    ".join(grain_dimensions)
        measures_block = ",\n    ".join(measure_lines)

        query = (
            "EVALUATE\n"
            "SUMMARIZECOLUMNS(\n"
            f"    /* Grain Dimensions */\n"
            f"    {dim_block},\n"
            f"\n"
            f"    /* Materialized Measures */\n"
            f"    {measures_block}\n"
            ")"
        )

        logger.info(
            f"Built materialization query: "
            f"{len(grain_dimensions)} dims, {len(measure_lines)} measure cols"
        )
        return query

    def build_queries_partitioned(
        self,
        metrics: List[Any],
        triage_results: dict[str, TriageResult],
        grain_dimensions: list[str],
        max_measures_per_query: int = 20,
    ) -> list[str]:
        """Build multiple queries if there are too many measures for one call.

        The Execute Queries API has payload size limits.  This helper splits
        metrics into batches of *max_measures_per_query* and produces one
        query per batch.

        Args:
            metrics: Full list of metrics.
            triage_results: Triage results dict.
            grain_dimensions: Grain dimension columns.
            max_measures_per_query: Maximum column-producing measures per query.

        Returns:
            List of DAX query strings.
        """
        # Pre-filter to metrics that actually have triage results
        eligible = [
            m for m in metrics
            if m.unique_name in triage_results
            and not (m.is_hidden and not m.sync_enabled)
        ]

        queries: list[str] = []
        for i in range(0, len(eligible), max_measures_per_query):
            batch = eligible[i : i + max_measures_per_query]
            q = self.build_query(batch, triage_results, grain_dimensions)
            if q:
                queries.append(q)

        logger.info(
            f"Partitioned {len(eligible)} metrics into {len(queries)} DAX queries"
        )
        return queries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lines_for_metric(
        metric: Any, triage: TriageResult
    ) -> list[str]:
        """Generate SUMMARIZECOLUMNS measure lines for a single metric.

        Returns:
            List of '"ColumnAlias", <DAX Expression>' strings.
        """
        lines: list[str] = []
        safe_name = metric.unique_name.replace(" ", "_")

        if triage.strategy == MaterializationStrategy.PASSTHROUGH:
            # Tier 1 — single column
            lines.append(
                f'"{safe_name}", [{metric.unique_name}]'
            )

        elif triage.strategy == MaterializationStrategy.ALIGNED_HISTORY:
            # Tier 2 — base value + aligned companion columns
            lines.append(
                f'"{safe_name}", [{metric.unique_name}]'
            )
            for suffix, dax_expr in triage.aligned_measures.items():
                lines.append(
                    f'"{safe_name}{suffix}", {dax_expr}'
                )

        elif triage.strategy == MaterializationStrategy.DECOMPOSITION:
            if triage.components:
                # Tier 3 with decomposed numerator / denominator
                for suffix, ref in triage.components.items():
                    lines.append(
                        f'"{safe_name}{suffix}", {ref}'
                    )
            else:
                # Tier 3 without decomposition — materialise full value
                lines.append(
                    f'"{safe_name}", [{metric.unique_name}]'
                )

        return lines
