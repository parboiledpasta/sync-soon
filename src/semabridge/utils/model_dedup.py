"""
Deterministic Semantic Deduplication Pipeline (Module 3).

Prevents duplicate models (e.g. ``Regional_Sales_Sample`` vs
``Regional Sales Sample``) from both deploying to the same target.

Two deduplication strategies:

1. **Canonical-name dedup** — normalises display names to a canonical
   form (lowercase, underscores only) and groups collisions.
2. **Structural fingerprint dedup** — hashes the model structure
   (dataset names, column names, metric names) so that models with
   different names but identical content are detected.

Usage::

    from semabridge.utils.model_dedup import deduplicate_models

    clean, dupes = deduplicate_models(osi_model_dict)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# =========================================================================
# Canonical Name Normalization
# =========================================================================


def canonicalize_name(name: str) -> str:
    """Normalise a model name to a canonical form for dedup comparison.

    Rules:
      - Lowercase
      - Replace spaces, hyphens, and non-alphanumeric chars with ``_``
      - Collapse consecutive underscores
      - Strip leading/trailing underscores

    Examples::

        "Regional Sales Sample"  → "regional_sales_sample"
        "Regional_Sales_Sample"  → "regional_sales_sample"
        "regional-sales-sample"  → "regional_sales_sample"
        "  My Model!  "          → "my_model"
    """
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


# =========================================================================
# Structural Fingerprinting
# =========================================================================


def fingerprint_model(model: Any) -> str:
    """Compute a deterministic structural fingerprint for a model.

    The fingerprint captures the *shape* of the model — which datasets,
    columns, and metrics are declared — but ignores display-name
    differences, metadata timestamps, and ordering.

    The hash is a SHA-256 hex digest truncated to 16 chars.

    Args:
        model: An OSI or SML model object (duck-typed).

    Returns:
        16-char hex fingerprint string.
    """
    parts: List[str] = []

    # Datasets + columns (sorted for determinism)
    for ds in sorted(
        getattr(model, "datasets", []),
        key=lambda d: getattr(d, "unique_name", ""),
    ):
        ds_name = getattr(ds, "unique_name", "").upper()
        parts.append(f"DS:{ds_name}")
        for col in sorted(
            getattr(ds, "columns", []),
            key=lambda c: getattr(c, "unique_name", ""),
        ):
            col_name = getattr(col, "unique_name", "").upper()
            parts.append(f"  COL:{col_name}")

    # Metrics (sorted)
    for metric in sorted(
        getattr(model, "metrics", []),
        key=lambda m: getattr(m, "unique_name", ""),
    ):
        m_name = getattr(metric, "unique_name", "").upper()
        m_agg = str(getattr(metric, "aggregation", "")).upper()
        parts.append(f"M:{m_name}:{m_agg}")

    # Relationships (sorted)
    for rel in sorted(
        getattr(model, "relationships", []),
        key=lambda r: getattr(r, "unique_name", ""),
    ):
        from_ds = getattr(rel, "from_dataset", "").upper()
        to_ds = getattr(rel, "to_dataset", "").upper()
        parts.append(f"R:{from_ds}->{to_ds}")

    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# =========================================================================
# Duplicate Group
# =========================================================================


@dataclass
class DuplicateGroup:
    """A group of models that share the same canonical name or fingerprint."""

    canonical_name: str
    fingerprint: str = ""
    variants: List[str] = field(default_factory=list)
    kept: str = ""
    dropped: List[str] = field(default_factory=list)
    reason: str = ""  # "name" or "structure"

    def summary(self) -> str:
        return (
            f"DuplicateGroup({self.reason}): "
            f"canonical='{self.canonical_name}', "
            f"kept='{self.kept}', dropped={self.dropped}"
        )


# =========================================================================
# Public API
# =========================================================================


def deduplicate_models(
    models: Dict[str, Any],
    *,
    strict: bool = False,
) -> Tuple[Dict[str, Any], List[DuplicateGroup]]:
    """Deduplicate a dictionary of models.

    Two passes:

    1. **Canonical-name pass** — groups models whose display names
       normalise to the same canonical form.  Keeps the first one
       encountered (stable dict order).

    2. **Structural-fingerprint pass** — among the survivors, groups
       models whose dataset/column/metric structure hashes to the same
       fingerprint.  Keeps the first one.

    Args:
        models: ``{model_id: model_object}`` dict.
        strict: If ``True``, raise ``ValueError`` on duplicates instead
            of silently dropping.

    Returns:
        Tuple of ``(cleaned_dict, duplicate_groups)``.

    Raises:
        ValueError: If ``strict=True`` and duplicates are detected.
    """
    if not models:
        return {}, []

    duplicate_groups: List[DuplicateGroup] = []

    # ── Pass 1: Canonical-name dedup ────────────────────────────────
    canonical_buckets: Dict[str, List[str]] = {}
    for model_id, model in models.items():
        display_name = (
            getattr(model, "label", None)
            or getattr(model, "unique_name", None)
            or model_id
        )
        canon = canonicalize_name(display_name)
        canonical_buckets.setdefault(canon, []).append(model_id)

    survivors: Dict[str, Any] = {}
    for canon, ids in canonical_buckets.items():
        # Keep first, record rest as duplicates
        kept_id = ids[0]
        survivors[kept_id] = models[kept_id]

        if len(ids) > 1:
            dropped = ids[1:]
            group = DuplicateGroup(
                canonical_name=canon,
                variants=ids,
                kept=kept_id,
                dropped=dropped,
                reason="name",
            )
            duplicate_groups.append(group)
            logger.warning(
                f"Name-duplicate detected: canonical='{canon}', "
                f"keeping='{kept_id}', dropping={dropped}"
            )

    # ── Pass 2: Structural-fingerprint dedup ────────────────────────
    fp_buckets: Dict[str, List[str]] = {}
    fp_map: Dict[str, str] = {}
    for model_id, model in survivors.items():
        fp = fingerprint_model(model)
        fp_map[model_id] = fp
        fp_buckets.setdefault(fp, []).append(model_id)

    final: Dict[str, Any] = {}
    for fp, ids in fp_buckets.items():
        kept_id = ids[0]
        final[kept_id] = survivors[kept_id]

        if len(ids) > 1:
            dropped = ids[1:]
            group = DuplicateGroup(
                canonical_name=canonicalize_name(
                    getattr(survivors[kept_id], "label", kept_id)
                    or kept_id
                ),
                fingerprint=fp,
                variants=ids,
                kept=kept_id,
                dropped=dropped,
                reason="structure",
            )
            duplicate_groups.append(group)
            for d in dropped:
                if d in final:
                    del final[d]
            logger.warning(
                f"Structure-duplicate detected: fingerprint='{fp}', "
                f"keeping='{kept_id}', dropping={dropped}"
            )

    if strict and duplicate_groups:
        summaries = "; ".join(g.summary() for g in duplicate_groups)
        raise ValueError(
            f"Duplicate models detected (strict mode): {summaries}"
        )

    total_dropped = sum(len(g.dropped) for g in duplicate_groups)
    if total_dropped:
        logger.info(
            f"Deduplication complete: {len(models)} → {len(final)} models "
            f"({total_dropped} duplicates removed in "
            f"{len(duplicate_groups)} group(s))"
        )

    return final, duplicate_groups


def deduplicate_model_names(names: List[str]) -> Tuple[List[str], List[DuplicateGroup]]:
    """Deduplicate a list of model names using canonical normalization.

    Lighter-weight alternative to :func:`deduplicate_models` when only
    names are available (e.g. during discovery/resolution phase).

    Args:
        names: List of model display names.

    Returns:
        Tuple of ``(unique_names, duplicate_groups)``.
    """
    canonical_map: Dict[str, List[str]] = {}
    for name in names:
        canon = canonicalize_name(name)
        canonical_map.setdefault(canon, []).append(name)

    unique: List[str] = []
    groups: List[DuplicateGroup] = []

    for canon, variants in canonical_map.items():
        unique.append(variants[0])
        if len(variants) > 1:
            groups.append(DuplicateGroup(
                canonical_name=canon,
                variants=variants,
                kept=variants[0],
                dropped=variants[1:],
                reason="name",
            ))
            logger.warning(
                f"Name-duplicate in discovery: canonical='{canon}', "
                f"keeping='{variants[0]}', dropping={variants[1:]}"
            )

    return unique, groups
