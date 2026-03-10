
import os
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

def sample_data():
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
        print("Sample values from FACT.CUSTOMER_KEY:")
        cur.execute("SELECT CUSTOMER_KEY FROM FACT LIMIT 5")
        for row in cur:
            print(f"  '{row[0]}'")
            
        print("\nSample values from CUSTOMER.CUSTOMER:")
        cur.execute("SELECT CUSTOMER FROM CUSTOMER LIMIT 5")
        for row in cur:
            print(f"  '{row[0]}'")
    finally:
        conn.close()

if __name__ == "__main__":
    sample_data()
