"""
Deployment Error Reporter.

Generates structured deployment reports with per-model status, errors,
root cause analysis, and suggested fixes.

Integrates with ``SyncReport`` from ``sync_orchestrator`` and the
pre-deployment validator to produce a comprehensive summary suitable
for logging, CLI output, and CI/CD pipelines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Report Models
# =========================================================================


class DeploymentStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class ModelDeploymentResult:
    """Deployment outcome for a single model."""

    model_name: str
    status: str  # "success", "failed", "blocked", "skipped"
    classification: str = ""  # "dimension", "fact", "derived"
    duration_ms: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggested_fixes: List[str] = field(default_factory=list)


@dataclass
class DeploymentReport:
    """Comprehensive deployment report for a batch run.

    Aggregates results from validation, PK resolution, identifier
    normalization, and Snowflake deployment.
    """

    batch_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    total_models: int = 0
    succeeded: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    overall_status: DeploymentStatus = DeploymentStatus.SUCCESS
    model_results: List[ModelDeploymentResult] = field(default_factory=list)
    global_errors: List[str] = field(default_factory=list)
    global_warnings: List[str] = field(default_factory=list)

    def add_model_result(self, result: ModelDeploymentResult) -> None:
        """Add a model result and update counters."""
        self.model_results.append(result)
        self.total_models = len(self.model_results)
        self._recount()

    def _recount(self) -> None:
        """Recalculate counters from model results."""
        self.succeeded = sum(1 for r in self.model_results if r.status == "success")
        self.failed = sum(1 for r in self.model_results if r.status == "failed")
        self.blocked = sum(1 for r in self.model_results if r.status == "blocked")
        self.skipped = sum(1 for r in self.model_results if r.status == "skipped")
        if self.failed > 0 or self.blocked > 0:
            if self.succeeded > 0:
                self.overall_status = DeploymentStatus.PARTIAL
            else:
                self.overall_status = DeploymentStatus.FAILED
        else:
            self.overall_status = DeploymentStatus.SUCCESS

    def format_text(self) -> str:
        """Format report as human-readable text for CLI/logging."""
        lines = [
            "=" * 72,
            "  SEMABRIDGE DEPLOYMENT REPORT",
            "=" * 72,
            f"  Batch:      {self.batch_id}",
            f"  Status:     {self.overall_status.value.upper()}",
            f"  Started:    {self.started_at}",
            f"  Completed:  {self.completed_at}",
            "-" * 72,
            f"  Total: {self.total_models}  |  "
            f"Success: {self.succeeded}  |  "
            f"Failed: {self.failed}  |  "
            f"Blocked: {self.blocked}  |  "
            f"Skipped: {self.skipped}",
            "-" * 72,
        ]

        if self.global_errors:
            lines.append("")
            lines.append("  GLOBAL ERRORS:")
            for err in self.global_errors:
                lines.append(f"    ! {err}")

        if self.global_warnings:
            lines.append("")
            lines.append("  GLOBAL WARNINGS:")
            for warn in self.global_warnings:
                lines.append(f"    ~ {warn}")

        # Successful models
        success_models = [r for r in self.model_results if r.status == "success"]
        if success_models:
            lines.append("")
            lines.append("  SUCCESSFUL DEPLOYMENTS:")
            for r in success_models:
                dur = f" ({r.duration_ms}ms)" if r.duration_ms else ""
                lines.append(f"    + {r.model_name}{dur}")

        # Failed/blocked models
        problem_models = [
            r for r in self.model_results
            if r.status in ("failed", "blocked")
        ]
        if problem_models:
            lines.append("")
            lines.append("  FAILED / BLOCKED MODELS:")
            for r in problem_models:
                lines.append(f"    x {r.model_name} [{r.status.upper()}]")
                for err in r.errors[:5]:
                    lines.append(f"      Error: {err}")
                for fix in r.suggested_fixes[:3]:
                    lines.append(f"      Fix:   {fix}")

        # Skipped models
        skip_models = [r for r in self.model_results if r.status == "skipped"]
        if skip_models:
            lines.append("")
            lines.append("  SKIPPED MODELS (upstream dependency failed):")
            for r in skip_models:
                lines.append(f"    - {r.model_name}")

        lines.append("")
        lines.append("=" * 72)
        return "\n".join(lines)

    def format_json(self) -> str:
        """Format report as JSON for CI/CD integration."""
        data = {
            "batch_id": self.batch_id,
            "overall_status": self.overall_status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": {
                "total": self.total_models,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "blocked": self.blocked,
                "skipped": self.skipped,
            },
            "global_errors": self.global_errors,
            "global_warnings": self.global_warnings,
            "models": [
                {
                    "name": r.model_name,
                    "status": r.status,
                    "classification": r.classification,
                    "duration_ms": r.duration_ms,
                    "errors": r.errors,
                    "warnings": r.warnings,
                    "suggested_fixes": r.suggested_fixes,
                }
                for r in self.model_results
            ],
        }
        return json.dumps(data, indent=2)


# =========================================================================
# Suggested Fix Engine
# =========================================================================


_FIX_PATTERNS = [
    ("invalid identifier", (
        "A column referenced in the semantic view does not exist in the "
        "Snowflake source table. Check for casing mismatches or calculated "
        "columns that need to be excluded."
    )),
    ("primary key", (
        "Set pk_resolution_mode=permissive in behavior.yaml, or mark a "
        "column as is_key=True in the model definition."
    )),
    ("pk", (
        "Set pk_resolution_mode=permissive in behavior.yaml, or mark a "
        "column as is_key=True in the model definition."
    )),
    ("does not exist", (
        "Source table or column is missing in Snowflake. Enable "
        "create_missing_tables=true in behavior.yaml."
    )),
    ("timeout", (
        "Snowflake warehouse is overloaded. Increase warehouse size or "
        "reduce concurrent workers."
    )),
    ("semaphore", (
        "Snowflake warehouse is overloaded. Increase warehouse size or "
        "reduce concurrent workers."
    )),
    ("not authorized", (
        "Check Snowflake RBAC grants: USAGE on database/schema, "
        "CREATE TABLE, CREATE SEMANTIC VIEW."
    )),
    ("permission", (
        "Check Snowflake RBAC grants: USAGE on database/schema, "
        "CREATE TABLE, CREATE SEMANTIC VIEW."
    )),
    ("attributeerror", (
        "Internal emitter bug — a required attribute was not initialized. "
        "Check model structure for empty or None values."
    )),
    ("lock", (
        "DuckDB metadata database is locked by another process. "
        "Ensure only one deployment runs at a time, or increase retry count."
    )),
]


def suggest_fix(error_message: str) -> str:
    """Generate a human-readable fix suggestion from an error message."""
    msg_lower = error_message.lower()
    for pattern, fix in _FIX_PATTERNS:
        if pattern in msg_lower:
            return fix
    return "Check Snowflake logs and model definition for details."


def suggest_fixes_for_errors(errors: List[str]) -> List[str]:
    """Generate fix suggestions for a list of error messages.

    Deduplicates suggestions.
    """
    seen: set = set()
    fixes: List[str] = []
    for err in errors:
        fix = suggest_fix(err)
        if fix not in seen:
            fixes.append(fix)
            seen.add(fix)
    return fixes
