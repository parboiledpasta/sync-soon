"""
Hierarchy Flattener.

Flattens parent-child (recursive) hierarchies into fixed-width columns
using DAX PATH() and PATHITEM() functions during extraction.

This complements the existing HierarchyDetector which handles fixed-level
hierarchies.  The Flattener focuses on **variable-depth** hierarchies
(Org Charts, Chart of Accounts) that must be denormalized into Snowflake
"shadow tables".
"""

from __future__ import annotations

from typing import Any, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class HierarchyFlattener:
    """Builds DAX queries to flatten parent-child hierarchies.

    Usage::

        flattener = HierarchyFlattener()
        dax = flattener.build_flattening_query(
            table_name="Employee",
            id_column="EmployeeKey",
            parent_column="ManagerKey",
            label_column="EmployeeName",
            max_depth=10,
        )
        # Execute dax via FabricExtractor.execute_dax_query()
        # Then post-process:
        flat_rows = flattener.flatten_results(raw_rows, max_depth=10)
    """

    def build_flattening_query(
        self,
        table_name: str,
        id_column: str,
        parent_column: str,
        label_column: Optional[str] = None,
        max_depth: int = 10,
    ) -> str:
        """Build a DAX query that flattens a parent-child hierarchy.

        Uses the DAX PATH() function to compute the full ancestor chain for
        every row, then PATHITEM() to split each depth level into a separate
        column.

        Args:
            table_name: Name of the source table (e.g., "Employee").
            id_column: The unique identifier column (e.g., "EmployeeKey").
            parent_column: The column referencing the parent row
                (e.g., "ManagerKey").
            label_column: Optional column for display names.  When provided
                the query also resolves the *label* at each level via LOOKUPVALUE.
            max_depth: Maximum hierarchy depth to materialise (default 10).

        Returns:
            A DAX EVALUATE expression string.
        """
        level_columns: list[str] = []
        for depth in range(1, max_depth + 1):
            level_columns.append(
                f'    "Level{depth}_Key", '
                f"PATHITEM(__Path, {depth}, INTEGER)"
            )
            if label_column:
                level_columns.append(
                    f'    "Level{depth}_Name", '
                    f"LOOKUPVALUE("
                    f"'{table_name}'[{label_column}], "
                    f"'{table_name}'[{id_column}], "
                    f"PATHITEM(__Path, {depth}, INTEGER))"
                )

        level_block = ",\n".join(level_columns)

        # PATHLENGTH gives the actual depth for each row, enabling consumers
        # to distinguish between a 3-level and a 7-level branch.
        query = (
            "EVALUATE\n"
            "ADDCOLUMNS(\n"
            f"    ADDCOLUMNS(\n"
            f"        '{table_name}',\n"
            f'        "__Path", PATH(\'{table_name}\'[{id_column}], '
            f"'{table_name}'[{parent_column}])\n"
            f"    ),\n"
            f'    "HierarchyDepth", PATHLENGTH(__Path),\n'
            f"{level_block}\n"
            ")"
        )

        logger.info(
            f"Built hierarchy flattening query for '{table_name}' "
            f"(max_depth={max_depth})"
        )
        return query

    @staticmethod
    def flatten_results(
        rows: list[dict[str, Any]], max_depth: int = 10
    ) -> list[dict[str, Any]]:
        """Post-process DAX results into clean flat rows.

        Removes the intermediate ``__Path`` column and normalises level
        columns so that empty levels beyond HierarchyDepth are set to None.

        Args:
            rows: Raw row dicts returned by FabricExtractor.execute_dax_query().
            max_depth: The same max_depth used in build_flattening_query().

        Returns:
            Cleaned list of row dictionaries.
        """
        cleaned: list[dict[str, Any]] = []

        for row in rows:
            new_row = dict(row)

            # Remove internal path column
            new_row.pop("__Path", None)
            new_row.pop("[__Path]", None)

            # Normalise: set levels beyond actual depth to None
            depth = new_row.get("HierarchyDepth") or new_row.get("[HierarchyDepth]") or max_depth
            try:
                depth = int(depth)
            except (TypeError, ValueError):
                depth = max_depth

            for level in range(depth + 1, max_depth + 1):
                for suffix in ("_Key", "_Name"):
                    col = f"Level{level}{suffix}"
                    alt_col = f"[Level{level}{suffix}]"
                    if col in new_row:
                        new_row[col] = None
                    if alt_col in new_row:
                        new_row[alt_col] = None

            cleaned.append(new_row)

        logger.info(f"Flattened {len(cleaned)} hierarchy rows")
        return cleaned

    @staticmethod
    def detect_parent_child_columns(
        columns: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        """Detect likely parent-child column pairs in table metadata.

        Looks for naming patterns such as:
            - EmployeeKey / ManagerKey
            - AccountID / ParentAccountID
            - ID / ParentID

        Args:
            columns: List of column metadata dicts with at least a "name" key.

        Returns:
            List of dicts with "id_column" and "parent_column" keys.
        """
        import re

        col_names = [c.get("name", c.get("COLUMN_NAME", "")) for c in columns]
        upper_names = {n.upper(): n for n in col_names}

        results: list[dict[str, str]] = []

        # Pattern 1: ParentXxx / Xxx
        parent_prefix_pattern = re.compile(r"^PARENT[_]?(.+)$", re.IGNORECASE)
        for upper, original in upper_names.items():
            match = parent_prefix_pattern.match(upper)
            if match:
                candidate_child = match.group(1)
                # Look for the child column
                if candidate_child in upper_names:
                    results.append({
                        "id_column": upper_names[candidate_child],
                        "parent_column": original,
                    })

        # Pattern 2: XxxKey / ManagerKey (Employee / Manager pattern)
        manager_pattern = re.compile(r"^MANAGER[_]?(KEY|ID)?$", re.IGNORECASE)
        for upper, original in upper_names.items():
            if manager_pattern.match(upper):
                # Look for EmployeeKey or similar
                for child_upper, child_orig in upper_names.items():
                    if child_upper != upper and child_upper.endswith(
                        upper.replace("MANAGER", "").lstrip("_") or "KEY"
                    ):
                        results.append({
                            "id_column": child_orig,
                            "parent_column": original,
                        })

        return results
