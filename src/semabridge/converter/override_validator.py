"""
SQL Override Validator.

Validates Manual SQL Override files for completeness, schema conformance,
and SQL syntax correctness before deployment to Snowflake.
"""

from __future__ import annotations

import re
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError as PydanticValidationError

from semabridge.converter.override_schema import (
    OverrideStatus,
    SQLOverrideFile,
)
from semabridge.converter.tiered_safety import (
    HazardCategory,
    OverrideLayerType,
    _CATEGORY_OVERRIDE_LAYERS,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class OverrideValidationResult:
    """Result of validating an override file."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_info(self, msg: str) -> None:
        self.info.append(msg)

    def summary(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  ✗ {e}")
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        if self.info:
            lines.append(f"INFO ({len(self.info)}):")
            for i in self.info:
                lines.append(f"  ℹ {i}")
        if self.is_valid:
            lines.append("✓ Override file is valid.")
        else:
            lines.append("✗ Override file has validation errors.")
        return "\n".join(lines)


class OverrideValidator:
    """Validates SQL Override files for deployment readiness.

    Performs three levels of validation:
    1. Schema validation (Pydantic model conformance)
    2. Completeness validation (all required layers present)
    3. Content validation (SQL patterns, TODO markers, naming)

    Usage::

        validator = OverrideValidator()
        result = validator.validate_file(Path("override_my_metric.yaml"))
        if result.is_valid:
            print("Ready for deployment")
    """

    # Patterns indicating incomplete template sections
    _TODO_PATTERN = re.compile(r"--\s*TODO", re.IGNORECASE)
    _PLACEHOLDER_PATTERN = re.compile(r"source_table|-- TODO|placeholder", re.IGNORECASE)

    # Valid SQL identifier pattern
    _SQL_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

    def validate(self, override: SQLOverrideFile) -> OverrideValidationResult:
        """Validate an in-memory SQLOverrideFile object.

        Args:
            override: The override file model to validate.

        Returns:
            OverrideValidationResult with errors, warnings, and info.
        """
        result = OverrideValidationResult()

        # 1. Basic metadata checks
        self._validate_metadata(override, result)

        # 2. Layer completeness
        self._validate_layer_completeness(override, result)

        # 3. Layer 1 content
        if override.layer_1_dynamic_table:
            self._validate_dynamic_table(override.layer_1_dynamic_table, result)

        # 4. Layer 2 content
        if override.layer_2_semantic_view:
            self._validate_semantic_view(override.layer_2_semantic_view, result)

        # 5. Layer 3 content
        if override.layer_3_cortex_metadata:
            self._validate_cortex_metadata(override.layer_3_cortex_metadata, result)

        # 6. Status checks
        self._validate_deployment_readiness(override, result)

        return result

    def validate_file(self, filepath: Path) -> OverrideValidationResult:
        """Validate an override YAML file from disk.

        Args:
            filepath: Path to the YAML override file.

        Returns:
            OverrideValidationResult.
        """
        result = OverrideValidationResult()

        if not filepath.exists():
            result.add_error(f"File not found: {filepath}")
            return result

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            result.add_error(f"YAML parse error: {e}")
            return result

        if not isinstance(data, dict):
            result.add_error("Override file must contain a YAML mapping at root level.")
            return result

        try:
            override = SQLOverrideFile(**data)
        except PydanticValidationError as e:
            result.add_error(f"Schema validation failed: {e}")
            return result

        result.add_info(f"Schema validation passed for: {filepath.name}")
        validation = self.validate(override)
        # Merge file-level info into validation result
        validation.errors = result.errors + validation.errors
        validation.warnings = result.warnings + validation.warnings
        validation.info = result.info + validation.info
        return validation

    def validate_directory(self, directory: Path) -> Dict[str, OverrideValidationResult]:
        """Validate all override YAML files in a directory.

        Returns:
            Dict mapping filename -> OverrideValidationResult.
        """
        results: Dict[str, OverrideValidationResult] = {}
        directory = Path(directory)

        if not directory.exists():
            logger.warning(f"Override directory not found: {directory}")
            return results

        for filepath in sorted(directory.glob("override_*.yaml")):
            results[filepath.name] = self.validate_file(filepath)

        valid_count = sum(1 for r in results.values() if r.is_valid)
        logger.info(
            f"Validated {len(results)} override files: "
            f"{valid_count} valid, {len(results) - valid_count} with errors"
        )
        return results

    # ------------------------------------------------------------------
    # Private Validators
    # ------------------------------------------------------------------

    def _validate_metadata(
        self, override: SQLOverrideFile, result: OverrideValidationResult
    ) -> None:
        """Validate override file metadata."""
        if not override.metric_name:
            result.add_error("metric_name is required.")

        if override.safety_tier not in (3, 4):
            result.add_warning(
                f"safety_tier is {override.safety_tier} — overrides are "
                f"typically for Tier 3 or 4 measures."
            )

        if not override.original_dax:
            result.add_warning("original_dax is empty — include for traceability.")

        try:
            HazardCategory(override.hazard_category)
        except ValueError:
            result.add_error(
                f"Invalid hazard_category: '{override.hazard_category}'. "
                f"Valid values: {[e.value for e in HazardCategory]}"
            )

    def _validate_layer_completeness(
        self, override: SQLOverrideFile, result: OverrideValidationResult
    ) -> None:
        """Check that all required layers are present."""
        completeness_errors = override.validate_completeness()
        for err in completeness_errors:
            result.add_error(err)

    def _validate_dynamic_table(
        self, layer, result: OverrideValidationResult
    ) -> None:
        """Validate Layer 1 Dynamic Table content."""
        if not self._SQL_IDENTIFIER.match(layer.table_name):
            result.add_error(
                f"Layer 1: table_name '{layer.table_name}' is not a valid SQL identifier."
            )

        if self._has_todo(layer.source_table):
            result.add_warning("Layer 1: source_table contains TODO placeholder.")

        for col in layer.select_columns:
            if self._has_todo(col):
                result.add_warning(f"Layer 1: SELECT column contains TODO: '{col}'")

        for wf in layer.window_functions:
            if any(self._has_todo(p) for p in wf.partition_by):
                result.add_warning(
                    f"Layer 1: Window function '{wf.alias}' has TODO in PARTITION BY."
                )
            if any(self._has_todo(o) for o in wf.order_by):
                result.add_warning(
                    f"Layer 1: Window function '{wf.alias}' has TODO in ORDER BY."
                )

    def _validate_semantic_view(
        self, layer, result: OverrideValidationResult
    ) -> None:
        """Validate Layer 2 Semantic View content."""
        if not self._SQL_IDENTIFIER.match(layer.view_name):
            result.add_error(
                f"Layer 2: view_name '{layer.view_name}' is not a valid SQL identifier."
            )

        if not layer.metrics:
            result.add_error("Layer 2: at least one metric must be defined.")

        for metric in layer.metrics:
            if self._has_todo(metric.expression):
                result.add_warning(
                    f"Layer 2: Metric '{metric.name}' expression contains TODO."
                )
            if not metric.name:
                result.add_error("Layer 2: Metric name cannot be empty.")

    def _validate_cortex_metadata(
        self, layer, result: OverrideValidationResult
    ) -> None:
        """Validate Layer 3 Cortex metadata content."""
        for syn in layer.metric_synonyms:
            if not syn.synonyms or all(self._has_todo(s) for s in syn.synonyms):
                result.add_warning(
                    f"Layer 3: Metric '{syn.column_name}' has no real synonyms "
                    f"(only TODO placeholders)."
                )

    def _validate_deployment_readiness(
        self, override: SQLOverrideFile, result: OverrideValidationResult
    ) -> None:
        """Check if the override is ready for deployment."""
        if override.status == OverrideStatus.DRAFT:
            result.add_info(
                "Status is DRAFT — override must be promoted to "
                "REVIEW or APPROVED before deployment."
            )
        elif override.status == OverrideStatus.REVIEW:
            result.add_info("Status is REVIEW — awaiting approval.")
        elif override.status == OverrideStatus.APPROVED:
            result.add_info("Status is APPROVED — ready for deployment.")

        if not override.snowflake_database:
            result.add_warning("snowflake_database is not set.")
        if not override.snowflake_schema:
            result.add_warning("snowflake_schema is not set.")

    def _has_todo(self, text: str) -> bool:
        """Check if a string contains a TODO placeholder."""
        return bool(self._TODO_PATTERN.search(text)) if text else False


def load_override_file(filepath: Path) -> Optional[SQLOverrideFile]:
    """Load and parse an override YAML file.

    Args:
        filepath: Path to the YAML file.

    Returns:
        SQLOverrideFile or None if loading fails.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return SQLOverrideFile(**data)
    except Exception as e:
        logger.error(f"Failed to load override file {filepath}: {e}")
        return None


def load_override_directory(directory: Path) -> Dict[str, SQLOverrideFile]:
    """Load all override files from a directory.

    Returns:
        Dict mapping metric_name -> SQLOverrideFile.
    """
    overrides: Dict[str, SQLOverrideFile] = {}
    directory = Path(directory)

    if not directory.exists():
        return overrides

    for filepath in sorted(directory.glob("override_*.yaml")):
        override = load_override_file(filepath)
        if override:
            overrides[override.metric_name] = override
            logger.debug(f"Loaded override: {override.metric_name} from {filepath.name}")

    logger.info(f"Loaded {len(overrides)} override files from {directory}")
    return overrides
