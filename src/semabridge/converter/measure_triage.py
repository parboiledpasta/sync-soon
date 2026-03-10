"""
Measure Triage Classifier.

Implements the \"Triage Protocol\" to classify each metric into a
materialization strategy for the Universal Sync Protocol:

    Tier 1 — Passthrough (Additive Primitives)
    Tier 2 — Aligned History (Time Intelligence / CALCULATE)
    Tier 3 — Decomposition (Non-Additive Ratios, DISTINCTCOUNT, IF/SWITCH)

Accepts both SMLMetric and OSIMetric objects (duck-typed on
unique_name, expression, is_hidden, sync_enabled).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Union

from semabridge.formats.sml.models import SMLMetric
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MaterializationStrategy(str, Enum):
    """Materialization strategy assigned by the Triage Protocol."""

    PASSTHROUGH = "passthrough"
    ALIGNED_HISTORY = "aligned_history"
    DECOMPOSITION = "decomposition"


@dataclass
class TriageResult:
    """Result of classifying a single metric.

    Attributes:
        metric_name: Unique name of the classified metric.
        tier: Integer tier (1, 2, or 3).
        strategy: The chosen MaterializationStrategy.
        aligned_measures: For Tier 2 — mapping of column suffix to DAX
            expression that must be evaluated alongside the base measure.
            Example: {"_LY": "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))"}
        components: For Tier 3 — mapping of component suffix to the DAX
            reference for numerator/denominator decomposition.
            Example: {"_Num": "[Total Sales]", "_Denom": "[Total Orders]"}
        reason: Human-readable explanation of the classification.
    """

    metric_name: str
    tier: int
    strategy: MaterializationStrategy
    aligned_measures: dict[str, str] = field(default_factory=dict)
    components: dict[str, str] = field(default_factory=dict)
    reason: str = ""


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Tier 1: Simple single-column aggregation without filter context
_TIER1_PATTERN = re.compile(
    r"^\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*"
    r"(?:'?[\w\s]+'?\[.+?\]|\[.+?\])"
    r"\s*\)\s*$",
    re.IGNORECASE,
)

# Time Intelligence functions that push a measure to Tier 2
_TIME_INTEL_FUNCTIONS: list[str] = [
    "TOTALYTD", "TOTALMTD", "TOTALQTD",
    "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
    "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
    "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR",
]

# Ratio / non‑additive indicators that push a measure to Tier 3
_TIER3_INDICATORS: list[str] = [
    "DIVIDE", "DISTINCTCOUNT", "IF(", "SWITCH(", "VAR ", "RETURN ",
]

# DIVIDE(A, B) pattern — used to extract numerator and denominator
_DIVIDE_PATTERN = re.compile(
    r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*(?:,.+?)?\)\s*$",
    re.IGNORECASE,
)

# Arithmetic ratio: [A] / [B]
_SIMPLE_RATIO_PATTERN = re.compile(
    r"^\s*(\[.+?\])\s*/\s*(\[.+?\])\s*$",
    re.IGNORECASE,
)


class MeasureTriage:
    """Classifies SML metrics into materialization tiers.

    Usage::

        triage = MeasureTriage()
        results = triage.classify_all(sml_model.metrics)
        for name, result in results.items():
            print(f"{name} → Tier {result.tier} ({result.strategy.value})")
    """

    def classify(self, metric: Any) -> TriageResult:
        """Classify a single metric into its materialization tier.

        Args:
            metric: An SMLMetric or OSIMetric with ``unique_name`` and
                ``expression`` attributes.

        Returns:
            A TriageResult with tier, strategy, and any extra metadata.
        """
        expression = (metric.expression or "").strip()
        upper_expr = expression.upper()

        # ------------------------------------------------------------------
        # Tier 1 — Additive Primitives
        # ------------------------------------------------------------------
        if _TIER1_PATTERN.match(expression):
            # Ensure no hidden time intel or CALCULATE lurking
            if not self._has_time_intel(upper_expr) and "CALCULATE" not in upper_expr:
                return TriageResult(
                    metric_name=metric.unique_name,
                    tier=1,
                    strategy=MaterializationStrategy.PASSTHROUGH,
                    reason="Simple additive aggregation over a single column",
                )

        # ------------------------------------------------------------------
        # Tier 2 — Context-Sensitive / Time Intelligence
        # ------------------------------------------------------------------
        if self._has_time_intel(upper_expr) or self._is_calculate_time(upper_expr):
            aligned = self._build_aligned_measures(metric, expression)
            return TriageResult(
                metric_name=metric.unique_name,
                tier=2,
                strategy=MaterializationStrategy.ALIGNED_HISTORY,
                aligned_measures=aligned,
                reason="Uses Time Intelligence or CALCULATE with temporal context",
            )

        # ------------------------------------------------------------------
        # Tier 3 — Non-Additive / Ratios / Complex
        # ------------------------------------------------------------------
        if self._has_tier3_indicator(upper_expr):
            components = self._decompose_ratio(expression)
            return TriageResult(
                metric_name=metric.unique_name,
                tier=3,
                strategy=MaterializationStrategy.DECOMPOSITION,
                components=components,
                reason="Non-additive expression requiring component materialization",
            )

        # ------------------------------------------------------------------
        # Default: treat unknowns as Tier 1 passthrough when they are plain
        # measure references (e.g. "[Other Metric]") or empty expressions.
        # ------------------------------------------------------------------
        if not expression or _TIER1_PATTERN.match(expression):
            return TriageResult(
                metric_name=metric.unique_name,
                tier=1,
                strategy=MaterializationStrategy.PASSTHROUGH,
                reason="Default classification (empty or simple expression)",
            )

        # Catch-all: if we can't determine, classify as Tier 3 to be safe
        # Still attempt ratio decomposition for [A] / [B] style expressions
        components = self._decompose_ratio(expression)
        return TriageResult(
            metric_name=metric.unique_name,
            tier=3,
            strategy=MaterializationStrategy.DECOMPOSITION,
            components=components,
            reason="Unrecognised DAX pattern — defaulting to safe decomposition",
        )

    def classify_all(
        self, metrics: List[Any]
    ) -> dict[str, TriageResult]:
        """Classify every metric in a list.

        Args:
            metrics: List of SMLMetric or OSIMetric objects.

        Returns:
            Dict mapping metric unique_name → TriageResult.
        """
        results: dict[str, TriageResult] = {}
        for metric in metrics:
            result = self.classify(metric)
            results[metric.unique_name] = result
            logger.debug(
                f"Triage: {metric.unique_name} → "
                f"Tier {result.tier} ({result.strategy.value})"
            )
        # Summary log
        tier_counts = {1: 0, 2: 0, 3: 0}
        for r in results.values():
            tier_counts[r.tier] = tier_counts.get(r.tier, 0) + 1
        logger.info(
            f"Triage complete: "
            f"Tier 1={tier_counts[1]}, "
            f"Tier 2={tier_counts[2]}, "
            f"Tier 3={tier_counts[3]}"
        )
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_time_intel(upper_expr: str) -> bool:
        """Check if expression contains any Time Intelligence function."""
        return any(func in upper_expr for func in _TIME_INTEL_FUNCTIONS)

    @staticmethod
    def _is_calculate_time(upper_expr: str) -> bool:
        """Detect CALCULATE wrapping a time-shifting filter context."""
        if "CALCULATE" not in upper_expr:
            return False
        # CALCULATE with a known time-shift filter inside
        return any(func in upper_expr for func in _TIME_INTEL_FUNCTIONS)

    @staticmethod
    def _has_tier3_indicator(upper_expr: str) -> bool:
        """Check for non-additive patterns."""
        return any(ind in upper_expr for ind in _TIER3_INDICATORS)

    @staticmethod
    def _build_aligned_measures(
        metric: Any, expression: str
    ) -> dict[str, str]:
        """Build the aligned-history companion columns for Tier 2.

        For a metric [Revenue] with SAMEPERIODLASTYEAR, produces:
            {"_LY": "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))"}

        The base measure itself is always materialized as-is (no suffix).
        """
        aligned: dict[str, str] = {}
        upper = expression.upper()

        if "SAMEPERIODLASTYEAR" in upper:
            aligned["_LY"] = (
                f"CALCULATE([{metric.unique_name}], "
                f"SAMEPERIODLASTYEAR('Date'[Date]))"
            )
        if "PREVIOUSYEAR" in upper:
            aligned["_PY"] = (
                f"CALCULATE([{metric.unique_name}], "
                f"PREVIOUSYEAR('Date'[Date]))"
            )
        if "PREVIOUSMONTH" in upper:
            aligned["_PM"] = (
                f"CALCULATE([{metric.unique_name}], "
                f"PREVIOUSMONTH('Date'[Date]))"
            )
        if "PREVIOUSQUARTER" in upper:
            aligned["_PQ"] = (
                f"CALCULATE([{metric.unique_name}], "
                f"PREVIOUSQUARTER('Date'[Date]))"
            )
        if "TOTALYTD" in upper:
            aligned["_YTD"] = (
                f"TOTALYTD([{metric.unique_name}], 'Date'[Date])"
            )
        if "TOTALMTD" in upper:
            aligned["_MTD"] = (
                f"TOTALMTD([{metric.unique_name}], 'Date'[Date])"
            )
        if "TOTALQTD" in upper:
            aligned["_QTD"] = (
                f"TOTALQTD([{metric.unique_name}], 'Date'[Date])"
            )

        return aligned

    @staticmethod
    def _decompose_ratio(expression: str) -> dict[str, str]:
        """Attempt to extract numerator and denominator from a ratio.

        Handles:
            DIVIDE([A], [B])
            [A] / [B]

        Returns:
            Dict with "_Num" and "_Denom" keys if decomposition succeeds,
            otherwise an empty dict.
        """
        # Try DIVIDE(A, B)
        match = _DIVIDE_PATTERN.match(expression)
        if match:
            return {"_Num": match.group(1), "_Denom": match.group(2)}

        # Try [A] / [B]
        match = _SIMPLE_RATIO_PATTERN.match(expression)
        if match:
            return {"_Num": match.group(1), "_Denom": match.group(2)}

        return {}
