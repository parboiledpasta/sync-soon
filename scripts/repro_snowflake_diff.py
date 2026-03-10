
import sys
from pathlib import Path
from typing import Set, List

# Mock SML Classes for testing
class MockSMLColumn:
    def __init__(self, unique_name, is_measure_candidate=False):
        self.unique_name = unique_name
        self.is_measure_candidate = is_measure_candidate
        self.data_type = "STRING"

class MockSMLDataset:
    def __init__(self, unique_name, columns):
        self.unique_name = unique_name
        self.columns = columns
        self.source_table = unique_name

# Mock Emitter Logic (stripped down for reproduction)
class MockSnowflakeEmitter:
    def _sanitize_col_name(self, name: str) -> str:
        """Original sanitization logic from SnowflakeEmitter"""
        if not name: return "UNKNOWN"
        import re
        bracket_match = re.search(r"\[(.+?)\]", name)
        if bracket_match: name = bracket_match.group(1)
        clean = re.sub(r'[^a-zA-Z0-9]', '_', name)
        clean = re.sub(r'_+', '_', clean)
        clean = clean.strip('_')
        return clean.upper() if clean else "COLUMN_UNKNOWN"

    def verify_table_columns(self, existing_cols: Set[str], dataset: MockSMLDataset):
        """Simulate _verify_table_columns logic"""
        print(f"DEBUG: Existing Snowflake Columns (Raw): {existing_cols}")
        
        expected_cols = set()
        for col in dataset.columns:
            if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                continue
            # Logic from source: sanitized = self._sanitize_col_name(col.unique_name)
            sanitized = self._sanitize_col_name(col.unique_name)
            expected_cols.add(sanitized)
        
        print(f"DEBUG: SML Expected Columns (Sanitized): {expected_cols}")
        
        # Logic from source: existing_cols = {row[0] for row in cursor.fetchall()}
        # BUT WAIT: The source code uses quoted identifiers in DESC TABLE, so existing_cols likely preserves case/format?
        # Let's assume existing_cols matches what 'DESC TABLE' returns.
        
        # Logic from source: missing = expected_cols - existing_cols
        missing = expected_cols - existing_cols
        print(f"DEBUG: Missing: {missing}")
        
        # Logic from source: extra_cols = existing_cols - expected_cols - internal_cols
        internal_cols = {'METADATA$ROW_ID'}
        extra_cols = existing_cols - expected_cols - internal_cols
        print(f"DEBUG: Extra (To Drop): {extra_cols}")
        
        return missing, extra_cols

def test_products_logic():
    emitter = MockSnowflakeEmitter()
    
    # CASE 1: Case Sensitivity Mismatch
    # Snowflake 'DESC TABLE' usually returns UPPERCASE unless quoted during creation
    # If the table was created by Semabridge previously, it used quoted identifiers, e.g., "Discount_Percent"
    
    # Scene: Table exists with mixed-case column
    existing = {"Discount_Percent", "ProductID", "Product"} 
    
    # SML has matched columns
    sml_cols = [
        MockSMLColumn("Discount_Percent"),
        MockSMLColumn("ProductID"),
        MockSMLColumn("Product")
    ]
    dataset = MockSMLDataset("PRODUCTS", sml_cols)
    
    print("--- TEST CASE 1: Mixed Case Existing vs Sanitized Expected ---")
    emitter.verify_table_columns(existing, dataset)
    
    # CASE 2: All Uppercase Existing
    existing_upper = {"DISCOUNT_PERCENT", "PRODUCTID", "PRODUCT"}
    print("\n--- TEST CASE 2: Uppercase Existing vs Sanitized Expected ---")
    emitter.verify_table_columns(existing_upper, dataset)

if __name__ == "__main__":
    test_products_logic()
