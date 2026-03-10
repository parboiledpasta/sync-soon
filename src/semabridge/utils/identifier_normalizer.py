"""
Identifier Normalizer.

Extends the IdentifierSanitizer with validation-oriented methods for
pre-deployment checks.  Used by the pre-deployment validator and the
SnowflakeEmitter to ensure every identifier in emitted SQL resolves to a
real physical column or a declared alias.

This is the normalization layer required by Snowflake identifier rules:
  - Unquoted identifiers → MUST be uppercase
  - Quoted identifiers   → preserve case
  - Cross-table refs      → TABLE.COLUMN uses correct alias
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class IdentifierNormalizer:
    """Normalization layer that enforces Snowflake identifier rules.

    Wraps ``IdentifierSanitizer`` with validation-oriented helpers so
    that every identifier emitted in SQL is confirmed valid *before*
    execution.

    Snowflake identifier rules:
      - Unquoted identifiers → MUST be uppercase
      - Quoted identifiers   → preserve original case
      - Cross-table refs     → TABLE.COLUMN uses correct alias

    Args:
        sanitizer: Pre-configured ``IdentifierSanitizer`` instance.
    """

    def __init__(self, sanitizer: IdentifierSanitizer) -> None:
        self._id = sanitizer

    # ------------------------------------------------------------------
    # Core normalisation
    # ------------------------------------------------------------------

    def normalize_identifier(self, name: str, *, quoted: bool = False) -> str:
        """Normalise a single identifier per Snowflake rules.

        Args:
            name: Raw identifier string.
            quoted: If ``True``, preserves original case (double-quoted
                identifier).  If ``False``, uppercases the result.

        Returns:
            Normalised identifier string (NOT wrapped in quotes).
        """
        if not name:
            return "UNKNOWN"
        # Strip surrounding quotes if present
        stripped = name.strip('"')
        if quoted:
            # Preserve the case of the original (post-sanitization cleaning)
            clean = re.sub(r"[^A-Za-z0-9_]", "_", stripped)
            clean = re.sub(r"_+", "_", clean).strip("_")
            return clean if clean else "UNKNOWN"
        sanitized = self._id.sanitize_column(stripped)
        return sanitized  # already uppercased by IdentifierSanitizer

    def normalize_for_snowflake(self, name: str) -> str:
        """Normalize an identifier for Snowflake — uppercase and sanitized.

        Convenience wrapper that always produces an unquoted uppercase
        identifier suitable for Snowflake DDL.
        """
        return self.normalize_identifier(name, quoted=False)

    def normalize_and_quote(self, name: str, *, quoted: bool = False) -> str:
        """Normalize and wrap in double-quotes for Snowflake DDL.

        Args:
            name: Raw identifier.
            quoted: Preserve original case if True.

        Returns:
            Double-quoted identifier string.
        """
        normalized = self.normalize_identifier(name, quoted=quoted)
        return self._id.quote(normalized)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_column_exists(
        self,
        dataset_columns: Set[str],
        column_name: str,
    ) -> bool:
        """Check whether *column_name* resolves to a known physical column.

        Comparison is case-insensitive to catch casing mismatches between
        the logical model and the physical schema.

        Args:
            dataset_columns: Set of sanitized (uppercase) physical column
                names for the target dataset.
            column_name: Column name to validate.

        Returns:
            ``True`` if the column exists in the dataset.
        """
        sanitized = self._id.sanitize_column(column_name)
        return sanitized in dataset_columns

    # ------------------------------------------------------------------
    # Alias lookup builder
    # ------------------------------------------------------------------

    def build_alias_lookup(
        self,
        datasets: list,
        dataset_aliases: Dict[str, str],
    ) -> Dict[str, str]:
        """Build a comprehensive reverse-lookup map for alias resolution.

        Maps every plausible raw-name variant of a dataset to the
        sanitized alias declared in the TABLES clause, including the
        alias itself.  This ensures reserved-word-prefixed aliases
        (e.g. ``L_TABLE``) are discoverable from expression rewriting.

        Args:
            datasets: List of SML dataset objects.
            dataset_aliases: ``{dataset.unique_name: alias}`` mapping
                produced during semantic view generation.

        Returns:
            ``{UPPERCASE_VARIANT: alias}`` dict.
        """
        lookup: Dict[str, str] = {}

        for ds_name, alias in dataset_aliases.items():
            # Primary: exact unique_name
            lookup[ds_name.upper()] = alias
            # The alias itself (handles L_TABLE → L_TABLE)
            lookup[alias.upper()] = alias
            # source_table variant
            for ds in datasets:
                if ds.unique_name == ds_name:
                    if hasattr(ds, "source_table") and ds.source_table:
                        lookup[ds.source_table.upper()] = alias
                        safe_table = self._id.sanitize_table_name(ds.source_table)
                        lookup[safe_table] = alias
                    break

        return lookup

    # ------------------------------------------------------------------
    # Physical column lookup builder
    # ------------------------------------------------------------------

    def build_physical_column_lookup(
        self,
        datasets: list,
    ) -> Dict[str, Set[str]]:
        """Build a sanitized physical-column lookup per dataset.

        Excludes internal columns (``RowNumber``, ``_``-prefixed) and
        calculated columns (non-physical ``source_expression``).

        Args:
            datasets: List of SML dataset objects.

        Returns:
            ``{dataset.unique_name: {SANITIZED_COL, …}}``
        """
        result: Dict[str, Set[str]] = {}

        for dataset in datasets:
            cols: Set[str] = set()
            for c in dataset.columns:
                if c.unique_name.startswith("RowNumber"):
                    continue
                if c.unique_name.startswith("_"):
                    continue
                source_expr = getattr(c, "source_expression", None)
                if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                    continue
                cols.add(self._id.sanitize_column(c.unique_name))
            result[dataset.unique_name] = cols

        return result

    # ------------------------------------------------------------------
    # Detect casing mismatches
    # ------------------------------------------------------------------

    def detect_casing_mismatches(
        self,
        model_columns: Set[str],
        physical_columns: Set[str],
    ) -> List[Tuple[str, str]]:
        """Detect columns whose names differ only by case.

        Args:
            model_columns: Column names from the semantic model (sanitized).
            physical_columns: Column names from Snowflake INFORMATION_SCHEMA.

        Returns:
            List of ``(model_name, physical_name)`` pairs that differ
            only in case.
        """
        mismatches: List[Tuple[str, str]] = []
        phys_upper_map = {c.upper(): c for c in physical_columns}
        for mc in model_columns:
            phys_match = phys_upper_map.get(mc.upper())
            if phys_match and phys_match != mc:
                mismatches.append((mc, phys_match))
        return mismatches

    # ------------------------------------------------------------------
    # Snowflake metadata validation
    # ------------------------------------------------------------------

    def validate_against_snowflake(
        self,
        datasets: list,
        snowflake_metadata: Dict[str, Set[str]],
    ) -> List[Tuple[str, str, str]]:
        """Validate model column references against live Snowflake metadata.

        Args:
            datasets: List of SML/OSI dataset objects.
            snowflake_metadata: ``{TABLE_NAME: {COL_NAME, ...}}`` from
                Snowflake INFORMATION_SCHEMA.

        Returns:
            List of ``(dataset_name, column_name, issue)`` triples for
            columns that don't match Snowflake metadata.
        """
        issues: List[Tuple[str, str, str]] = []

        for ds in datasets:
            source_table = getattr(ds, "source_table", None) or ds.unique_name
            safe_table = self._id.sanitize_table_name(source_table)

            if safe_table not in snowflake_metadata:
                issues.append((ds.unique_name, "*", f"Table '{safe_table}' not found in Snowflake"))
                continue

            sf_cols = snowflake_metadata[safe_table]
            for col in getattr(ds, "columns", []):
                name = getattr(col, "unique_name", "")
                if name.startswith("RowNumber") or name.startswith("_"):
                    continue
                source_expr = getattr(col, "source_expression", None)
                if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                    continue
                sanitized = self._id.sanitize_column(name)
                if sanitized not in sf_cols:
                    # Check for case mismatch
                    upper_map = {c.upper(): c for c in sf_cols}
                    if sanitized.upper() in upper_map:
                        actual = upper_map[sanitized.upper()]
                        issues.append(
                            (ds.unique_name, name,
                             f"Casing mismatch: model='{sanitized}', snowflake='{actual}'")
                        )
                    else:
                        issues.append(
                            (ds.unique_name, name,
                             f"Column '{sanitized}' not found in Snowflake table '{safe_table}'")
                        )

        return issues
    # ------------------------------------------------------------------
    # Module 2: Qualified identifier splitting
    # ------------------------------------------------------------------

    def split_qualified_identifier(
        self, name: str
    ) -> Tuple[Optional[str], str]:
        """Split a potentially dot-qualified identifier and normalize parts.

        Delegates to :meth:`IdentifierSanitizer.split_dot_identifier` for
        the structural split, then normalizes each part through
        ``sanitize_table_name`` / ``sanitize_column``.

        Handles:
          - ``COLUMN``              → ``(None, "COLUMN")``
          - ``TABLE.COLUMN``        → ``("TABLE", "COLUMN")``
          - ``SCHEMA.TABLE.COLUMN`` → ``("TABLE", "COLUMN")``

        Returns:
            ``(table_or_none, column)`` — both sanitized.
        """
        table_raw, col_raw = IdentifierSanitizer.split_dot_identifier(name)
        col = self._id.sanitize_column(col_raw)
        table = self._id.sanitize_table_name(table_raw) if table_raw else None
        return (table, col)