"""
Snowflake Metadata Extractor.

Extracts table, column, and relationship metadata from Snowflake
using INFORMATION_SCHEMA and system functions.

Key improvements over semantic-sync:
1. Batch column extraction (single query for all tables)
2. Foreign key detection via SHOW commands
3. Efficient caching integration
4. Clean separation of concerns
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Optional

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger
from semabridge.utils.cache import MetadataCache

logger = get_logger(__name__)


class ExtractionError(Exception):
    """Raised when metadata extraction fails."""
    pass


class SnowflakeExtractor:
    """
    Extracts metadata from Snowflake for semantic model generation.
    
    Features:
    - Batch table and column extraction
    - Foreign key relationship detection
    - Primary key detection
    - Incremental extraction with caching
    - Custom _SEMANTIC_* table support
    """
    
    def __init__(
        self,
        config: SnowflakeConfig,
        cache: Optional[MetadataCache] = None,
        exclude_tables: Optional[list[str]] = None,
        include_tables: Optional[list[str]] = None,
    ):
        """
        Initialize the extractor.
        
        Args:
            config: Snowflake connection configuration
            cache: Optional metadata cache for incremental extraction
            exclude_tables: Tables to exclude (case-insensitive)
            include_tables: Tables to include (if set, only these are extracted)
        """
        self.config = config
        self.cache = cache
        self.exclude_tables = set(t.upper() for t in (exclude_tables or []))
        self.include_tables = set(t.upper() for t in (include_tables or [])) if include_tables else None
        
        # Extracted data
        self._tables: dict[str, dict[str, Any]] = {}
        self._columns: dict[str, list[dict[str, Any]]] = {}
        self._primary_keys: dict[str, list[str]] = {}
        self._foreign_keys: list[dict[str, Any]] = []
    
    @contextmanager
    def connection(self) -> Generator[SnowflakeConnection, None, None]:
        """Context manager for database connections."""
        conn = None
        try:
            logger.debug(f"Connecting to Snowflake: {self.config.account}")
            conn = snowflake.connector.connect(
                user=self.config.user,
                password=self.config.password.get_secret_value(),
                account=self.config.account,
                warehouse=self.config.warehouse,
                database=self.config.database,
                schema=self.config.schema_name,
                role=self.config.role,
            )
            yield conn
        except Exception as e:
            logger.error(f"Snowflake connection failed: {e}")
            raise ExtractionError(f"Failed to connect to Snowflake: {e}") from e
        finally:
            if conn:
                conn.close()
    
    def test_connection(self) -> bool:
        """Test Snowflake connectivity."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
            result = cur.fetchone()
            logger.info(f"Connected: {result[1]}.{result[2]} (Warehouse: {result[0]})")
            return True
    
    def extract_all(self) -> dict[str, Any]:
        """
        Extract all metadata from Snowflake.
        
        Returns:
            Dict containing:
            - tables: Dict of table_name -> table metadata
            - columns: Dict of table_name -> list of column metadata
            - primary_keys: Dict of table_name -> list of PK column names
            - foreign_keys: List of FK relationship dicts
        """
        logger.info(f"Starting metadata extraction from {self.config.database}.{self.config.schema_name}")
        
        with self.connection() as conn:
            # Extract in optimal order
            self._extract_tables(conn)
            self._extract_columns_batch(conn)
            self._extract_primary_keys(conn)
            self._extract_foreign_keys(conn)
        
        # Update cache if available
        if self.cache:
            self._update_cache()
        
        result = {
            "database": self.config.database,
            "schema": self.config.schema_name,
            "tables": self._tables,
            "columns": self._columns,
            "primary_keys": self._primary_keys,
            "foreign_keys": self._foreign_keys,
        }
        
        logger.info(
            f"Extraction complete: {len(self._tables)} tables, "
            f"{sum(len(cols) for cols in self._columns.values())} columns, "
            f"{len(self._foreign_keys)} relationships"
        )
        
        return result
    
    def list_tables(self) -> list[dict[str, Any]]:
        """
        List all tables and views in the schema.
        
        Returns:
            List of dictionaries with 'id' and 'displayName' for UI compatibility.
        """
        logger.info(f"Listing tables and views in {self.config.database}.{self.config.schema_name}...")
        
        results = []
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    TABLE_NAME,
                    TABLE_TYPE,
                    COMMENT
                FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME
            """, (self.config.schema_name.upper(),))
            
            for row in cur.fetchall():
                table_name = row[0]
                table_type = row[1]
                
                # Apply filters
                if table_name.upper() in self.exclude_tables:
                    continue
                
                if self.include_tables and table_name.upper() not in self.include_tables:
                    continue
                
                results.append({
                    "id": table_name,
                    "displayName": table_name,
                    "type": table_type,
                    "description": row[2] or ""
                })
        
        logger.info(f"Discovered {len(results)} objects in Snowflake")
        return results

    def list_semantic_views(self) -> list[dict[str, Any]]:
        """
        List all Snowflake Semantic Views in the configured schema.

        Uses ``SHOW SEMANTIC VIEWS IN SCHEMA`` which is the only reliable
        discovery mechanism for Semantic Views (they don't appear in
        INFORMATION_SCHEMA.VIEWS).

        Returns:
            List of dictionaries with 'id', 'displayName', 'type', and
            'description' for UI compatibility.
        """
        logger.info(
            f"Listing semantic views in "
            f"{self.config.database}.{self.config.schema_name}..."
        )

        results: list[dict[str, Any]] = []
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    f"SHOW SEMANTIC VIEWS IN SCHEMA "
                    f"{self.config.database}.{self.config.schema_name}"
                )
                # SHOW SEMANTIC VIEWS columns:
                #   0: created_on, 1: name, 2: kind,
                #   3: database_name, 4: schema_name,
                #   5: comment, 6: owner, 7: owner_role_type
                for row in cur.fetchall():
                    view_name = row[1]
                    comment = row[5] if len(row) > 5 else ""

                    results.append({
                        "id": view_name,
                        "displayName": view_name,
                        "type": "SEMANTIC_VIEW",
                        "description": comment or "",
                    })
            except Exception as e:
                logger.warning(
                    f"SHOW SEMANTIC VIEWS failed (feature may not be "
                    f"enabled on this account): {e}"
                )

        logger.info(f"Discovered {len(results)} semantic views in Snowflake")
        return results

    def read_semantic_view_ddl(self, sv_name: str) -> Dict[str, Any]:
        """
        Read a Snowflake Semantic View's DDL and parse its definition.

        Uses ``GET_DDL('SEMANTIC VIEW', '<name>')`` to retrieve the
        authoritative DDL, then parses the TABLES, RELATIONSHIPS,
        MEASURES, and DIMENSIONS sections.

        This gives us the **exact** relationships and measures defined
        in the SV, rather than relying on heuristic auto-detection.

        Args:
            sv_name: Name of the semantic view to read.

        Returns:
            Dict with keys: 'alias_to_table', 'tables', 'relationships',
            'measures', 'dimensions', 'ddl_raw'. Returns empty dict on error.
        """
        import re as _re

        result: Dict[str, Any] = {
            "alias_to_table": {},
            "tables": [],
            "relationships": [],
            "measures": [],
            "dimensions": [],
            "ddl_raw": "",
        }

        with self.connection() as conn:
            cur = conn.cursor()
            try:
                db = self.config.database
                schema = self.config.schema_name

                # Resolve canonical semantic view name from Snowflake metadata
                resolved_sv_name = sv_name
                try:
                    cur.execute(
                        f"SHOW SEMANTIC VIEWS IN SCHEMA {db}.{schema}"
                    )
                    available_sv_names = [r[1] for r in cur.fetchall() if len(r) > 1]
                    for candidate_name in available_sv_names:
                        if candidate_name.lower() == sv_name.lower():
                            resolved_sv_name = candidate_name
                            break
                except Exception as list_exc:
                    logger.debug(f"Could not list semantic views for name resolution: {list_exc}")

                ddl = ""
                attempt_errors: list[str] = []
                ddl_targets = [
                    f"{db}.{schema}.{resolved_sv_name}",
                    f"{db.upper()}.{schema.upper()}.{resolved_sv_name}",
                    f"{db}.{schema}.{resolved_sv_name.upper()}",
                    f"{db.upper()}.{schema.upper()}.{resolved_sv_name.upper()}",
                    f'"{db}"."{schema}"."{resolved_sv_name}"',
                    f'"{db.upper()}"."{schema.upper()}"."{resolved_sv_name.upper()}"',
                ]

                seen_targets = set()
                unique_targets = []
                for target in ddl_targets:
                    if target not in seen_targets:
                        seen_targets.add(target)
                        unique_targets.append(target)

                for target in unique_targets:
                    try:
                        logger.info(f"[DDL FETCH] GET_DDL('SEMANTIC VIEW', '{target}')")
                        cur.execute("SELECT GET_DDL('SEMANTIC VIEW', %s)", (target,))
                        row = cur.fetchone()
                        if row and row[0]:
                            ddl = row[0]
                            break
                    except Exception as attempt_exc:
                        attempt_errors.append(f"{target}: {attempt_exc}")

                if not ddl:
                    error_text = " | ".join(attempt_errors[-3:]) if attempt_errors else "no attempts recorded"
                    raise RuntimeError(
                        f"Unable to read DDL for semantic view '{sv_name}' "
                        f"(resolved='{resolved_sv_name}'). Last errors: {error_text}"
                    )

                result["ddl_raw"] = ddl
            except Exception as exc:
                logger.warning(
                    f"GET_DDL failed for semantic view '{sv_name}': {exc}. "
                    f"Falling back to auto-detection."
                )
                return result

        # ----- Parse the DDL into sections -----
        # The DDL has top-level keyword sections:
        #   TABLES ( ... )
        #   RELATIONSHIPS ( ... )
        #   DIMENSIONS ( ... )
        #   MEASURES ( ... )  or  METRICS ( ... )
        # Each section is delimited by balanced parentheses.
        
        # Diagnostic: log the raw DDL preview
        logger.info(
            f"[DDL PARSER] Raw DDL for '{sv_name}' "
            f"(len={len(ddl)}): {ddl[:200]}..."
        )

        def _extract_section(ddl_text: str, keyword: str) -> str:
            """Extract the content inside a top-level section's parentheses."""
            # Find the keyword followed by optional whitespace and '('
            pattern = _re.compile(
                rf'\b{keyword}\s*\(', _re.IGNORECASE
            )
            match = pattern.search(ddl_text)
            if not match:
                return ""
            # Walk forward to find the balanced closing paren
            start = match.end()  # right after '('
            depth = 1
            i = start
            while i < len(ddl_text) and depth > 0:
                if ddl_text[i] == '(':
                    depth += 1
                elif ddl_text[i] == ')':
                    depth -= 1
                i += 1
            return ddl_text[start:i - 1].strip()

        def _split_entries(section: str) -> list[str]:
            """Split a section into entries by commas at depth 0."""
            entries: list[str] = []
            depth = 0
            current: list[str] = []
            for ch in section:
                if ch == '(':
                    depth += 1
                    current.append(ch)
                elif ch == ')':
                    depth -= 1
                    current.append(ch)
                elif ch == ',' and depth == 0:
                    entries.append("".join(current).strip())
                    current = []
                else:
                    current.append(ch)
            remainder = "".join(current).strip()
            if remainder:
                entries.append(remainder)
            return entries

        def _unquote(s: str) -> str:
            """Remove surrounding double-quotes if present."""
            s = s.strip()
            if s.startswith('"') and s.endswith('"'):
                return s[1:-1]
            return s

        def _split_csv(text: str) -> list[str]:
            """Split comma-separated text while respecting quotes and parentheses."""
            parts: list[str] = []
            current: list[str] = []
            depth = 0
            in_quotes = False
            i = 0
            while i < len(text):
                ch = text[i]
                if ch == '"':
                    in_quotes = not in_quotes
                    current.append(ch)
                elif not in_quotes and ch == '(':
                    depth += 1
                    current.append(ch)
                elif not in_quotes and ch == ')':
                    depth -= 1
                    current.append(ch)
                elif not in_quotes and depth == 0 and ch == ',':
                    part = "".join(current).strip()
                    if part:
                        parts.append(part)
                    current = []
                else:
                    current.append(ch)
                i += 1
            remainder = "".join(current).strip()
            if remainder:
                parts.append(remainder)
            return parts

        ident = r'(?:"[^"]+"|\w+)'

        # ── TABLES section ──
        tables_text = _extract_section(ddl, "TABLES")
        logger.info(f"[DDL PARSER] TABLES section: {'FOUND' if tables_text else 'EMPTY'} ({len(tables_text)} chars)")
        alias_to_table: Dict[str, str] = {}
        if tables_text:
            for entry in _split_entries(tables_text):
                # Format: ALIAS AS "DB"."SCHEMA"."TABLE" PRIMARY KEY ("COL")
                # or:     ALIAS AS "DB"."SCHEMA"."TABLE"
                m = _re.match(
                    r'(\w+)\s+AS\s+(.+?)(?:\s+PRIMARY\s+KEY\s*\((.+?)\))?$',
                    entry.strip(), _re.IGNORECASE | _re.DOTALL,
                )
                if m:
                    alias = m.group(1).strip()
                    table_ref = m.group(2).strip()
                    pk_str = m.group(3) or ""
                    # Extract the physical table name (last part of "DB"."SCHEMA"."TABLE")
                    parts = [_unquote(p) for p in table_ref.split(".")]
                    physical_table = parts[-1] if parts else table_ref
                    alias_to_table[alias] = physical_table
                    pk_cols = [
                        _unquote(c.strip())
                        for c in pk_str.split(",") if c.strip()
                    ]
                    result["tables"].append({
                        "alias": alias,
                        "physical_table": physical_table,
                        "full_ref": table_ref,
                        "primary_keys": pk_cols,
                    })
        result["alias_to_table"] = alias_to_table
        logger.info(f"[DDL PARSER] Parsed {len(alias_to_table)} table aliases: {list(alias_to_table.items())}")

        # ── RELATIONSHIPS section ──
        rels_text = _extract_section(ddl, "RELATIONSHIPS")
        logger.info(f"[DDL PARSER] RELATIONSHIPS section: {'FOUND' if rels_text else 'EMPTY'} ({len(rels_text)} chars)")
        if rels_text:
            for entry in _split_entries(rels_text):
                # Format: FROM_ALIAS ("FK_COL") REFERENCES TO_ALIAS
                # or:     FROM_ALIAS ("FK_COL", "FK_COL2") REFERENCES TO_ALIAS
                m = _re.match(
                    rf'({ident})\s*\((.+?)\)\s+REFERENCES\s+({ident})(?:\s*\((.+?)\))?$',
                    entry.strip(), _re.IGNORECASE | _re.DOTALL,
                )
                if m:
                    from_alias = _unquote(m.group(1).strip())
                    fk_cols_str = m.group(2).strip()
                    to_alias = _unquote(m.group(3).strip())
                    to_cols_str = (m.group(4) or "").strip()
                    fk_cols = [
                        _unquote(c.strip())
                        for c in _split_csv(fk_cols_str)
                    ]
                    explicit_to_cols = [
                        _unquote(c.strip())
                        for c in _split_csv(to_cols_str)
                    ] if to_cols_str else []
                    # Resolve aliases to physical table names
                    from_table = alias_to_table.get(
                        from_alias, from_alias
                    )
                    to_table = alias_to_table.get(
                        to_alias, to_alias
                    )
                    # The referenced PK column in the to-table:
                    # find it from the TABLES section pk_cols
                    to_pk_cols: list[str] = []
                    for t_entry in result["tables"]:
                        if t_entry["alias"] == to_alias:
                            to_pk_cols = t_entry["primary_keys"]
                            break
                    # Match FK cols to PK cols by position
                    for idx, fk_col in enumerate(fk_cols):
                        to_col = ""
                        if idx < len(explicit_to_cols):
                            to_col = explicit_to_cols[idx]
                        elif idx < len(to_pk_cols):
                            to_col = to_pk_cols[idx]
                        else:
                            to_col = fk_col
                        result["relationships"].append({
                            "name": (
                                f"SV_{from_table}_{fk_col}_"
                                f"{to_table}_{to_col}"
                            ),
                            "from_table": from_table,
                            "from_column": fk_col,
                            "to_table": to_table,
                            "to_column": to_col,
                            "source": "semantic_view_ddl",
                            "confidence": 1.0,
                        })
                    continue

                # Alternate format: FROM_ALIAS."COL" = TO_ALIAS."COL"
                m_eq = _re.match(
                    rf'({ident})\.({ident})\s*=\s*({ident})\.({ident})$',
                    entry.strip(), _re.IGNORECASE,
                )
                if m_eq:
                    from_alias = _unquote(m_eq.group(1).strip())
                    from_col = _unquote(m_eq.group(2).strip())
                    to_alias = _unquote(m_eq.group(3).strip())
                    to_col = _unquote(m_eq.group(4).strip())
                    from_table = alias_to_table.get(from_alias, from_alias)
                    to_table = alias_to_table.get(to_alias, to_alias)
                    result["relationships"].append({
                        "name": f"SV_{from_table}_{from_col}_{to_table}_{to_col}",
                        "from_table": from_table,
                        "from_column": from_col,
                        "to_table": to_table,
                        "to_column": to_col,
                        "source": "semantic_view_ddl",
                        "confidence": 1.0,
                    })

        # ── MEASURES / METRICS section ──
        measures_text = (
            _extract_section(ddl, "MEASURES")
            or _extract_section(ddl, "METRICS")
        )
        logger.info(
            f"[DDL PARSER] MEASURES/METRICS section: "
            f"{'FOUND' if measures_text else 'EMPTY'} "
            f"({len(measures_text)} chars)"
        )
        if measures_text:
            for entry in _split_entries(measures_text):
                # Format: ALIAS."METRIC_NAME" AS <expression>
                m = _re.match(
                    rf'({ident})\.({ident})\s+AS\s+(.+)$',
                    entry.strip(), _re.IGNORECASE | _re.DOTALL,
                )
                if m:
                    alias = _unquote(m.group(1).strip())
                    metric_name = _unquote(m.group(2).strip())
                    expression = m.group(3).strip()
                    table_name = alias_to_table.get(alias, alias)
                    # Try to parse aggregation and column from expression
                    agg_match = _re.match(
                        r'(SUM|COUNT|AVG|AVERAGE|MIN|MAX)\s*\(\s*'
                        r'(?:DISTINCT\s+)?'
                        rf'(?:{ident}\.)?({ident})\s*\)',
                        expression, _re.IGNORECASE,
                    )
                    if agg_match:
                        agg = agg_match.group(1).upper()
                        # Map AVERAGE → AVG for SML compatibility
                        if agg == "AVERAGE":
                            agg = "AVG"
                        source_col = _unquote(agg_match.group(2))
                    else:
                        agg = "NONE"
                        source_col = ""

                    result["measures"].append({
                        "name": metric_name,
                        "table_name": table_name,
                        "table_alias": alias,
                        "expression": expression,
                        "aggregation": agg.lower(),
                        "source_column": source_col,
                        "source": "semantic_view_ddl",
                    })
                    continue

                # Alternate format: "METRIC_NAME" AS <expression> (no alias prefix)
                m_no_alias = _re.match(
                    rf'({ident})\s+AS\s+(.+)$',
                    entry.strip(), _re.IGNORECASE | _re.DOTALL,
                )
                if m_no_alias:
                    metric_name = _unquote(m_no_alias.group(1).strip())
                    expression = m_no_alias.group(2).strip()

                    # Infer table alias from expression references (if unique)
                    aliases_in_expr = {
                        _unquote(a)
                        for a, _ in _re.findall(rf'({ident})\.({ident})', expression)
                    }
                    inferred_alias = next(iter(aliases_in_expr)) if len(aliases_in_expr) == 1 else ""
                    table_name = alias_to_table.get(inferred_alias, inferred_alias)

                    agg_match = _re.match(
                        r'(SUM|COUNT|AVG|AVERAGE|MIN|MAX)\s*\(\s*'
                        r'(?:DISTINCT\s+)?'
                        rf'(?:{ident}\.)?({ident})\s*\)',
                        expression, _re.IGNORECASE,
                    )
                    if agg_match:
                        agg = agg_match.group(1).upper()
                        if agg == "AVERAGE":
                            agg = "AVG"
                        source_col = _unquote(agg_match.group(2))
                    else:
                        agg = "NONE"
                        source_col = ""

                    result["measures"].append({
                        "name": metric_name,
                        "table_name": table_name,
                        "table_alias": inferred_alias,
                        "expression": expression,
                        "aggregation": agg.lower(),
                        "source_column": source_col,
                        "source": "semantic_view_ddl",
                    })

        # ── DIMENSIONS section ──
        dims_text = _extract_section(ddl, "DIMENSIONS")
        logger.info(f"[DDL PARSER] DIMENSIONS section: {'FOUND' if dims_text else 'EMPTY'} ({len(dims_text)} chars)")
        if dims_text:
            for entry in _split_entries(dims_text):
                # Format: ALIAS."SEMANTIC_NAME" AS ALIAS."PHYS_COL"
                m = _re.match(
                    r'(\w+)\."(.+?)"\s+AS\s+(\w+)\."(.+?)"',
                    entry.strip(), _re.IGNORECASE,
                )
                if m:
                    alias = m.group(1).strip()
                    semantic_name = m.group(2).strip()
                    phys_col = m.group(4).strip()
                    table_name = alias_to_table.get(alias, alias)
                    result["dimensions"].append({
                        "table_name": table_name,
                        "table_alias": alias,
                        "semantic_name": semantic_name,
                        "physical_column": phys_col,
                    })

        logger.info(
            f"Parsed SV DDL for '{sv_name}': "
            f"{len(result['tables'])} tables, "
            f"{len(result['relationships'])} relationships, "
            f"{len(result['measures'])} measures, "
            f"{len(result['dimensions'])} dimensions"
        )
        return result

    def _extract_tables(self, conn: SnowflakeConnection) -> None:
        """Extract table metadata from INFORMATION_SCHEMA."""
        cur = conn.cursor()
        
        logger.debug("Extracting table metadata...")
        cur.execute("""
            SELECT 
                TABLE_NAME,
                TABLE_TYPE,
                ROW_COUNT,
                BYTES,
                LAST_ALTERED,
                COMMENT
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = %s
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """, (self.config.schema_name.upper(),))
        
        for row in cur.fetchall():
            table_name = row[0]
            
            # Apply filters
            if table_name.upper() in self.exclude_tables:
                logger.debug(f"Skipping excluded table: {table_name}")
                continue
            
            if self.include_tables and table_name.upper() not in self.include_tables:
                logger.debug(f"Skipping non-included table: {table_name}")
                continue
            
            self._tables[table_name] = {
                "name": table_name,
                "type": row[1],
                "row_count": row[2],
                "bytes": row[3],
                "last_altered": row[4].isoformat() if row[4] else None,
                "description": row[5] or "",
            }
        
        logger.debug(f"Found {len(self._tables)} tables")
    
    def _extract_columns_batch(self, conn: SnowflakeConnection) -> None:
        """
        Extract column metadata for all tables in a single query.
        
        This is much faster than querying per-table.
        """
        if not self._tables:
            return
        
        cur = conn.cursor()
        table_names = list(self._tables.keys())
        
        # Initialize column lists
        for table_name in table_names:
            self._columns[table_name] = []
        
        logger.debug(f"Extracting columns for {len(table_names)} tables...")
        
        # Build placeholders for IN clause
        placeholders = ", ".join(["%s"] * len(table_names))
        
        cur.execute(f"""
            SELECT 
                TABLE_NAME,
                COLUMN_NAME,
                ORDINAL_POSITION,
                DATA_TYPE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COMMENT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """, (self.config.schema_name.upper(), *table_names))
        
        for row in cur.fetchall():
            table_name = row[0]
            
            # Build full data type string
            data_type = row[3]
            if row[6]:  # Character length
                data_type = f"{data_type}({row[6]})"
            elif row[7] and row[8]:  # Numeric precision/scale
                data_type = f"{data_type}({row[7]},{row[8]})"
            
            col_info = {
                "name": row[1],
                "ordinal": row[2],
                "data_type": data_type,
                "is_nullable": row[4] == "YES",
                "default_value": row[5],
                "description": row[9] or "",
                "is_key": False,  # Updated by PK extraction
            }
            
            self._columns[table_name].append(col_info)
        
        total_cols = sum(len(cols) for cols in self._columns.values())
        logger.debug(f"Extracted {total_cols} columns")
    
    def _extract_primary_keys(self, conn: SnowflakeConnection) -> None:
        """Extract primary key information using SHOW commands."""
        cur = conn.cursor()
        
        logger.debug("Extracting primary keys...")
        
        for table_name in self._tables:
            try:
                cur.execute(f'SHOW PRIMARY KEYS IN TABLE "{self.config.schema_name}"."{table_name}"')
                pk_cols = []
                for row in cur.fetchall():
                    pk_col = row[4]  # Column name is at index 4
                    pk_cols.append(pk_col)
                    
                    # Mark column as key
                    for col in self._columns.get(table_name, []):
                        if col["name"] == pk_col:
                            col["is_key"] = True
                
                if pk_cols:
                    self._primary_keys[table_name] = pk_cols
            except Exception as e:
                logger.debug(f"Could not get PKs for {table_name}: {e}")
        
        logger.debug(f"Found primary keys for {len(self._primary_keys)} tables")
    
    def _extract_foreign_keys(self, conn: SnowflakeConnection) -> None:
        """Extract foreign key relationships using SHOW commands.
        
        SHOW IMPORTED KEYS output format (index positions):
        0: created_on
        1: pk_database_name
        2: pk_schema_name
        3: pk_table_name
        4: pk_column_name
        5: fk_database_name
        6: fk_schema_name
        7: fk_table_name
        8: fk_column_name
        9: key_sequence
        10: update_rule
        11: delete_rule
        12: fk_name (constraint name)
        13: pk_name
        14: deferrability
        15: rely
        """
        cur = conn.cursor()
        
        logger.debug("Extracting foreign keys...")
        
        for table_name in self._tables:
            try:
                cur.execute(f'SHOW IMPORTED KEYS IN TABLE "{self.config.schema_name}"."{table_name}"')
                for row in cur.fetchall():
                    # Get FK constraint name (index 12), fallback to generated name
                    fk_name = row[12] if len(row) > 12 and row[12] else None
                    
                    # Extract table and column info (correct indices)
                    from_table = row[7]  # fk_table_name
                    from_column = row[8]  # fk_column_name
                    to_table = row[3]     # pk_table_name
                    to_column = row[4]    # pk_column_name
                    
                    # Generate unique name if FK name is missing or is a rule name
                    if not fk_name or fk_name in ("NO ACTION", "CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT"):
                        fk_name = f"FK_{from_table}_{from_column}_{to_table}_{to_column}"
                    
                    fk_info = {
                        "name": fk_name,
                        "from_table": from_table,
                        "from_column": from_column,
                        "to_table": to_table,
                        "to_column": to_column,
                    }
                    self._foreign_keys.append(fk_info)
            except Exception as e:
                logger.debug(f"Could not get FKs for {table_name}: {e}")
        
        logger.debug(f"Found {len(self._foreign_keys)} foreign key relationships")
    
    def _update_cache(self) -> None:
        """Update the metadata cache with extracted data."""
        if not self.cache:
            return
        
        for table_name, columns in self._columns.items():
            col_hash = self.cache._compute_hash(columns)
            table_info = self._tables.get(table_name, {})
            
            self.cache.set_table_hash(
                database=self.config.database,
                schema=self.config.schema_name,
                table_name=table_name,
                column_hash=col_hash,
                row_count=table_info.get("row_count", 0),
                last_altered=table_info.get("last_altered"),
            )
    
    def get_table_info(self, table_name: str) -> Optional[dict[str, Any]]:
        """Get metadata for a specific table."""
        return self._tables.get(table_name)
    
    def get_columns(self, table_name: str) -> list[dict[str, Any]]:
        """Get columns for a specific table."""
        return self._columns.get(table_name, [])
    
    def get_primary_key(self, table_name: str) -> list[str]:
        """Get primary key columns for a table."""
        return self._primary_keys.get(table_name, [])
    
    def get_foreign_keys(self) -> list[dict[str, Any]]:
        """Get all foreign key relationships."""
        return self._foreign_keys
    
    def read_semantic_tables(self) -> dict[str, Any]:
        """
        Read existing semantic metadata from _SEMANTIC_* tables.
        
        Returns:
            Dict with 'metadata', 'measures', 'relationships', 'columns' keys
        """
        result = {
            "metadata": [],
            "measures": [],
            "relationships": [],
            "columns": [],
        }
        
        with self.connection() as conn:
            cur = conn.cursor()
            
            # Check which semantic tables exist
            cur.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME LIKE '_SEMANTIC%%'
            """, (self.config.schema_name.upper(),))
            
            existing = {row[0] for row in cur.fetchall()}
            
            # Read _SEMANTIC_MEASURES
            if "_SEMANTIC_MEASURES" in existing:
                try:
                    cur.execute("""
                        SELECT MEASURE_NAME, TABLE_NAME, EXPRESSION, DESCRIPTION,
                               DISPLAY_FOLDER, FORMAT_STRING, IS_HIDDEN, DATA_TYPE
                        FROM _SEMANTIC_MEASURES
                    """)
                    for row in cur.fetchall():
                        result["measures"].append({
                            "name": row[0],
                            "table_name": row[1],
                            "expression": row[2],
                            "description": row[3] or "",
                            "folder": row[4],
                            "format_string": row[5],
                            "is_hidden": row[6] or False,
                            "data_type": row[7],
                        })
                    logger.info(f"Found {len(result['measures'])} existing measures")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_MEASURES: {e}")
            
            # Read _SEMANTIC_RELATIONSHIPS
            if "_SEMANTIC_RELATIONSHIPS" in existing:
                try:
                    cur.execute("""
                        SELECT RELATIONSHIP_NAME, FROM_TABLE, FROM_COLUMN,
                               TO_TABLE, TO_COLUMN, CARDINALITY, IS_ACTIVE
                        FROM _SEMANTIC_RELATIONSHIPS
                    """)
                    for row in cur.fetchall():
                        result["relationships"].append({
                            "name": row[0],
                            "from_table": row[1],
                            "from_column": row[2],
                            "to_table": row[3],
                            "to_column": row[4],
                            "cardinality": row[5] or "many-to-one",
                            "is_active": row[6] if row[6] is not None else True,
                        })
                    logger.info(f"Found {len(result['relationships'])} existing relationships")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_RELATIONSHIPS: {e}")
            
            # Read _SEMANTIC_COLUMNS for additional column metadata
            if "_SEMANTIC_COLUMNS" in existing:
                try:
                    cur.execute("""
                        SELECT TABLE_NAME, COLUMN_NAME, DISPLAY_NAME, DESCRIPTION,
                               IS_HIDDEN, FORMAT_STRING, FOLDER
                        FROM _SEMANTIC_COLUMNS
                    """)
                    for row in cur.fetchall():
                        result["columns"].append({
                            "table_name": row[0],
                            "column_name": row[1],
                            "display_name": row[2],
                            "description": row[3] or "",
                            "is_hidden": row[4] or False,
                            "format_string": row[5],
                            "folder": row[6],
                        })
                    logger.info(f"Found {len(result['columns'])} column metadata records")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_COLUMNS: {e}")
        
        return result
