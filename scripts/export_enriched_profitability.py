
import os
import snowflake.connector
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

def export_enriched_profitability_data():
    conn = snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA")
    )
    try:
        # Join FACT and CUSTOMER
        # FACT.CUSTOMER_KEY maps to CUSTOMER.CUSTOMER
        query = """
        SELECT 
            c.CUSTOMER AS CUSTOMER_ID,
            c.NAME AS CUSTOMER_NAME,
            c.CITY,
            c.STATE,
            c.COUNTRY_REGION,
            f.REVENUE, 
            f.MATERIAL_COSTS, 
            f.SUBSCRIPTION_REVENUE,
            f.REV_FOR_EXP_TRAVEL,
            f.YEARPERIOD
        FROM FACT f
        JOIN CUSTOMER c ON f.CUSTOMER_KEY = c.CUSTOMER
        """
        df = pd.read_sql(query, conn)
        
        output_path = "customer_profitability_data.csv"
        df.to_csv(output_path, index=False)
        print(f"Data exported successfully to {output_path}")
        print(f"Total rows: {len(df)}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    export_enriched_profitability_data()
