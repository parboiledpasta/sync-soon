import sys
from pathlib import Path
from datetime import datetime
import json
import uuid

# Add src to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from semabridge.repository.duckdb_manager import DuckDBManager

def seed():
    db = DuckDBManager()
    
    project_id = "demo-table-model"
    project_name = "Demo Table Model"
    
    print(f"Seeding project: {project_name} ({project_id})")
    db.ensure_project(project_id, project_name, "demo-workspace", adapter="fabric")
    
    # Version 1: Basic metrics
    sml_v1 = {
        "label": project_name,
        "datasets": [
            {
                "unique_name": "Sales",
                "columns": [
                    {"unique_name": "Amount", "data_type": "double"},
                    {"unique_name": "OrderDate", "data_type": "datetime"}
                ]
            }
        ],
        "metrics": [
            {
                "unique_name": "Total Revenue",
                "table_name": "Sales",
                "expression": "SUM(Sales[Amount])",
                "description": "Total revenue from all sales"
            }
        ],
        "dimensions": [
            {
                "unique_name": "Date",
                "table_name": "Sales",
                "column_name": "OrderDate"
            }
        ],
        "relationships": []
    }
    
    print("Committing Version 1 (Initial Setup)...")
    db.commit_model(
        project_id=project_id, 
        sml_json=sml_v1, 
        tag="v1.0.0", 
        initiated_by="demo-script",
        status="success"
    )
    
    # Version 2: Added Cost and Profit metrics
    sml_v2 = {
        "label": project_name,
        "datasets": [
            {
                "unique_name": "Sales",
                "columns": [
                    {"unique_name": "Amount", "data_type": "double"},
                    {"unique_name": "Cost", "data_type": "double"},
                    {"unique_name": "OrderDate", "data_type": "datetime"}
                ]
            }
        ],
        "metrics": [
            {
                "unique_name": "Total Revenue",
                "table_name": "Sales",
                "expression": "SUM(Sales[Amount])",
                "description": "Total revenue from all sales"
            },
            {
                "unique_name": "Total Cost",
                "table_name": "Sales",
                "expression": "SUM(Sales[Cost])",
                "description": "Total cost of goods sold"
            },
            {
                "unique_name": "Gross Profit",
                "table_name": "Sales",
                "expression": "[Total Revenue] - [Total Cost]",
                "description": "Revenue minus cost"
            }
        ],
        "dimensions": [
            {
                "unique_name": "Date",
                "table_name": "Sales",
                "column_name": "OrderDate"
            }
        ],
        "relationships": []
    }
    
    # Force a slight timestamp difference
    import time
    time.sleep(0.5)
    
    print("Committing Version 2 (Added Profit Analytics)...")
    db.commit_model(
        project_id=project_id, 
        sml_json=sml_v2, 
        tag="v2.0.0", 
        initiated_by="demo-script",
        status="success"
    )
    
    print("\n[SUCCESS] Demo data seeded.")
    print(f"You can now select '{project_name}' in the UI to see its history.")

if __name__ == "__main__":
    seed()
