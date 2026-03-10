"""
Unified Identifier Sanitizer.

Single source of truth for all identifier sanitization, quoting, and reserved
word handling across the SemaBridge pipeline (SnowflakeEmitter, OSI-to-SQL
converter, DAX translator).

Architectural Mandate 1: Strict Identifier Hygiene & Contextual Scoping.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Snowflake Reserved Words
# ─────────────────────────────────────────────────────────────────────────────
# Comprehensive list — includes SQL keywords, Snowflake-specific identifiers,
# aggregate functions, window functions, data types, and DML/DDL keywords.
# Extended beyond the previous ~80-entry set to cover 150+ terms.

SNOWFLAKE_RESERVED_WORDS: frozenset[str] = frozenset({
    # SQL Keywords
    "TABLE", "DATE", "GROUP", "ORDER", "JOIN", "VIEW", "SELECT", "FROM",
    "WHERE", "AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "AS", "BY", "ON",
    "IN", "IS", "SET", "TO", "WITH", "QUALIFY",
    # Additional SQL clauses
    "HAVING", "LIMIT", "OFFSET", "UNION", "EXCEPT", "INTERSECT", "INTO",
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "GRANT", "REVOKE",
    "TRUNCATE", "MERGE", "COPY", "REPLACE", "CLONE", "UNDROP", "DESCRIBE",
    "SHOW", "USE", "PUT", "GET", "LIST", "REMOVE",
    # Aggregate functions (common conflict)
    "COUNT", "SUM", "AVG", "MIN", "MAX",
    # Snowflake-specific
    "CURRENT", "SESSION", "ACCOUNT", "DATABASE", "SCHEMA", "USER", "ROLE",
    "WAREHOUSE", "STAGE", "SEQUENCE", "STREAM", "TASK", "PIPE", "SHARE",
    "POLICY", "TAG", "NETWORK", "INTEGRATION", "NOTIFICATION", "FUNCTION",
    "PROCEDURE", "MASKING", "STORAGE",
    # Data types
    "INTEGER", "VARCHAR", "BOOLEAN", "FLOAT", "NUMBER", "STRING", "TIMESTAMP",
    "VARIANT", "OBJECT", "ARRAY", "BINARY", "GEOGRAPHY", "GEOMETRY",
    "DECIMAL", "NUMERIC", "DOUBLE", "REAL", "BIGINT", "SMALLINT", "TINYINT",
    "CHAR", "TEXT", "DATE", "TIME", "DATETIME",
    # Window functions
    "OVER", "PARTITION", "ROW", "ROWS", "RANGE", "BETWEEN", "UNBOUNDED",
    "PRECEDING", "FOLLOWING", "CURRENT_ROW", "LAG", "LEAD", "RANK",
    "DENSE_RANK", "ROW_NUMBER", "NTILE", "FIRST_VALUE", "LAST_VALUE",
    # Other reserved
    "ALL", "ANY", "SOME", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END",
    "DISTINCT", "UNIQUE", "PRIMARY", "FOREIGN", "KEY", "REFERENCES",
    "CONSTRAINT", "INDEX", "DEFAULT", "CHECK", "LIKE", "ILIKE", "RLIKE",
    "REGEXP", "SIMILAR", "ESCAPE", "COLLATE", "NATURAL", "CROSS", "INNER",
    "OUTER", "LEFT", "RIGHT", "FULL", "SEMI", "ANTI", "LATERAL", "PIVOT",
    "UNPIVOT", "SAMPLE", "TABLESAMPLE", "FLATTEN", "RECURSIVE", "CONNECT",
    "START", "PRIOR", "LEVEL", "ROWNUM",
    # Boolean/null
    "ASC", "DESC", "NULLS", "FIRST", "LAST",
    # Transaction
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "TRANSACTION",
    # Conditional
    "IF", "ELSIF", "ELSEIF", "LOOP", "WHILE", "FOR", "RETURN", "EXECUTE",
    "CALL", "DECLARE", "LET", "RESULT", "RESULTS",
    # File format
    "FORMAT", "FILE", "PATTERN", "TYPE", "LOCATION", "CREDENTIALS",
    "ENCRYPTION", "COMMENT", "RETURNS", "LANGUAGE", "RUNTIME_VERSION",
})


# SQL functions that look like table references in expressions (TABLE.something)
# but are actually function call prefixes or SQL keywords.
SQL_FUNCTION_NAMES: frozenset[str] = frozenset({
    "SUM", "AVG", "MIN", "MAX", "COUNT", "DIV0", "DIV0NULL",
    "IFF", "COALESCE", "NULLIF", "NVL", "NVL2", "ZEROIFNULL", "IFNULL",
    "CASE", "WHEN", "THEN", "ELSE", "END", "OVER",
    "DISTINCT", "CAST", "TRY_CAST", "TRY_TO_NUMBER", "TRY_TO_DATE",
    "DATE_TRUNC", "DATE_PART", "DATEPART",
    "DATEDIFF", "DATEADD", "TIMESTAMPDIFF", "TIMESTAMPADD",
    "CURRENT_DATE", "CURRENT_TIMESTAMP", "CURRENT_TIME",
    "SYSDATE", "GETDATE", "CONVERT_TIMEZONE",
    "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND", "QUARTER", "WEEK",
    "DAYOFWEEK", "DAYOFYEAR", "MONTHNAME", "DAYNAME",
    "EXTRACT", "TO_DATE", "TO_TIMESTAMP", "TO_CHAR", "TO_VARCHAR",
    "TO_NUMBER", "TO_DECIMAL", "TO_DOUBLE", "TO_BOOLEAN",
    "UPPER", "LOWER", "TRIM", "LTRIM", "RTRIM", "REPLACE", "SUBSTRING",
    "SUBSTR", "LEFT", "RIGHT", "LENGTH", "LEN", "LPAD", "RPAD",
    "CONCAT", "CONCAT_WS", "SPLIT", "SPLIT_PART",
    "ABS", "CEIL", "CEILING", "FLOOR", "ROUND", "TRUNC", "TRUNCATE",
    "MOD", "POWER", "POW", "SQRT", "SIGN", "LOG", "LN", "EXP",
    "GREATEST", "LEAST",
    "ARRAY_AGG", "ARRAY_SIZE", "ARRAY_CONSTRUCT",
    "OBJECT_CONSTRUCT", "PARSE_JSON", "TRY_PARSE_JSON",
    "LISTAGG", "MEDIAN", "PERCENTILE_CONT", "PERCENTILE_DISC",
    "STDDEV", "VARIANCE", "CORR", "REGR_SLOPE",
    "HASH", "MD5", "SHA1", "SHA2",
    "DECODE", "EQUAL_NULL", "IS_NULL_VALUE",
    "TYPEOF", "SYSTEM$TYPEOF",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "NTILE",
    "FIRST_VALUE", "LAST_VALUE", "LAG", "LEAD",
    "CONDITIONAL_TRUE_EVENT", "CONDITIONAL_CHANGE_EVENT",
    "APPROX_COUNT_DISTINCT", "HLL", "HLL_ACCUMULATE",
    "BOOLAND_AGG", "BOOLOR_AGG", "BITOR_AGG", "BITAND_AGG",
    "ANY_VALUE", "MINHASH", "APPROXIMATE_SIMILARITY",
})


class IdentifierSanitizer:
    """
    Unified identifier sanitization for Snowflake SQL.

    All identifier handling — column names, table aliases, table names,
    DAX qualifiers — flows through this single class to guarantee
    consistent quoting, casing, and reserved-word handling across the
    entire SemaBridge pipeline.

    Controls:
        force_uppercase:  bool — Force all identifiers to UPPER (default True).
        always_quote:     bool — Always wrap in double-quotes (default True).
        suppress_reserved: bool — Prefix reserved words with ``L_`` (default True).
        additional_reserved: set — Extra reserved words to supplement the default set.
    """

    def __init__(
        self,
        *,
        force_uppercase: bool = True,
        always_quote: bool = True,
        suppress_reserved: bool = True,
        additional_reserved: Optional[Set[str]] = None,
    ):
        self.force_uppercase = force_uppercase
        self.always_quote = always_quote
        self.suppress_reserved = suppress_reserved
        self._reserved = SNOWFLAKE_RESERVED_WORDS | (additional_reserved or set())

    # ─── Core sanitisation ──────────────────────────────────────────────

    def sanitize_column(self, name: str) -> str:
        """Sanitize a column name.

        - Strips DAX table qualifiers (``'Table'[Column]`` → ``Column``).
        - Splits dot-notation (``TABLE.COLUMN`` → ``COLUMN``).
        - Replaces non-alphanumeric characters with ``_``.
        - Collapses consecutive underscores, strips leading/trailing ``_``.
        - Uppercases (when configured).

        Returns:
            Sanitized column identifier (NOT quoted).  Call :meth:`quote`
            separately when DDL quoting is required.
        """
        if not name:
            return "UNKNOWN"

        # Strip DAX table qualifier
        bracket_match = re.search(r"\[(.+?)\]", name)
        if bracket_match:
            name = bracket_match.group(1)

        # Module 2: Split dot-notation — TABLE.COLUMN → COLUMN.
        # Only when *both* parts are simple identifiers (no spaces, no
        # expressions, no quoted segments).  Three-part names like
        # DB.SCHEMA.TABLE are NOT column references — leave as-is.
        if '.' in name and not name.startswith('"'):
            parts = name.split('.')
            if len(parts) == 2:
                left, right = parts
                if re.fullmatch(r'[A-Za-z_]\w*', left) and re.fullmatch(r'[A-Za-z_]\w*', right):
                    name = right  # discard the table qualifier

        clean = re.sub(r"[^a-zA-Z0-9]", "_", name)
        clean = re.sub(r"_+", "_", clean).strip("_")

        if not clean:
            return "COLUMN_UNKNOWN"

        return clean.upper() if self.force_uppercase else clean

    def sanitize_alias(self, name: str) -> str:
        """Sanitize a table alias, applying reserved-word prefixing.

        Same cleaning as :meth:`sanitize_column` plus reserved-word
        collision avoidance (``TABLE`` → ``L_TABLE``).
        """
        clean = self.sanitize_column(name)

        if self.suppress_reserved:
            if clean in self._reserved or (clean and clean[0].isdigit()):
                clean = f"L_{clean}"

        return clean

    def sanitize_table_name(self, name: str) -> str:
        """Sanitize a physical table name for Snowflake.

        Like :meth:`sanitize_column` but tailored for table-level
        identifiers — strips special characters, uppercases.
        """
        if not name:
            return name

        clean = re.sub(r"[^A-Za-z0-9_]", "_", name)
        clean = re.sub(r"_+", "_", clean).strip("_")
        return clean.upper() if self.force_uppercase else clean

    def quote(self, identifier: str) -> str:
        """Wrap an identifier in double-quotes for Snowflake.

        If the identifier is already quoted, returns as-is.
        """
        if not identifier:
            return identifier
        if identifier.startswith('"') and identifier.endswith('"'):
            return identifier
        return f'"{identifier}"'

    def sanitize_and_quote(self, name: str) -> str:
        """Sanitize then quote a column identifier in one call."""
        return self.quote(self.sanitize_column(name))

    # ─── Expression helpers ─────────────────────────────────────────────

    def is_reserved(self, name: str) -> bool:
        """Check if an identifier collides with a reserved word."""
        return name.upper() in self._reserved

    def is_sql_function(self, name: str) -> bool:
        """Check if a name is a known SQL function (not a table alias)."""
        return name.upper() in SQL_FUNCTION_NAMES

    # ─── Physical column detection ──────────────────────────────────────

    @staticmethod
    def is_physical_source_column(source_expression: Optional[str]) -> bool:
        """Determine if a source_expression represents a plain physical column.

        Calculated columns contain DAX operators / function calls and
        should be excluded from physical DDL and DIMENSIONS clauses.
        """
        if not source_expression:
            return True
        expr = source_expression.strip()
        if not expr:
            return True
        # Quick reject: DAX operators, arithmetic, or multiple bracket pairs
        # We allow a SINGLE pair of brackets if it wraps the entire identifier (e.g. [Column])
        # but reject if there are multiple brackets or other operators.
        if re.search(r"[()+\-*/=<>!&|,\n]", expr):
            return False
        
        # Check for multiple bracketed segments - e.g. "[Col1] [Col2]" or "[Col1] + [Col2]"
        # If we find more than one '[' or more than one ']', it's likely a complex expression
        if expr.count('[') > 1 or expr.count(']') > 1:
            return False
            
        return True


    # ─── Module 2: Centralized dot-notation resolution ──────────────────

    # Master regex for cross-table references: matches WORD."COL" or WORD.WORD
    _DOT_REF_RE = re.compile(
        r'\b([A-Za-z_]\w*)\s*(\.\.?\s*"[^"]+"|\.\.?(?:[A-Za-z_]\w*))'
    )
    # Simpler pattern for defence-in-depth: finds remaining TABLE. prefixes
    _TABLE_PREFIX_RE = re.compile(r'\b([A-Z_]\w*)\.')

    def resolve_dot_notation(
        self,
        expr: str,
        alias_lookup: Dict[str, str],
        *,
        sanitize_col_fn: Optional[object] = None,
    ) -> str:
        """Rewrite ``TABLE.COLUMN`` references in *expr* using *alias_lookup*.

        Replaces every ``TABLE.COLUMN`` (or ``TABLE."COL"``) where
        ``TABLE`` maps to a known alias with the sanitized alias +
        quoted column form.

        This consolidates the 6 duplicated regex rewrite closures
        previously scattered in ``generate_ddls`` and
        ``generate_ddls_from_osi``.

        Args:
            expr:  SQL expression to rewrite.
            alias_lookup: ``{RAW_TABLE_UPPER: sanitized_alias}`` mapping.
            sanitize_col_fn: Optional callable ``(col_name) -> str``.  Defaults
                to ``self.sanitize_column``.

        Returns:
            Rewritten expression.
        """
        _sanitize = sanitize_col_fn or self.sanitize_column

        def _rewrite(m: re.Match) -> str:
            raw_table = m.group(1).upper()
            dot_rest = m.group(2)  # e.g. ."COL" or .COL
            resolved = alias_lookup.get(raw_table)
            if resolved:
                col_part = dot_rest.lstrip('. ')
                if not col_part.startswith('"'):
                    col_part = f'"{ _sanitize(col_part) }"'
                return f"{resolved}.{col_part}"
            return m.group(0)  # leave untouched

        return self._DOT_REF_RE.sub(_rewrite, expr)

    def validate_table_refs(
        self,
        expr: str,
        valid_aliases: Set[str],
        valid_functions: Optional[Set[str]] = None,
    ) -> List[str]:
        """Return unresolved ``TABLE.`` prefix references in *expr*.

        Any ``TABLE.`` prefix that is not in *valid_aliases* and not a
        known SQL function is returned as an invalid reference.

        Args:
            expr: SQL expression to inspect.
            valid_aliases: Set of valid table aliases (uppercase).
            valid_functions: Set of SQL function names to skip.
                Defaults to :data:`SQL_FUNCTION_NAMES`.

        Returns:
            List of invalid table reference strings.
        """
        funcs = valid_functions if valid_functions is not None else SQL_FUNCTION_NAMES
        refs = self._TABLE_PREFIX_RE.findall(expr)
        return [
            t for t in refs
            if t not in valid_aliases and t not in funcs
        ]

    @staticmethod
    def split_dot_identifier(name: str) -> Tuple[Optional[str], str]:
        """Split a potentially dot-qualified identifier.

        Handles:
          - ``COLUMN``           → ``(None, "COLUMN")``
          - ``TABLE.COLUMN``     → ``("TABLE", "COLUMN")``
          - ``SCHEMA.TABLE.COL`` → ``("TABLE", "COL")``  (last 2 parts)
          - ``DB.SCH.TBL.COL``   → ``("TBL", "COL")``

        Quoted segments (``"My Col"``) are preserved.

        Returns:
            Tuple of ``(table_or_none, column)``.
        """
        if not name or '.' not in name:
            return (None, name or "")

        # Split on dots, respecting double-quoted segments
        parts: List[str] = []
        current: List[str] = []
        in_quote = False
        for ch in name:
            if ch == '"':
                in_quote = not in_quote
                current.append(ch)
            elif ch == '.' and not in_quote:
                parts.append(''.join(current))
                current = []
            else:
                current.append(ch)
        parts.append(''.join(current))

        if len(parts) == 1:
            return (None, parts[0])
        # Take the last two parts (TABLE.COLUMN)
        return (parts[-2], parts[-1])
