
import os
import snowflake.connector
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def export_profitability_data():
    conn = snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA")
    )
    try:
        cur = conn.cursor()
        
        # Check for any view containing PROFITABILITY
        cur.execute("SHOW VIEWS IN DATABASE")
        views = cur.fetchall()
        print("Views found in database:")
        found_view = None
        for v in views:
            v_name = v[1]
            print(f"  {v[3]}.{v[4]}.{v_name}")
            if "PROFITABILITY" in v_name.upper():
                found_view = f"{v[3]}.{v[4]}.{v_name}"
        
        if not found_view:
            # Fallback to FACT table if no view found
            print("\nNo Profitability view found. Checking FACT table in SEMANTIC_LAYER...")
            found_view = f"{os.getenv('SNOWFLAKE_DATABASE')}.{os.getenv('SNOWFLAKE_SCHEMA')}.FACT"
            
        print(f"\nQuerying data from: {found_view}")
        query = f"SELECT * FROM {found_view} LIMIT 100"
        df = pd.read_sql(query, conn)
        
        output_path = "customer_profitability_data.csv"
        df.to_csv(output_path, index=False)
        print(f"Data exported to {output_path}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    export_profitability_data()
