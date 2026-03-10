"""
LLM-Based DAX to SQL Converter.

Uses configured LLM API client to convert complex DAX expressions
that cannot be handled by rule-based Tier 1-3 translators.

This module handles prompt engineering, response parsing,
and SQL extraction for Tier 4 translation.
"""

from __future__ import annotations

import re
import time
from typing import Optional

from semabridge.core.llm_config import LLMConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

# SQL code block extraction patterns
_SQL_BLOCK_PATTERN = re.compile(
    r"```(?:sql)?\s*\n?(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)

_BARE_SQL_PATTERN = re.compile(
    r"((?:SELECT|SUM|AVG|COUNT|MIN|MAX|CASE)\b.*)",
    re.DOTALL | re.IGNORECASE,
)

# Dangerous SQL guard
_DANGEROUS_KEYWORDS = {"DROP", "DELETE", "ALTER", "INSERT", "CREATE", "TRUNCATE"}


PROMPT_TEMPLATE = """You are a DAX to SQL translator. Convert the following DAX expression to {dialect}-compatible SQL.

Requirements:
- Generate ONLY the SQL expression, no explanations or commentary
- Use {dialect} SQL syntax
- Preserve the semantic meaning of the DAX expression
- Use standard SQL functions where possible
- For aggregations, use SUM, AVG, COUNT, MIN, MAX
- For time intelligence, use CASE WHEN with date comparisons
- For CALCULATE with FILTER, use CASE WHEN conditions
- Output a single SQL expression (not a full SELECT statement)
- Do NOT include CREATE VIEW, SELECT FROM, or any DDL
- Reference table columns using TABLE_ALIAS."COLUMN_NAME" format

Examples:

DAX: SUM('Sales'[Amount])
SQL: SUM(SALES."AMOUNT")

DAX: CALCULATE(SUM('Sales'[Amount]), 'Product'[Category] = "Electronics")
SQL: SUM(CASE WHEN PRODUCT."CATEGORY" = 'Electronics' THEN SALES."AMOUNT" ELSE 0 END)

DAX: TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])
SQL: SUM(CASE WHEN CALENDAR."YEAR" = YEAR(CURRENT_DATE) 
              AND CALENDAR."PERIOD" <= MONTH(CURRENT_DATE)
              THEN FACT."REVENUE" ELSE 0 END)

DAX Expression:
{dax_expression}

Generate the SQL expression only:
"""


class LLMConverter:
    """
    Converts DAX expressions to SQL using LLM APIs.

    This is the Tier 4 translation strategy, invoked only when
    rule-based Tier 1-3 translators fail.
    """

    def __init__(self, config: LLMConfig):
        self.config = config
        self._api_client = None

        if not config.enabled:
            logger.info("LLM disabled in configuration")
            return

        try:
            from semabridge.converter.api_client import APIClient
            self._api_client = APIClient(config)
            logger.info("LLM converter initialized successfully")
        except Exception as e:
            logger.warning(f"LLM converter initialization failed: {e}")
            self._api_client = None

    def convert(self, dax: str, dialect: str = "snowflake") -> Optional[str]:
        if self._api_client is None:
            logger.warning("LLM API client not available, skipping conversion")
            return None

        if not dax or not dax.strip():
            return None

        logger.info(f"Attempting LLM DAX→SQL conversion (dialect={dialect})")

        try:
            prompt = self._build_prompt(dax, dialect)
            start_time = time.time()

            response = self._api_client.call_llm(prompt)

            elapsed = time.time() - start_time

            if not response:
                logger.warning("LLM returned empty response")
                return None

            sql = self._extract_sql(response)

            if not sql:
                logger.warning("Failed to extract SQL from LLM response")
                return None

            if self._contains_dangerous_sql(sql):
                logger.error("Dangerous SQL detected in LLM output")
                return None

            logger.info(f"LLM DAX→SQL conversion succeeded in {elapsed:.2f}s")
            return sql

        except Exception as e:
            logger.error(f"Unexpected error in LLM conversion: {e}")
            return None

    def _build_prompt(self, dax: str, dialect: str) -> str:
        dialect_display = dialect.upper().replace("_", " ")
        return PROMPT_TEMPLATE.format(
            dialect=dialect_display,
            dax_expression=dax,
        )

    def _extract_sql(self, response: str) -> Optional[str]:
        text = response.strip()

        # 1️⃣ Extract from fenced code block
        match = _SQL_BLOCK_PATTERN.search(text)
        if match:
            return self._clean_sql(match.group(1))

        # 2️⃣ Extract bare SQL
        match = _BARE_SQL_PATTERN.search(text)
        if match:
            return self._clean_sql(match.group(1))

        # 3️⃣ Fallback if looks like SQL
        if any(func in text.upper() for func in ["SUM(", "CASE", "COUNT(", "AVG(", "MIN(", "MAX("]):
            return self._clean_sql(text)

        return None

    def _clean_sql(self, sql: str) -> str:
        sql = sql.strip().rstrip(";")

        # Remove single-line comments
        lines = [
            line for line in sql.splitlines()
            if not line.strip().startswith("--")
        ]

        cleaned = " ".join(lines).strip()
        return re.sub(r"\s+", " ", cleaned)

    def _contains_dangerous_sql(self, sql: str) -> bool:
        upper_sql = sql.upper()
        return any(keyword in upper_sql for keyword in _DANGEROUS_KEYWORDS)