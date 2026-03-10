"""
Tiered Safety DAX Classification Engine.

Implements the formal "Tiered Safety" translation taxonomy for DAX-to-Snowflake
migration, classifying measures into four safety tiers and five hazard categories:

    Tier 1 — Deterministic Translation (Fully Automated)
    Tier 2 — Conditional Equivalency (Partially Automated)
    Tier 3 — Contextual Dissonance (Manual Override Required)
    Tier 4 — Structural Hazard (Manual Override Required)

Hazard Categories (Tier 3 & 4):
    Category 1 — Complex Filter Contexts (CALCULATE + FILTER, ALL, ALLEXCEPT)
    Category 2 — Row Context Iterators (SUMX, AVERAGEX, MINX, MAXX + FILTER)
    Category 3 — Advanced Time Intelligence (PARALLELPERIOD, DATEADD, SAMEPERIODLASTYEAR)
    Category 4 — Dynamic Relationship Modifiers (USERELATIONSHIP, CROSSFILTER)
    Category 5 — Specialized Built-in Logic (RANKX, EARLIER, nested iterations)
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    Future,
)
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SafetyTier(int, Enum):
    """Translation safety tier classification."""
    TIER_1_DETERMINISTIC = 1
    TIER_2_CONDITIONAL = 2
    TIER_3_CONTEXTUAL_DISSONANCE = 3
    TIER_4_STRUCTURAL_HAZARD = 4


class HazardCategory(str, Enum):
    """Categories of unsupported DAX patterns requiring manual override."""
    NONE = "none"
    COMPLEX_FILTER_CONTEXT = "complex_filter_context"
    ROW_CONTEXT_ITERATOR = "row_context_iterator"
    ADVANCED_TIME_INTELLIGENCE = "advanced_time_intelligence"
    DYNAMIC_RELATIONSHIP_MODIFIER = "dynamic_relationship_modifier"
    SPECIALIZED_BUILTIN_LOGIC = "specialized_builtin_logic"


class OverrideLayerType(str, Enum):
    """Types of SQL override layers."""
    DYNAMIC_TABLE = "dynamic_table"
    SEMANTIC_VIEW = "semantic_view"
    CORTEX_METADATA = "cortex_metadata"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HazardDetection:
    """A single hazard detected within a DAX expression."""
    category: HazardCategory
    function_name: str
    pattern_matched: str
    description: str
    severity: str = "high"  # "high" | "critical"


@dataclass
class SafetyClassification:
    """Complete safety classification result for a DAX measure.

    Attributes:
        metric_name: Name of the classified metric.
        tier: The assigned safety tier (1-4).
        hazards: List of detected hazard patterns.
        primary_hazard: The highest-severity hazard category.
        is_automatable: Whether the measure can be auto-translated.
        requires_override: Whether a manual SQL override is needed.
        override_layers: Recommended SQL override layers.
        original_dax: The original DAX expression.
        recommendation: Human-readable override recommendation.
        estimated_complexity: Estimated complexity score (1-10).
    """
    metric_name: str
    tier: SafetyTier
    hazards: List[HazardDetection] = field(default_factory=list)
    primary_hazard: HazardCategory = HazardCategory.NONE
    is_automatable: bool = True
    requires_override: bool = False
    override_layers: List[OverrideLayerType] = field(default_factory=list)
    original_dax: str = ""
    recommendation: str = ""
    estimated_complexity: int = 1


# ---------------------------------------------------------------------------
# Detection Patterns
# ---------------------------------------------------------------------------

# Category 1: Complex Filter Contexts
_CATEGORY_1_PATTERNS: List[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"CALCULATE\s*\(.+,\s*FILTER\s*\(", re.IGNORECASE | re.DOTALL),
        "CALCULATE+FILTER",
        "CALCULATE with nested FILTER table expression — opaque filter context "
        "cannot be mapped to static SQL WHERE/CASE expressions.",
    ),
    (
        re.compile(r"CALCULATE\s*\(.+,\s*ALL\s*\(", re.IGNORECASE | re.DOTALL),
        "CALCULATE+ALL",
        "CALCULATE with ALL() — removes all filters from the specified table/columns, "
        "behavior depends on runtime dashboard filter context.",
    ),
    (
        re.compile(r"CALCULATE\s*\(.+,\s*ALLEXCEPT\s*\(", re.IGNORECASE | re.DOTALL),
        "CALCULATE+ALLEXCEPT",
        "CALCULATE with ALLEXCEPT — selectively preserves filters, "
        "dynamic behavior incompatible with static SQL views.",
    ),
    (
        re.compile(r"CALCULATE\s*\(.+,\s*ALLSELECTED\s*\(", re.IGNORECASE | re.DOTALL),
        "CALCULATE+ALLSELECTED",
        "CALCULATE with ALLSELECTED — returns the set of values visible in the "
        "current visual context, no SQL equivalent.",
    ),
    (
        re.compile(r"CALCULATE\s*\(.+,\s*KEEPFILTERS\s*\(", re.IGNORECASE | re.DOTALL),
        "CALCULATE+KEEPFILTERS",
        "CALCULATE with KEEPFILTERS — intersects rather than replaces filter context, "
        "requires deep context-awareness not available in SQL views.",
    ),
]

# Category 2: Row Context Iterators
_CATEGORY_2_PATTERNS: List[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"SUMX\s*\(\s*FILTER\s*\(", re.IGNORECASE),
        "SUMX+FILTER",
        "SUMX iterating over a filtered table — row-by-row evaluation with "
        "conditional filtering generates correlated subqueries in SQL.",
    ),
    (
        re.compile(r"SUMX\s*\(", re.IGNORECASE),
        "SUMX",
        "SUMX row iterator — evaluates expression per row then aggregates. "
        "May require pre-aggregation via Dynamic Table.",
    ),
    (
        re.compile(r"AVERAGEX\s*\(", re.IGNORECASE),
        "AVERAGEX",
        "AVERAGEX row iterator — per-row expression evaluation with averaging.",
    ),
    (
        re.compile(r"MINX\s*\(", re.IGNORECASE),
        "MINX",
        "MINX row iterator — per-row expression evaluation with MIN aggregation.",
    ),
    (
        re.compile(r"MAXX\s*\(", re.IGNORECASE),
        "MAXX",
        "MAXX row iterator — per-row expression evaluation with MAX aggregation.",
    ),
    (
        re.compile(r"COUNTX\s*\(", re.IGNORECASE),
        "COUNTX",
        "COUNTX row iterator — per-row conditional counting.",
    ),
    (
        re.compile(r"CONCATENATEX\s*\(", re.IGNORECASE),
        "CONCATENATEX",
        "CONCATENATEX row iterator — string concatenation across rows with "
        "row-context-dependent ordering.",
    ),
    (
        re.compile(r"ADDCOLUMNS\s*\(", re.IGNORECASE),
        "ADDCOLUMNS",
        "ADDCOLUMNS — dynamically adds computed columns requiring row context.",
    ),
]

# Category 3: Advanced Time Intelligence
_CATEGORY_3_PATTERNS: List[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"PARALLELPERIOD\s*\(", re.IGNORECASE),
        "PARALLELPERIOD",
        "PARALLELPERIOD — shifts date range by a specified interval. "
        "Requires complex calendar joins in Snowflake.",
    ),
    (
        re.compile(r"DATEADD\s*\(\s*'", re.IGNORECASE),
        "DATEADD",
        "DAX DATEADD — context-aware date shifting. Not equivalent to "
        "Snowflake's scalar DATEADD function.",
    ),
    (
        re.compile(r"SAMEPERIODLASTYEAR\s*\(", re.IGNORECASE),
        "SAMEPERIODLASTYEAR",
        "SAMEPERIODLASTYEAR — dynamically resolves prior year dates based on "
        "the current visual filter context.",
    ),
    (
        re.compile(r"OPENINGBALANCEYEAR\s*\(", re.IGNORECASE),
        "OPENINGBALANCEYEAR",
        "OPENINGBALANCEYEAR — retrieves the first value in a fiscal year, "
        "requires materialized calendar alignment.",
    ),
    (
        re.compile(r"CLOSINGBALANCEYEAR\s*\(", re.IGNORECASE),
        "CLOSINGBALANCEYEAR",
        "CLOSINGBALANCEYEAR — retrieves the last value in a fiscal year, "
        "requires materialized calendar alignment.",
    ),
    (
        re.compile(r"PREVIOUSYEAR\s*\(", re.IGNORECASE),
        "PREVIOUSYEAR",
        "PREVIOUSYEAR — returns prior year date set, "
        "requires calendar-joined self-referencing view.",
    ),
    (
        re.compile(r"PREVIOUSMONTH\s*\(", re.IGNORECASE),
        "PREVIOUSMONTH",
        "PREVIOUSMONTH — returns prior month date set.",
    ),
    (
        re.compile(r"PREVIOUSQUARTER\s*\(", re.IGNORECASE),
        "PREVIOUSQUARTER",
        "PREVIOUSQUARTER — returns prior quarter date set.",
    ),
    (
        re.compile(r"TOTALYTD\s*\(", re.IGNORECASE),
        "TOTALYTD",
        "TOTALYTD — accumulates year-to-date values using calendar context. "
        "Can be translated to running SUM window function.",
    ),
    (
        re.compile(r"TOTALMTD\s*\(", re.IGNORECASE),
        "TOTALMTD",
        "TOTALMTD — accumulates month-to-date values using calendar context. "
        "Can be translated to running SUM window function.",
    ),
    (
        re.compile(r"TOTALQTD\s*\(", re.IGNORECASE),
        "TOTALQTD",
        "TOTALQTD — accumulates quarter-to-date values using calendar context. "
        "Can be translated to running SUM window function.",
    ),
]

# Category 4: Dynamic Relationship Modifiers
_CATEGORY_4_PATTERNS: List[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"USERELATIONSHIP\s*\(", re.IGNORECASE),
        "USERELATIONSHIP",
        "USERELATIONSHIP — temporarily activates an inactive relationship path. "
        "Snowflake Semantic Views require statically defined join paths.",
    ),
    (
        re.compile(r"CROSSFILTER\s*\(", re.IGNORECASE),
        "CROSSFILTER",
        "CROSSFILTER — dynamically changes cross-filter direction at runtime. "
        "No static SQL equivalent exists.",
    ),
    (
        re.compile(r"TREATAS\s*\(", re.IGNORECASE),
        "TREATAS",
        "TREATAS — applies virtual relationship mapping without physical joins. "
        "Requires explicit JOIN reconstruction in Snowflake.",
    ),
]

# Category 5: Specialized Built-in Logic & Nested Iterations
_CATEGORY_5_PATTERNS: List[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"RANKX\s*\(", re.IGNORECASE),
        "RANKX",
        "RANKX — context-dependent ranking with dynamic partition boundaries. "
        "Must be rebuilt using RANK()/ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...).",
    ),
    (
        re.compile(r"EARLIER\s*\(", re.IGNORECASE),
        "EARLIER",
        "EARLIER — references outer row context in nested iterations. "
        "SQL has no lexical equivalent; requires complete restructuring via "
        "window functions.",
    ),
    (
        re.compile(r"EARLIEST\s*\(", re.IGNORECASE),
        "EARLIEST",
        "EARLIEST — references the outermost row context in deeply nested loops.",
    ),
    (
        re.compile(r"TOPN\s*\(", re.IGNORECASE),
        "TOPN",
        "TOPN — returns top N rows based on dynamic context evaluation.",
    ),
    (
        re.compile(r"GENERATE\s*\(", re.IGNORECASE),
        "GENERATE",
        "GENERATE — cross-join iterator with row context evaluation per row.",
    ),
    (
        re.compile(r"GENERATESERIES\s*\(", re.IGNORECASE),
        "GENERATESERIES",
        "GENERATESERIES — generates a synthetic table requiring iterative evaluation.",
    ),
    (
        re.compile(r"PATH\s*\(", re.IGNORECASE),
        "PATH",
        "PATH — parent-child hierarchy traversal via recursive row context.",
    ),
    (
        re.compile(r"PATHITEM\s*\(", re.IGNORECASE),
        "PATHITEM",
        "PATHITEM — extracts items from a PATH result in nested row context.",
    ),
]

# Tier 1: Safe deterministic patterns
_TIER_1_PATTERN = re.compile(
    r"^\s*(?:SUM|AVERAGE|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(\s*"
    r"(?:'?[\w\s]+'?\[[^\]]+\]|\[[^\]]+\])"
    r"\s*\)\s*$",
    re.IGNORECASE,
)

# Tier 2: Conditional equivalency indicators
_TIER_2_INDICATORS = [
    "DIVIDE", "IF(", "SWITCH(",
]

_TIER_2_TIME_INTEL_SAFE = [
    "TOTALYTD", "TOTALMTD", "TOTALQTD",
]

# Mapping from category to severity assessment
_CATEGORY_SEVERITY = {
    HazardCategory.COMPLEX_FILTER_CONTEXT: "high",
    HazardCategory.ROW_CONTEXT_ITERATOR: "high",
    HazardCategory.ADVANCED_TIME_INTELLIGENCE: "high",
    HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER: "critical",
    HazardCategory.SPECIALIZED_BUILTIN_LOGIC: "critical",
}

# Mapping from category to recommended override layers
_CATEGORY_OVERRIDE_LAYERS = {
    HazardCategory.COMPLEX_FILTER_CONTEXT: [
        OverrideLayerType.DYNAMIC_TABLE,
        OverrideLayerType.SEMANTIC_VIEW,
        OverrideLayerType.CORTEX_METADATA,
    ],
    HazardCategory.ROW_CONTEXT_ITERATOR: [
        OverrideLayerType.DYNAMIC_TABLE,
        OverrideLayerType.SEMANTIC_VIEW,
        OverrideLayerType.CORTEX_METADATA,
    ],
    HazardCategory.ADVANCED_TIME_INTELLIGENCE: [
        OverrideLayerType.DYNAMIC_TABLE,
        OverrideLayerType.SEMANTIC_VIEW,
        OverrideLayerType.CORTEX_METADATA,
    ],
    HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER: [
        OverrideLayerType.SEMANTIC_VIEW,
        OverrideLayerType.CORTEX_METADATA,
    ],
    HazardCategory.SPECIALIZED_BUILTIN_LOGIC: [
        OverrideLayerType.DYNAMIC_TABLE,
        OverrideLayerType.SEMANTIC_VIEW,
        OverrideLayerType.CORTEX_METADATA,
    ],
}


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class TieredSafetyClassifier:
    """Classifies DAX measures into the four-tier safety taxonomy.

    This classifier scans DAX expressions for hazardous patterns across
    five hazard categories and assigns the appropriate safety tier. For
    Tier 3 and Tier 4 measures, it generates detailed metadata required
    to produce Manual SQL Override files.

    Usage::

        classifier = TieredSafetyClassifier()
        result = classifier.classify("Total Sales", dax_expression)
        if result.requires_override:
            print(f"Override required: {result.primary_hazard}")
    """

    # All detection pattern sets organized by category
    _PATTERN_REGISTRY: Dict[HazardCategory, List[tuple]] = {
        HazardCategory.COMPLEX_FILTER_CONTEXT: _CATEGORY_1_PATTERNS,
        HazardCategory.ROW_CONTEXT_ITERATOR: _CATEGORY_2_PATTERNS,
        HazardCategory.ADVANCED_TIME_INTELLIGENCE: _CATEGORY_3_PATTERNS,
        HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER: _CATEGORY_4_PATTERNS,
        HazardCategory.SPECIALIZED_BUILTIN_LOGIC: _CATEGORY_5_PATTERNS,
    }

    # Categories that mandate Tier 4 (structural hazard)
    _TIER_4_CATEGORIES = {
        HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER,
        HazardCategory.SPECIALIZED_BUILTIN_LOGIC,
    }

    def classify(self, metric_name: str, dax: str) -> SafetyClassification:
        """Classify a single DAX expression into its safety tier.

        Args:
            metric_name: Name of the metric being classified.
            dax: The DAX formula string.

        Returns:
            A SafetyClassification with tier, hazards, and override metadata.
        """
        if not dax or not dax.strip():
            return SafetyClassification(
                metric_name=metric_name,
                tier=SafetyTier.TIER_1_DETERMINISTIC,
                original_dax="",
                recommendation="Empty expression — no translation needed.",
            )

        clean_dax = dax.strip()
        upper_dax = clean_dax.upper()

        # Scan all hazard categories
        hazards = self._detect_hazards(clean_dax)

        if not hazards:
            # No hazards detected — classify as Tier 1 or Tier 2
            tier, recommendation = self._classify_safe_expression(clean_dax, upper_dax)
            return SafetyClassification(
                metric_name=metric_name,
                tier=tier,
                is_automatable=True,
                requires_override=False,
                original_dax=clean_dax,
                recommendation=recommendation,
                estimated_complexity=1 if tier == SafetyTier.TIER_1_DETERMINISTIC else 3,
            )

        # Determine highest tier based on hazard categories
        has_tier_4 = any(
            h.category in self._TIER_4_CATEGORIES for h in hazards
        )
        tier = (
            SafetyTier.TIER_4_STRUCTURAL_HAZARD
            if has_tier_4
            else SafetyTier.TIER_3_CONTEXTUAL_DISSONANCE
        )

        # Check if advanced time intel can still be partially automated
        if (
            len(hazards) == 1
            and hazards[0].category == HazardCategory.ADVANCED_TIME_INTELLIGENCE
            and any(func in upper_dax for func in _TIER_2_TIME_INTEL_SAFE)
        ):
            # Standard PTD functions are borderline — classify as Tier 2
            return SafetyClassification(
                metric_name=metric_name,
                tier=SafetyTier.TIER_2_CONDITIONAL,
                hazards=hazards,
                primary_hazard=hazards[0].category,
                is_automatable=True,
                requires_override=False,
                original_dax=clean_dax,
                recommendation=(
                    "Standard Period-To-Date function — can be translated "
                    "to Snowflake window function."
                ),
                estimated_complexity=4,
            )

        # Determine primary hazard (highest severity)
        primary = self._pick_primary_hazard(hazards)
        override_layers = _CATEGORY_OVERRIDE_LAYERS.get(primary, [])
        complexity = self._estimate_complexity(hazards, clean_dax)

        return SafetyClassification(
            metric_name=metric_name,
            tier=tier,
            hazards=hazards,
            primary_hazard=primary,
            is_automatable=False,
            requires_override=True,
            override_layers=list(override_layers),
            original_dax=clean_dax,
            recommendation=self._generate_recommendation(primary, hazards),
            estimated_complexity=complexity,
        )

    def classify_all(
        self,
        measures: Dict[str, str],
        max_workers: Optional[int] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, SafetyClassification]:
        """Classify a batch of DAX measures, optionally in parallel.

        When ``max_workers`` is ``None`` (default) or 1, classification runs
        sequentially in the calling thread.  When > 1 the measures are
        distributed across a :class:`~concurrent.futures.ThreadPoolExecutor`
        for parallel regex scanning.  The ``classify`` method is stateless
        and thread-safe, so no locking is required.

        Args:
            measures: Dict mapping metric_name -> DAX expression.
            max_workers: Number of threads for parallel classification.
                ``None`` or 1 = sequential; 0 = auto (CPU count).
            progress_callback: Optional ``(completed, total, metric_name)``
                callback invoked after each classification finishes.

        Returns:
            Dict mapping metric_name -> SafetyClassification.
        """
        if not measures:
            return {}

        effective_workers = self._resolve_workers(max_workers, len(measures))

        if effective_workers <= 1:
            # Fast-path: sequential (avoids thread-pool overhead for small batches)
            results = self._classify_sequential(measures, progress_callback)
        else:
            results = self._classify_parallel(
                measures, effective_workers, progress_callback
            )

        self._log_summary(results)
        return results

    # ------------------------------------------------------------------
    # Concurrency helpers
    # ------------------------------------------------------------------

    # Minimum batch size before parallel execution is worthwhile.
    # Below this threshold the thread-pool overhead exceeds any gain.
    _PARALLEL_THRESHOLD = 50

    # Hard cap on worker threads to avoid CPU saturation.
    _MAX_WORKER_CAP = 4

    @classmethod
    def _resolve_workers(cls, max_workers: Optional[int], batch_size: int) -> int:
        """Determine the effective thread-pool size.

        * ``None`` or 1 → sequential.
        * 0 → auto-detect: sequential if ``batch_size`` <
          ``_PARALLEL_THRESHOLD``, else ``min(cpu_count-1, _MAX_WORKER_CAP)``.
        * >1 → use as-is, capped to ``min(batch_size, _MAX_WORKER_CAP)``.
        """
        if max_workers is None or max_workers == 1:
            return 1
        if max_workers == 0:
            # Only spin up threads when the batch justifies it
            if batch_size < cls._PARALLEL_THRESHOLD:
                return 1
            return min(
                max(2, (os.cpu_count() or 2) - 1),
                batch_size,
                cls._MAX_WORKER_CAP,
            )
        return min(max_workers, batch_size, cls._MAX_WORKER_CAP)

    def _classify_sequential(
        self,
        measures: Dict[str, str],
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, SafetyClassification]:
        """Classify measures sequentially (single-threaded)."""
        results: Dict[str, SafetyClassification] = {}
        total = len(measures)
        for idx, (name, dax) in enumerate(measures.items(), 1):
            result = self.classify(name, dax)
            results[name] = result
            logger.debug(
                f"Safety: {name} → Tier {result.tier.value} "
                f"({'OVERRIDE' if result.requires_override else 'AUTO'})"
            )
            if progress_callback:
                progress_callback(idx, total, name)
        return results

    def _classify_parallel(
        self,
        measures: Dict[str, str],
        max_workers: int,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, SafetyClassification]:
        """Classify measures concurrently using a thread pool.

        Each ``classify()`` call is stateless and thread-safe — it only
        reads from immutable compiled regex patterns and returns a new
        dataclass instance.
        """
        results: Dict[str, SafetyClassification] = {}
        errors: Dict[str, str] = {}
        total = len(measures)
        completed = 0

        logger.info(
            f"Starting parallel classification: {total} measures, "
            f"{max_workers} workers"
        )
        start = time.monotonic()

        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="safety-classify",
        ) as executor:
            future_to_name: Dict[Future, str] = {
                executor.submit(self.classify, name, dax): name
                for name, dax in measures.items()
            }

            for future in as_completed(future_to_name):
                name = future_to_name[future]
                completed += 1
                try:
                    result = future.result()
                    results[name] = result
                    logger.debug(
                        f"Safety [{completed}/{total}]: {name} → "
                        f"Tier {result.tier.value} "
                        f"({'OVERRIDE' if result.requires_override else 'AUTO'})"
                    )
                except Exception as exc:
                    # Record the failure but don't abort the batch
                    error_msg = f"{type(exc).__name__}: {exc}"
                    errors[name] = error_msg
                    logger.error(
                        f"Safety [{completed}/{total}]: {name} FAILED — {error_msg}"
                    )
                    # Insert a fallback classification so callers always
                    # get a result for every input metric.
                    results[name] = SafetyClassification(
                        metric_name=name,
                        tier=SafetyTier.TIER_4_STRUCTURAL_HAZARD,
                        is_automatable=False,
                        requires_override=True,
                        recommendation=(
                            f"Classification failed ({error_msg}). "
                            f"Defaulting to Tier 4 — manual review required."
                        ),
                    )

                if progress_callback:
                    progress_callback(completed, total, name)

        elapsed = time.monotonic() - start
        logger.info(
            f"Parallel classification complete in {elapsed:.2f}s "
            f"({total} measures, {max_workers} workers"
            f"{f', {len(errors)} errors' if errors else ''})"
        )
        return results

    @staticmethod
    def _log_summary(results: Dict[str, SafetyClassification]) -> None:
        """Emit a summary log line for a batch of results."""
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        override_count = 0
        for r in results.values():
            tier_counts[r.tier.value] = tier_counts.get(r.tier.value, 0) + 1
            if r.requires_override:
                override_count += 1

        logger.info(
            f"Tiered Safety classification complete: "
            f"T1={tier_counts[1]}, T2={tier_counts[2]}, "
            f"T3={tier_counts[3]}, T4={tier_counts[4]} | "
            f"Overrides required: {override_count}"
        )


    def get_summary(
        self, results: Dict[str, SafetyClassification]
    ) -> Dict:
        """Generate a summary report of safety classification results.

        Returns:
            Dict with tier counts, hazard breakdown, and override metadata.
        """
        tier_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        hazard_counts: Dict[str, int] = {}
        override_measures: List[str] = []

        for name, r in results.items():
            tier_counts[r.tier.value] += 1
            if r.requires_override:
                override_measures.append(name)
            for h in r.hazards:
                key = h.category.value
                hazard_counts[key] = hazard_counts.get(key, 0) + 1

        return {
            "total_measures": len(results),
            "tier_distribution": tier_counts,
            "automatable": tier_counts[1] + tier_counts[2],
            "requires_override": tier_counts[3] + tier_counts[4],
            "hazard_breakdown": hazard_counts,
            "override_measures": override_measures,
            "automation_rate": round(
                (tier_counts[1] + tier_counts[2]) / max(len(results), 1) * 100,
                1,
            ),
        }

    # ------------------------------------------------------------------
    # Private detection helpers
    # ------------------------------------------------------------------

    def _detect_hazards(self, dax: str) -> List[HazardDetection]:
        """Scan a DAX expression for all hazardous patterns."""
        hazards: List[HazardDetection] = []
        seen_functions: set[str] = set()

        for category, patterns in self._PATTERN_REGISTRY.items():
            for pattern, func_name, description in patterns:
                if func_name in seen_functions:
                    continue
                if pattern.search(dax):
                    hazards.append(
                        HazardDetection(
                            category=category,
                            function_name=func_name,
                            pattern_matched=pattern.pattern,
                            description=description,
                            severity=_CATEGORY_SEVERITY.get(category, "high"),
                        )
                    )
                    seen_functions.add(func_name)

        return hazards

    def _classify_safe_expression(
        self, dax: str, upper_dax: str
    ) -> tuple[SafetyTier, str]:
        """Classify an expression with no hazards as Tier 1 or Tier 2."""
        # Tier 1: Direct aggregation
        if _TIER_1_PATTERN.match(dax):
            return (
                SafetyTier.TIER_1_DETERMINISTIC,
                "Simple aggregation — maps directly to SQL.",
            )

        # Tier 2 indicators
        for indicator in _TIER_2_INDICATORS:
            if indicator in upper_dax:
                return (
                    SafetyTier.TIER_2_CONDITIONAL,
                    f"Conditional expression ({indicator.rstrip('(')}) — "
                    f"requires parameter mapping.",
                )

        # Check for simple arithmetic / measure references
        if re.match(r"^\s*\[.+?\]\s*[\+\-\*\/]\s*\[.+?\]\s*$", dax):
            return (
                SafetyTier.TIER_2_CONDITIONAL,
                "Simple arithmetic between measures — requires dependency resolution.",
            )

        # Bare measure reference: [MeasureName]
        if re.match(r"^\s*\[.+?\]\s*$", dax):
            return (
                SafetyTier.TIER_1_DETERMINISTIC,
                "Direct measure reference.",
            )

        # Default: if no hazard detected but also not recognized, Tier 2 (safe side)
        return (
            SafetyTier.TIER_2_CONDITIONAL,
            "Unrecognized but hazard-free expression — classified as conditional.",
        )

    @staticmethod
    def _pick_primary_hazard(hazards: List[HazardDetection]) -> HazardCategory:
        """Select the highest-severity hazard as primary."""
        # Priority order: Cat5 > Cat4 > Cat1 > Cat2 > Cat3
        priority = [
            HazardCategory.SPECIALIZED_BUILTIN_LOGIC,
            HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER,
            HazardCategory.COMPLEX_FILTER_CONTEXT,
            HazardCategory.ROW_CONTEXT_ITERATOR,
            HazardCategory.ADVANCED_TIME_INTELLIGENCE,
        ]
        for cat in priority:
            if any(h.category == cat for h in hazards):
                return cat
        return hazards[0].category if hazards else HazardCategory.NONE

    @staticmethod
    def _estimate_complexity(hazards: List[HazardDetection], dax: str) -> int:
        """Estimate override complexity on a 1-10 scale."""
        base = len(hazards) * 2
        # Nesting depth adds complexity
        nesting = dax.count("(") - dax.count(")")
        nesting = max(0, dax.count("("))
        depth_score = min(nesting // 3, 3)
        # Specific high-complexity functions
        if any(h.function_name in ("EARLIER", "RANKX", "GENERATE") for h in hazards):
            base += 2
        return min(base + depth_score, 10)

    @staticmethod
    def _generate_recommendation(
        primary: HazardCategory,
        hazards: List[HazardDetection],
    ) -> str:
        """Generate a human-readable override recommendation."""
        recommendations = {
            HazardCategory.COMPLEX_FILTER_CONTEXT: (
                "Create a Dynamic Table to pre-compute the filtered aggregation. "
                "Define explicit WHERE/CASE clauses in the override SQL. "
                "Expose the result through a Semantic View METRICS clause."
            ),
            HazardCategory.ROW_CONTEXT_ITERATOR: (
                "Pre-aggregate the row-level computation in a Dynamic Table using "
                "standard SQL expressions. Avoid correlated subqueries. "
                "Consider Snowflake UDFs for complex per-row logic."
            ),
            HazardCategory.ADVANCED_TIME_INTELLIGENCE: (
                "Build a materialized calendar dimension with temporal offset columns. "
                "Join the fact table to the calendar using engineered date keys. "
                "Express the time-shifted metric as a standard aggregation over the joined view."
            ),
            HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER: (
                "Create physical table aliases (e.g., OrderDate_Dim, ShipDate_Dim) "
                "within the Semantic View definition. Define separate static JOIN paths "
                "for each relationship variant."
            ),
            HazardCategory.SPECIALIZED_BUILTIN_LOGIC: (
                "Dismantle the DAX logic entirely. Rebuild using Snowflake analytical "
                "window functions (RANK(), ROW_NUMBER(), DENSE_RANK()) with explicit "
                "PARTITION BY and ORDER BY clauses in a Dynamic Table."
            ),
        }
        base = recommendations.get(primary, "Manual review required.")
        if len(hazards) > 1:
            cats = {h.category.value for h in hazards}
            base += f" Note: {len(hazards)} hazards detected across categories: {', '.join(cats)}."
        return base
