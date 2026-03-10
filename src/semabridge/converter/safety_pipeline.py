"""
Tiered Safety Pipeline Integration.

Integrates the TieredSafetyClassifier, OverrideGenerator, and OverrideValidator
into the existing SemaBridge translation and deployment pipeline.

This module serves as the coordination layer that:
1. Classifies all DAX measures through the safety engine
2. Routes Tier 1/2 measures to automated translation 
3. Flags Tier 3/4 measures and generates override files
4. Loads existing overrides and applies them during deployment
5. Produces safety reports for governance review
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from semabridge.converter.dax_translator import DAXTranslator, DAXTranslationResult
from semabridge.converter.tiered_safety import (
    HazardCategory,
    SafetyClassification,
    SafetyTier,
    TieredSafetyClassifier,
)
from semabridge.converter.override_generator import OverrideGenerator
from semabridge.converter.override_schema import SQLOverrideFile
from semabridge.converter.override_validator import (
    OverrideValidator,
    OverrideValidationResult,
    load_override_directory,
)
from semabridge.formats.sml.models import SMLMetric, SMLModel
from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    from semabridge.intermediate.models import OSIModel, OSIMetric

logger = get_logger(__name__)


class SafetyPipelineResult:
    """Result of running the full safety pipeline on a semantic model."""

    def __init__(self):
        self.classifications: Dict[str, SafetyClassification] = {}
        self.auto_translated: Dict[str, DAXTranslationResult] = {}
        self.overrides_generated: Dict[str, SQLOverrideFile] = {}
        self.overrides_loaded: Dict[str, SQLOverrideFile] = {}
        self.validation_results: Dict[str, OverrideValidationResult] = {}
        self.flagged_measures: List[str] = []
        self.errors: List[str] = []

    @property
    def total_measures(self) -> int:
        return len(self.classifications)

    @property
    def auto_translatable_count(self) -> int:
        return sum(
            1 for c in self.classifications.values() if c.is_automatable
        )

    @property
    def override_required_count(self) -> int:
        return sum(
            1 for c in self.classifications.values() if c.requires_override
        )

    @property
    def override_coverage(self) -> float:
        """Percentage of Tier 3/4 measures that have valid overrides."""
        if self.override_required_count == 0:
            return 100.0
        covered = sum(
            1
            for name in self.flagged_measures
            if name in self.overrides_loaded
        )
        return round(covered / self.override_required_count * 100, 1)

    def get_report(self) -> Dict[str, Any]:
        """Generate a structured pipeline report."""
        tier_dist = {1: 0, 2: 0, 3: 0, 4: 0}
        hazard_dist: Dict[str, int] = {}

        for c in self.classifications.values():
            tier_dist[c.tier.value] += 1
            if c.requires_override:
                key = c.primary_hazard.value
                hazard_dist[key] = hazard_dist.get(key, 0) + 1

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "total_measures": self.total_measures,
            "tier_distribution": tier_dist,
            "auto_translatable": self.auto_translatable_count,
            "override_required": self.override_required_count,
            "overrides_loaded": len(self.overrides_loaded),
            "override_coverage_pct": self.override_coverage,
            "hazard_breakdown": hazard_dist,
            "flagged_measures": self.flagged_measures,
            "errors": self.errors,
        }


class TieredSafetyPipeline:
    """Orchestrates the Tiered Safety workflow for semantic model translation.

    This pipeline:
    1. Classifies every DAX measure in the model through the safety engine
    2. Auto-translates safe measures (Tier 1/2) via DAXTranslator
    3. Generates override templates for hazardous measures (Tier 3/4)
    4. Loads and validates existing manual overrides
    5. Produces a safety report

    Usage::

        pipeline = TieredSafetyPipeline(
            override_dir=Path("output/manual_sql_overrides"),
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        result = pipeline.run(sml_model)
        report = result.get_report()
    """

    def __init__(
        self,
        override_dir: Optional[Path] = None,
        database: str = "ANALYTICS_DB",
        schema: str = "SEMANTIC",
        warehouse: str = "ANALYTICS_COMPUTE_WH",
        llm_config=None,
        max_workers: Optional[int] = None,
    ):
        self.override_dir = Path(override_dir) if override_dir else None
        self.classifier = TieredSafetyClassifier()
        # Pass LLM config to DAXTranslator for Tier 4 support
        self.translator = DAXTranslator(llm_config=llm_config)
        self.generator = OverrideGenerator(database, schema, warehouse)
        self.validator = OverrideValidator()
        self.database = database
        self.schema = schema
        self.max_workers = max_workers

    def run(
        self,
        sml_model: SMLModel,
        generate_templates: bool = True,
        auto_translate: bool = True,
    ) -> SafetyPipelineResult:
        """Execute the full Tiered Safety pipeline on a semantic model.

        Args:
            sml_model: The SML model containing metrics to classify.
            generate_templates: Whether to generate override templates for Tier 3/4.
            auto_translate: Whether to auto-translate Tier 1/2 measures.

        Returns:
            SafetyPipelineResult with all classifications, translations, and overrides.
        """
        result = SafetyPipelineResult()

        logger.info(
            f"Starting Tiered Safety Pipeline for model: {sml_model.unique_name} "
            f"({len(sml_model.metrics)} metrics)"
        )

        # Step 1: Classify all measures
        result.classifications = self._classify_all_metrics(sml_model.metrics)

        # Step 2: Separate automatable vs override-required
        for name, classification in result.classifications.items():
            if classification.requires_override:
                result.flagged_measures.append(name)

        logger.info(
            f"Classification complete: "
            f"{result.auto_translatable_count} automatable, "
            f"{result.override_required_count} require override"
        )

        # Step 3: Auto-translate safe measures
        if auto_translate:
            result.auto_translated = self._auto_translate(
                sml_model, result.classifications
            )

        # Step 4: Load existing overrides
        if self.override_dir and self.override_dir.exists():
            result.overrides_loaded = load_override_directory(self.override_dir)
            logger.info(
                f"Loaded {len(result.overrides_loaded)} existing overrides "
                f"from {self.override_dir}"
            )

        # Step 5: Generate templates for uncovered Tier 3/4 measures
        if generate_templates and self.override_dir:
            uncovered = [
                name
                for name in result.flagged_measures
                if name not in result.overrides_loaded
            ]
            if uncovered:
                result.overrides_generated = self._generate_override_templates(
                    uncovered,
                    result.classifications,
                    sml_model.unique_name,
                )

        # Step 6: Validate all overrides
        all_overrides = {**result.overrides_loaded, **result.overrides_generated}
        for name, override in all_overrides.items():
            result.validation_results[name] = self.validator.validate(override)

        # Step 7: Update SMLMetric metadata with classification results
        self._update_metric_metadata(sml_model, result)

        # Log summary
        report = result.get_report()
        logger.info(
            f"Pipeline complete: "
            f"T1={report['tier_distribution'][1]}, "
            f"T2={report['tier_distribution'][2]}, "
            f"T3={report['tier_distribution'][3]}, "
            f"T4={report['tier_distribution'][4]} | "
            f"Override coverage: {report['override_coverage_pct']}%"
        )

        return result

    def write_safety_report(
        self,
        result: SafetyPipelineResult,
        output_dir: Path,
    ) -> Path:
        """Write the safety pipeline report to a JSON file.

        Args:
            result: The pipeline result to report on.
            output_dir: Directory to write the report.

        Returns:
            Path to the written report file.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        report = result.get_report()

        # Add per-measure detail
        measure_details = []
        for name, classification in result.classifications.items():
            detail = {
                "metric_name": name,
                "safety_tier": classification.tier.value,
                "is_automatable": classification.is_automatable,
                "requires_override": classification.requires_override,
                "primary_hazard": classification.primary_hazard.value,
                "hazard_count": len(classification.hazards),
                "estimated_complexity": classification.estimated_complexity,
                "recommendation": classification.recommendation,
                "has_override": name in result.overrides_loaded,
            }

            # Validation status
            if name in result.validation_results:
                vr = result.validation_results[name]
                detail["override_valid"] = vr.is_valid
                detail["validation_errors"] = len(vr.errors)
            else:
                detail["override_valid"] = None
                detail["validation_errors"] = 0

            measure_details.append(detail)

        report["measure_details"] = measure_details

        filepath = output_dir / "tiered_safety_report.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"Safety report written: {filepath}")
        return filepath

    # ------------------------------------------------------------------
    # Private Helpers
    # ------------------------------------------------------------------

    def _classify_all_metrics(
        self, metrics: List[SMLMetric]
    ) -> Dict[str, SafetyClassification]:
        """Classify all metrics through the TieredSafetyClassifier."""
        measures = {}
        for metric in metrics:
            dax = metric.expression or ""
            measures[metric.unique_name] = dax
        return self.classifier.classify_all(measures, max_workers=self.max_workers)

    def _auto_translate(
        self,
        sml_model: SMLModel,
        classifications: Dict[str, SafetyClassification],
    ) -> Dict[str, DAXTranslationResult]:
        """Auto-translate Tier 1/2 measures using the DAXTranslator."""
        results: Dict[str, DAXTranslationResult] = {}

        for metric in sml_model.metrics:
            classification = classifications.get(metric.unique_name)
            if not classification or not classification.is_automatable:
                continue

            dax = metric.expression or ""
            if not dax:
                continue

            table_alias = self.translator._quote(
                metric.dataset or "t"
            ).strip('"')
            translation = self.translator.translate(
                dax,
                table_alias,
                metric.dataset or "",
                metric_name=metric.unique_name,
                metrics_context=sml_model.metrics,
            )
            results[metric.unique_name] = translation

            if translation.is_success:
                metric.sql_expression = translation.sql
                logger.debug(
                    f"Auto-translated: {metric.unique_name} (Tier {translation.tier})"
                )

        return results

    def _generate_override_templates(
        self,
        uncovered_metrics: List[str],
        classifications: Dict[str, SafetyClassification],
        source_model: str,
    ) -> Dict[str, SQLOverrideFile]:
        """Generate override template files for uncovered Tier 3/4 measures."""
        overrides: Dict[str, SQLOverrideFile] = {}

        for name in uncovered_metrics:
            classification = classifications.get(name)
            if not classification:
                continue

            # Do not overwrite existing override files on disk
            if self.override_dir:
                from semabridge.converter.override_generator import OverrideGenerator
                sanitized = OverrideGenerator._sanitize_name(name)
                existing_yaml = self.override_dir / f"override_{sanitized}.yaml"
                if existing_yaml.exists():
                    logger.info(
                        f"Skipping template generation for '{name}': "
                        f"override file already exists at {existing_yaml}"
                    )
                    continue

            override = self.generator.generate_override(
                classification, source_model=source_model
            )
            overrides[name] = override

            # Write to disk
            if self.override_dir:
                self.generator.write_override_yaml(override, self.override_dir)
                self.generator.write_sql(override, self.override_dir)

        logger.info(
            f"Generated {len(overrides)} override templates for uncovered measures."
        )
        return overrides

    def _update_metric_metadata(
        self,
        sml_model: SMLModel,
        result: SafetyPipelineResult,
    ) -> None:
        """Update SMLMetric metadata fields based on classification results."""
        for metric in sml_model.metrics:
            classification = result.classifications.get(metric.unique_name)
            if not classification:
                continue

            metric.complexity_tier = classification.tier.value
            metric.sync_enabled = classification.is_automatable

            if classification.requires_override:
                metric.sync_failure_reason = (
                    f"Tier {classification.tier.value} hazard: "
                    f"{classification.primary_hazard.value}. "
                    f"{classification.recommendation}"
                )

                # Check if an override exists
                if metric.unique_name in result.overrides_loaded:
                    override = result.overrides_loaded[metric.unique_name]
                    # Apply override SQL if available
                    if (
                        override.layer_2_semantic_view
                        and override.layer_2_semantic_view.metrics
                    ):
                        for m in override.layer_2_semantic_view.metrics:
                            if m.is_override and m.expression and "TODO" not in m.expression:
                                metric.sql_expression = m.expression
                                metric.sync_enabled = True
                                metric.sync_failure_reason = None
                                logger.info(
                                    f"Applied manual override for: {metric.unique_name}"
                                )
                                break

    # ------------------------------------------------------------------
    # OSI-native pipeline (no SML dependency)
    # ------------------------------------------------------------------

    def run_osi(
        self,
        osi_model: "OSIModel",
        generate_templates: bool = True,
        auto_translate: bool = True,
    ) -> SafetyPipelineResult:
        """Execute the Tiered Safety pipeline directly on an OSI model.

        This is the OSI-native counterpart of :meth:`run`.  It classifies,
        auto-translates, loads overrides, and updates ``OSIMetric`` fields
        *without* converting to SML first.

        Args:
            osi_model: The OSI model containing metrics to classify.
            generate_templates: Whether to generate override templates for Tier 3/4.
            auto_translate: Whether to auto-translate Tier 1/2 measures.

        Returns:
            ``SafetyPipelineResult`` with all classifications, translations,
            and overrides.
        """
        result = SafetyPipelineResult()

        logger.info(
            f"Starting OSI Safety Pipeline for model: {osi_model.unique_name} "
            f"({len(osi_model.metrics)} metrics)"
        )

        # Step 1: Classify — identical logic, reads expression strings
        measures: dict[str, str] = {}
        for m in osi_model.metrics:
            measures[m.unique_name] = m.expression or ""
        result.classifications = self.classifier.classify_all(
            measures, max_workers=self.max_workers
        )

        # Step 2: Identify overrides-required
        for name, classification in result.classifications.items():
            if classification.requires_override:
                result.flagged_measures.append(name)

        logger.info(
            f"Classification complete: "
            f"{result.auto_translatable_count} automatable, "
            f"{result.override_required_count} require override"
        )

        # Step 3: Auto-translate safe measures on OSI metrics
        if auto_translate:
            result.auto_translated = self._auto_translate_osi(
                osi_model, result.classifications
            )

        # Step 4: Load existing overrides
        if self.override_dir and self.override_dir.exists():
            result.overrides_loaded = load_override_directory(self.override_dir)
            logger.info(
                f"Loaded {len(result.overrides_loaded)} existing overrides "
                f"from {self.override_dir}"
            )

        # Step 5: Generate templates for uncovered Tier 3/4
        if generate_templates and self.override_dir:
            uncovered = [
                name for name in result.flagged_measures
                if name not in result.overrides_loaded
            ]
            if uncovered:
                result.overrides_generated = self._generate_override_templates(
                    uncovered,
                    result.classifications,
                    osi_model.unique_name,
                )

        # Step 6: Validate overrides
        all_overrides = {**result.overrides_loaded, **result.overrides_generated}
        for name, override in all_overrides.items():
            result.validation_results[name] = self.validator.validate(override)

        # Step 7: Update OSI metric metadata
        self._update_osi_metric_metadata(osi_model, result)

        report = result.get_report()
        logger.info(
            f"OSI Pipeline complete: "
            f"T1={report['tier_distribution'][1]}, "
            f"T2={report['tier_distribution'][2]}, "
            f"T3={report['tier_distribution'][3]}, "
            f"T4={report['tier_distribution'][4]} | "
            f"Override coverage: {report['override_coverage_pct']}%"
        )

        return result

    def _auto_translate_osi(
        self,
        osi_model: "OSIModel",
        classifications: Dict[str, SafetyClassification],
    ) -> Dict[str, DAXTranslationResult]:
        """Auto-translate Tier 1/2 OSI metrics via DAXTranslator."""
        results: Dict[str, DAXTranslationResult] = {}

        for metric in osi_model.metrics:
            classification = classifications.get(metric.unique_name)
            if not classification or not classification.is_automatable:
                continue

            dax = metric.expression or ""
            if not dax:
                continue

            table_alias = self.translator._quote(
                metric.dataset or "t"
            ).strip('"')
            translation = self.translator.translate(
                dax,
                table_alias,
                metric.dataset or "",
                metric_name=metric.unique_name,
            )
            results[metric.unique_name] = translation

            if translation.is_success:
                object.__setattr__(metric, "sql_expression", translation.sql)
                object.__setattr__(metric, "complexity_tier", translation.tier)
                logger.debug(
                    f"Auto-translated (OSI): {metric.unique_name} (Tier {translation.tier})"
                )

        return results

    def _update_osi_metric_metadata(
        self,
        osi_model: "OSIModel",
        result: SafetyPipelineResult,
    ) -> None:
        """Update OSI metric fields based on classification results.

        Sets complexity_tier, is_override, sync_enabled, sync_failure_reason,
        and requires_time_intel so that downstream consumers (sync-measures,
        emitter) can filter and route metrics without SML conversion.
        """
        for metric in osi_model.metrics:
            classification = result.classifications.get(metric.unique_name)
            if not classification:
                continue

            object.__setattr__(metric, "complexity_tier", classification.tier.value)
            object.__setattr__(metric, "is_override", classification.requires_override)

            # Determine sync eligibility
            has_sql = bool(metric.sql_expression)
            is_auto = classification.is_automatable
            has_override = metric.unique_name in result.overrides_loaded

            if is_auto and has_sql:
                object.__setattr__(metric, "sync_enabled", True)
            elif classification.requires_override and has_override:
                object.__setattr__(metric, "sync_enabled", True)
            elif classification.requires_override and not has_override:
                object.__setattr__(metric, "sync_enabled", False)
                object.__setattr__(
                    metric, "sync_failure_reason",
                    f"Tier {classification.tier.value} requires override — none loaded"
                )
            else:
                # Tier 1/2 without sql_expression yet — still syncable via DAX
                object.__setattr__(metric, "sync_enabled", True)

            # Flag time-intelligence metrics
            upper_expr = (metric.expression or "").upper()
            time_funcs = [
                "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH",
                "PREVIOUSQUARTER", "TOTALYTD", "TOTALMTD", "TOTALQTD",
                "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
                "PARALLELPERIOD", "DATESBETWEEN",
            ]
            if any(f in upper_expr for f in time_funcs):
                object.__setattr__(metric, "requires_time_intel", True)

            if classification.requires_override:
                # Check if an override exists
                if metric.unique_name in result.overrides_loaded:
                    override = result.overrides_loaded[metric.unique_name]
                    if (
                        override.layer_2_semantic_view
                        and override.layer_2_semantic_view.metrics
                    ):
                        for m in override.layer_2_semantic_view.metrics:
                            if m.is_override and m.expression and "TODO" not in m.expression:
                                object.__setattr__(metric, "sql_expression", m.expression)
                                object.__setattr__(metric, "is_override", True)
                                logger.info(
                                    f"Applied manual override (OSI): {metric.unique_name}"
                                )
                                break
