"""
Diagnostic script: dump the semantic view DDL for each model
and show which columns are in the DDL vs which exist in the physical table.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pathlib import Path
from semabridge.core.config_loader import get_default_config_path, load_yaml_file
from semabridge.core.settings import get_settings
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
import snowflake.connector

def main():
    settings = get_settings()
    extractor = FabricExtractor(settings.fabric)
    
    # Discover models
    all_models = extractor.list_semantic_models()
    print(f"Found {len(all_models)} models")
    
    converter = TMSLToOSIConverter()
    emitter = SnowflakeEmitter(settings.snowflake)
    
    # Connect to Snowflake to check actual tables
    conn = snowflake.connector.connect(
        user=settings.snowflake.user,
        password=settings.snowflake.password.get_secret_value(),
        account=settings.snowflake.account,
        warehouse=settings.snowflake.warehouse,
        database=settings.snowflake.database,
        schema=settings.snowflake.schema_name,
        role=settings.snowflake.role,
    )
    cur = conn.cursor()
    
    for model in all_models:
        m_id = model.get("id", "")
        m_name = model.get("displayName", m_id)
        print(f"\n{'='*70}")
        print(f"MODEL: {m_name} ({m_id[:8]}...)")
        print(f"{'='*70}")
        
        try:
            tmsl = extractor.get_model_definition(m_id)
            ws_id = settings.fabric.workspace_id
            source_data = {"tmsl": tmsl, "workspace_id": ws_id, "dataset_id": m_id}
            osi_model = converter.to_osi(source_data)
            
            # Show datasets and their columns with source_expression
            for ds in osi_model.datasets:
                source_table = ds.source_table or ds.unique_name
                safe_table = emitter._safe_table_name(source_table)
                
                # Check if table exists in Snowflake and get its columns
                try:
                    cur.execute(f'DESCRIBE TABLE "{safe_table}"')
                    sf_cols = {row[0].upper() for row in cur.fetchall()}
                except Exception:
                    sf_cols = set()
                
                physical_cols = []
                calculated_cols = []
                for col in ds.columns:
                    if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                        continue
                    src_expr = col.source_expression
                    sanitized = emitter._sanitize_col_name(col.unique_name)
                    is_physical = not src_expr or emitter._is_physical_source_column(src_expr)
                    in_snowflake = sanitized in sf_cols
                    
                    if is_physical:
                        physical_cols.append((col.unique_name, sanitized, in_snowflake, src_expr))
                    else:
                        calculated_cols.append((col.unique_name, sanitized, src_expr))
                
                print(f"\n  Dataset: {ds.unique_name} -> Table: {safe_table}")
                print(f"  SF table exists: {'YES' if sf_cols else 'NO'}")
                print(f"  Physical cols ({len(physical_cols)}):")
                for name, sanitized, in_sf, expr in physical_cols:
                    marker = "[OK]" if in_sf else "[MISSING IN SF!]"
                    expr_info = f" [expr: {expr[:50]}]" if expr else ""
                    print(f"    {marker} {sanitized}{expr_info}")
                
                if calculated_cols:
                    print(f"  Calculated cols ({len(calculated_cols)}) [EXCLUDED]:")
                    for name, sanitized, expr in calculated_cols:
                        print(f"    ~ {sanitized} [expr: {expr[:60] if expr else 'None'}...]")
                    
            # Check dimensions
            print(f"\n  Dimensions ({len(osi_model.dimensions)}):")
            for dim in osi_model.dimensions:
                print(f"    {dim.unique_name}: {len(dim.attributes)} attributes")
                for attr in dim.attributes:
                    print(f"      - {attr.unique_name} -> {attr.source_column} (dataset: {attr.dataset})")
            
            # Generate DDL and show it
            try:
                ddls = emitter.generate_ddls_from_osi(osi_model)
                print(f"\n  Generated DDL ({len(ddls)} statements):")
                for ddl in ddls:
                    # Show lines that contain REPORTINGPERIODID or any suspicious column
                    for i, line in enumerate(ddl.split('\n'), 1):
                        if 'REPORTINGPERIODID' in line.upper():
                            print(f"    *** LINE {i}: {line.strip()}")
                    
                    # Show the full DDL (truncated)
                    if len(ddl) < 3000:
                        print(f"\n--- DDL ---\n{ddl}\n--- END ---")
                    else:
                        print(f"\n--- DDL (first 2000 chars) ---\n{ddl[:2000]}\n...\n--- END ---")
            except Exception as e:
                print(f"  DDL generation failed: {e}")

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
