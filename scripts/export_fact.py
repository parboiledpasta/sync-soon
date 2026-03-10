
import os
import snowflake.connector
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def export_fact_data():
    conn = snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA")
    )
    try:
        query = "SELECT * FROM FACT"
        df = pd.read_sql(query, conn)
        
        output_path = "customer_profitability_fact_data.csv"
        df.to_csv(output_path, index=False)
        print(f"Data exported to {output_path}")
        print(f"Total rows exported: {len(df)}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    export_fact_data()
