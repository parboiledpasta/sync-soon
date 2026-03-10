"""
DAX to SQL Translator.

Implements the "Tiered Safety" translation strategy to convert Fabric DAX expressions
into Snowflake-compatible SQL for Semantic Views.
"""

import re
from typing import Optional, Tuple, Dict, List, Any

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer

logger = get_logger(__name__)


class DAXTranslationResult:
    """Result of a DAX translation attempt."""
    
    def __init__(self, sql: Optional[str], tier: int, original_dax: str, source: str = "rule"):
        self.sql = sql
        self.tier = tier  # 0=Override, 1=Direct, 2=Branching/Arith, 3=Time Intel, 4=LLM/Complex
        self.original_dax = original_dax
        self.is_success = sql is not None
        self.source = source  # "rule", "llm", "override", "manual"


class DAXTranslator:
    """
    Translates DAX expressions to SQL.
    
    Tier 0: Manual Overrides & Recursion
    Tier 1: Direct Aggregations (SUM, AVG, MIN, MAX, COUNT, DISTINCTCOUNT)
    Tier 2: Arithmetic & Branching (A + B, A / B, DIVIDE)
    Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD) - Window functions
    Tier 4: LLM-based conversion (complex DAX via AI model API)
    """
    
    # Regex patterns for Tier 1
    # Matches: FUNC('Table'[Column]) or FUNC([Column])
    # Groups: 1=func, 2=table_name (optional), 3=column (with table), 4=column (without table)
    _TIER1_PATTERN = re.compile(
        r"^\s*(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*(?:'?([\w\s]+?)'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$",
        re.IGNORECASE
    )
    
    # Simple Arithmetic Patterns
    # Matches: [Measure1] + [Measure2] 
    # Matches: [Measure1] - [Measure2]
    # Matches: [Measure1] * [Measure2]
    # Matches: [Measure1] / [Measure2]
    # Very basic parser - assumes simple structure
    _ARITHMETIC_PATTERN = re.compile(
        r"^\s*(\[.+?\])\s*([\+\-\*\/])\s*(\[.+?\])\s*$",
        re.IGNORECASE
    )
    
    # DIVIDE functionality
    _DIVIDE_PATTERN = re.compile(
        r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*(?:,.+?)?\)\s*$",
        re.IGNORECASE
    )
    
    # Time Intelligence patterns that CAN be translated to Snowflake window functions
    TIME_INTEL_PATTERNS = {
        # TOTALYTD(SUM('Table'[Column]), 'Date'[Date])
        "YTD": re.compile(
            r"TOTALYTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "MTD": re.compile(
            r"TOTALMTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "QTD": re.compile(
            r"TOTALQTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
    }
    
    # Time Intelligence functions that require Date dimension context
    TIME_INTEL_FUNCTIONS = [
        "TOTALYTD", "TOTALMTD", "TOTALQTD", 
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
        "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
        "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"
    ]
    
    # Patterns that CANNOT be safely translated (require DAX engine evaluation)
    UNSUPPORTED_PATTERNS = [
        r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(",          # CALCULATE with FILTER
        r"SUMX\s*\(\s*FILTER\s*\(",                      # SUMX over filtered table
        r"EARLIER\s*\(",                                 # Row context reference
        r"RANKX\s*\(",                                   # Ranking (requires full context)
        r"USERELATIONSHIP\s*\(",                         # Dynamic relationship
        r"CROSSFILTER\s*\(",                             # Cross filter modification
        r"ALL\s*\([^)]*\)\s*\)",                         # ALL alone is complex
        r"ALLEXCEPT\s*\(",                               # Removes filters except specified
        r"VALUES\s*\([^)]+\)\s*\)",                      # Context-dependent values
    ]
    
    def __init__(self, llm_config=None):
        """
        Initialize DAXTranslator with optional LLM support.
        
        Args:
            llm_config: LLMProjectConfig instance. If None, loads from config.yaml.
                        Pass False to explicitly disable LLM without loading config.
        """
        self.llm_converter = None
        
        if llm_config is False:
            # Explicitly disabled — skip config loading
            return
        
        try:
            if llm_config is None:
                from semabridge.core.llm_config import LLMConfigManager
                llm_config = LLMConfigManager.load_config()
            
            if llm_config.llm.enabled:
                from semabridge.converter.llm_converter import LLMConverter
                self.llm_converter = LLMConverter(llm_config.llm)
                logger.info("LLM-based Tier 4 translation enabled")
        except Exception as e:
            logger.warning(f"LLM initialization skipped: {e}")
            self.llm_converter = None

    def translate(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str, 
                  overrides: Dict[str, str] = None,
                  metric_name: str = None,
                  metrics_context: List[Any] = None) -> DAXTranslationResult:
        """
        Translate a DAX expression to SQL.
        
        Args:
            dax: The DAX formula string
            table_alias: SQL alias for the main table (e.g. 'sales')
            dataset_name: Name of the dataset for context
            overrides: Dictionary of metric_name -> manual_sql
            metric_name: Name of the current metric being translated
            metrics_context: List of SMLMetric objects to resolve dependencies
        """
        if not dax:
            return DAXTranslationResult(None, 3, "")
        
        clean_dax = dax.strip()
        overrides = overrides or {}
        
        # Tier 0: Manual Overrides
        # Priority 1: Check if THIS metric has an override
        if metric_name and metric_name in overrides:
            logger.info(f"Using manual SQL override for metric '{metric_name}'")
            return DAXTranslationResult(overrides[metric_name], 0, clean_dax)
            
        # Priority 2: Check if exact DAX string matches an override key (rare but possible)
        if clean_dax in overrides:
            return DAXTranslationResult(overrides[clean_dax], 0, clean_dax)
        
        # Tier 1: Direct Aggregations
        tier1_sql = self._try_tier1(clean_dax, table_alias, dataset_name)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)
        
        # Tier 2: Branching & Arithmetic
        # e.g. [Net Sales] = [Gross Sales] - [Discounts]
        if metrics_context:
            tier2_sql = self._try_branching(clean_dax, metrics_context, overrides)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)
        
        # Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD)
        # Uses Snowflake window functions for period-to-date calculations
        tier3_sql = self.try_tier3_time_intel(clean_dax, table_alias)
        if tier3_sql:
            # TODO: Fix Cortex Analyst metric validation for window functions
            # Currently fails with "Window functions in a metric must operate over other metrics..."
            # Returning None falls back to Tier 4 (LLM or metadata only).
            pass
            # return DAXTranslationResult(tier3_sql, 3, clean_dax)
        
        # Tier 4: LLM-based translation (when enabled)
        if self.llm_converter:
            logger.info(f"Tier 1-3 failed for '{metric_name or clean_dax[:60]}', attempting LLM (Tier 4)")
            tier4_sql = self.llm_converter.convert(clean_dax, dialect="snowflake")
            if tier4_sql:
                logger.info(f"LLM Tier 4 translation succeeded for '{metric_name or clean_dax[:60]}'")
                return DAXTranslationResult(tier4_sql, 4, clean_dax, source="llm")
            else:
                logger.warning(f"LLM Tier 4 translation failed for '{metric_name or clean_dax[:60]}'")
        
        # All tiers exhausted — return None
        return DAXTranslationResult(None, 4, clean_dax)
    
    def _try_tier1(self, dax: str, table_alias: str, dataset_name: str = "") -> Optional[str]:
        """Attempt Tier 1 translation.

        Rejects cross-table references where the DAX expression
        references a different table than the metric's own dataset.
        e.g. AVERAGE(Sentiment[Score]) on a SalesFact metric would
        incorrectly emit SALESFACT."SCORE" if not rejected.

        Args:
            dax: The DAX expression string.
            table_alias: SQL alias for the metric's dataset.
            dataset_name: Name of the metric's owning dataset.

        Returns:
            SQL expression string or None if translation not possible.
        """
        match = self._TIER1_PATTERN.match(dax)
        if not match:
            return None
        
        func = match.group(1).upper()
        # Group 2 = DAX table name (optional), Group 3 = column (with table),
        # Group 4 = column (without table qualifier)
        dax_table_name = match.group(2)
        col_name = match.group(3) or match.group(4)
        
        # Reject cross-table references: if the DAX expression explicitly
        # references a different table, the metric cannot be safely emitted
        # under the current dataset's alias.
        if dax_table_name and dataset_name:
            dax_table_clean = dax_table_name.strip().strip("'").upper()
            dataset_clean = dataset_name.strip().upper()
            if dax_table_clean != dataset_clean:
                logger.info(
                    f"Tier 1 rejected: DAX references table '{dax_table_name}' "
                    f"but metric belongs to '{dataset_name}'"
                )
                return None
        
        # Map DAX function to SQL function
        func_map = {
            "SUM": "SUM",
            "AVERAGE": "AVG",
            "MIN": "MIN",
            "MAX": "MAX",
            "COUNT": "COUNT",
            "DISTINCTCOUNT": "COUNT(DISTINCT {col})"
        }
        
        col_ref = f"{table_alias}.{self._quote(col_name)}"
        sql_template = func_map.get(func)
        
        if not sql_template:
            return None
            
        if "{col}" in sql_template:
            return sql_template.format(col=col_ref)
        else:
            return f"{sql_template}({col_ref})"
    
    def _try_branching(self, dax: str, metrics: List[Any], overrides: Dict[str, str]) -> Optional[str]:
        """
        Attempt to resolve references to other measures.
        Handles simple cases:
        1. Single measure ref: [Measure]
        2. Simple arithmetic: [A] + [B]
        3. DIVIDE: DIVIDE([A], [B])
        """
        
        # Helper to resolve a single [MeasureName]
        def resolve_measure(ref_str: str) -> Optional[str]:
            name = ref_str.strip('[]')
            
            # Check override first
            if name in overrides:
                return overrides[name]
                
            # Find metric in context
            for m in metrics:
                if m.unique_name == name:
                    # If the referenced metric has SQL, use it
                    # Note: We rely on the fact that simple metrics were processed first 
                    # or that we can get their simple translation
                    if m.sql_expression:
                        return m.sql_expression
                    elif m.expression:
                        # Try to translate it on-the-fly (limited depth recursion)
                        # We don't have table aliases here easily, so this is risky if it's Tier 1
                        pass
            return None

        # Case 1: DIVIDE([A], [B])
        div_match = self._DIVIDE_PATTERN.match(dax)
        if div_match:
            num_ref = div_match.group(1)
            den_ref = div_match.group(2)
            
            num_sql = resolve_measure(num_ref)
            den_sql = resolve_measure(den_ref)
            
            if num_sql and den_sql:
                # Safe division in Snowflake
                return f"DIV0({num_sql}, {den_sql})"
        
        # Case 2: Arithmetic [A] op [B]
        arith_match = self._ARITHMETIC_PATTERN.match(dax)
        if arith_match:
            left_ref = arith_match.group(1)
            op = arith_match.group(2)
            right_ref = arith_match.group(3)
            
            left_sql = resolve_measure(left_ref)
            right_sql = resolve_measure(right_ref)
            
            if left_sql and right_sql:
                return f"({left_sql} {op} {right_sql})"
                
        # Case 3: Recursion for simple branching [Measure]
        # Regex to find all [Measure] tokens
        # This is a general replacement strategy for expressions like [A] - [B] + [C]
        # But we need to be careful not to replace Table[Col] logic if mixed
        
        # Only attempt this if it looks like a purely measure-based expression
        # i.e., doesn't contain CALCULATE, SUM, etc.
        if "CALCULATE" not in dax.upper() and "(" not in dax:
             # Find all [Tags]
             measure_refs = re.findall(r"\[([^\]]+)\]", dax)
             if not measure_refs:
                 return None
                 
             current_sql = dax
             resolved_all = True
             
             for ref in measure_refs:
                 sql = resolve_measure(f"[{ref}]")
                 if not sql:
                     resolved_all = False
                     break
                 # Replace [Name] with (SQL)
                 current_sql = current_sql.replace(f"[{ref}]", f"({sql})")
                 
             if resolved_all:
                 return current_sql
                 
        return None
    
    def _quote(self, identifier: str) -> str:
        """Quote SQL identifier using unified IdentifierSanitizer (Mandate 1)."""
        sanitizer = IdentifierSanitizer()
        return sanitizer.sanitize_and_quote(identifier)
    
    def analyze_complexity(self, dax: str) -> dict:
        """
        Analyze DAX expression complexity and return metadata for sync decisions.
        
        Returns:
            dict with keys:
                - tier: int (1-4)
                - requires_time_intel: bool
                - group_by_dimensions: list[str]
                - depends_on_measures: list[str]
                - sync_enabled: bool
                - failure_reason: Optional[str]
        """
        if not dax:
            return {
                "tier": 0,
                "requires_time_intel": False,
                "group_by_dimensions": [],
                "depends_on_measures": [],
                "sync_enabled": False,
                "failure_reason": "Empty expression"
            }
        
        clean_dax = dax.strip()
        upper_dax = clean_dax.upper()
        
        result = {
            "tier": 1,
            "requires_time_intel": False,
            "group_by_dimensions": [],
            "depends_on_measures": [],
            "sync_enabled": True,
            "failure_reason": None
        }
        
        # Extract measure dependencies [MeasureName]
        measure_refs = re.findall(r"\[([^\]]+)\]", clean_dax)
        # Filter out column refs (those from 'Table'[Column] patterns)
        table_col_pattern = re.findall(r"'[\w\s]+'\[([^\]]+)\]", clean_dax)
        pure_measure_refs = [m for m in measure_refs if m not in table_col_pattern]
        result["depends_on_measures"] = list(set(pure_measure_refs))
        
        # Check for unsupported patterns first (Tier 4)
        for pattern in self.UNSUPPORTED_PATTERNS:
            if re.search(pattern, upper_dax, re.IGNORECASE):
                result["tier"] = 4
                result["sync_enabled"] = False
                result["failure_reason"] = f"Unsupported DAX pattern detected"
                return result
        
        # Check for Time Intelligence functions (Tier 3)
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                result["tier"] = 3
                result["requires_time_intel"] = True
                # Time Intelligence requires Date dimension for proper evaluation
                result["group_by_dimensions"] = ["'Calendar'[Date]"]
                
                # Check if we can translate this specific pattern
                can_translate = False
                for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
                    if pattern.search(clean_dax):
                        can_translate = True
                        break
                
                if not can_translate:
                    # Complex Time Intelligence we can't translate
                    result["sync_enabled"] = False
                    result["failure_reason"] = f"Complex Time Intelligence ({func}) requires manual override"
                break
        
        # Check for simple CALCULATE without complex filters (still Tier 3)
        if "CALCULATE" in upper_dax and result["tier"] < 3:
            result["tier"] = 3
            # Simple CALCULATE might still be translatable if it's just wrapping an aggregation
            if not re.search(r"CALCULATE\s*\([^)]+,\s*\w+\s*\(", upper_dax):
                # CALCULATE with simple filter - might work
                pass
            else:
                result["sync_enabled"] = False
                result["failure_reason"] = "CALCULATE with complex filter requires manual override"
        
        # Check for arithmetic/branching (Tier 2)
        if result["tier"] == 1 and result["depends_on_measures"]:
            result["tier"] = 2
        
        return result
    
    def try_tier3_time_intel(
        self, 
        dax: str, 
        table_alias: str, 
        date_alias: str = "CALENDAR"
    ) -> Optional[str]:
        """
        Attempt Tier 3 Time Intelligence translation using Snowflake window functions.
        
        Converts DAX Time Intelligence to SQL window functions:
        - TOTALYTD -> Cumulative sum partitioned by Year
        - TOTALMTD -> Cumulative sum partitioned by Year, Month
        - TOTALQTD -> Cumulative sum partitioned by Year, Quarter
        
        Args:
            dax: The DAX expression
            table_alias: SQL alias for the measure's source table
            date_alias: SQL alias for the Date dimension table
            
        Returns:
            SQL expression or None if translation not possible
        """
        for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
            match = pattern.search(dax)
            if match:
                agg_func = match.group(1).upper()
                col_name = match.group(2) or match.group(3)
                # Groups 4 and 5 are the date table and column
                
                col_ref = f"{table_alias}.{self._quote(col_name)}"
                sql_agg = {
                    "SUM": "SUM", 
                    "AVERAGE": "AVG", 
                    "COUNT": "COUNT",
                    "MIN": "MIN",
                    "MAX": "MAX"
                }.get(agg_func, "SUM")
                
                # Generate Snowflake window function for period-to-date
                if period_type == "YTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "MTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."MONTH"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "QTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."QUARTER"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
        
        return None
    
    def get_required_dimensions(self, dax: str) -> list[str]:
        """
        Extract dimension columns that should be included in GROUP BY for proper measure evaluation.
        
        Analyzes the DAX expression to determine what dimensions are needed for
        context-dependent calculations.
        
        Returns:
            List of dimension column references (e.g., ["'Date'[Year]", "'Region'[Name]"])
        """
        dimensions = []
        upper_dax = dax.upper() if dax else ""
        
        # Time Intelligence always needs date context
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                dimensions.append("'Calendar'[Date]")
                break
        
        # Extract explicit table[column] references that might indicate required dimensions
        # Pattern: 'TableName'[ColumnName] 
        table_col_refs = re.findall(r"'([\w\s]+)'\[(\w+)\]", dax or "")
        for table, col in table_col_refs:
            dim_ref = f"'{table}'[{col}]"
            if dim_ref not in dimensions:
                # Only add if it looks like a dimension (not a measure column)
                col_upper = col.upper()
                if not any(m in col_upper for m in ["AMOUNT", "SALES", "REVENUE", "PRICE", "COST", "QTY"]):
                    dimensions.append(dim_ref)
        
        return dimensions

