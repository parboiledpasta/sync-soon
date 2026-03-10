
import os
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

def inspect_probability():
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
        print("\nColumns for PROBABILITY_1:")
        cur.execute("DESC TABLE PROBABILITY_1")
        for row in cur:
            print(f"  {row[0]} ({row[1]})")
            
        print("\nSample values:")
        cur.execute("SELECT * FROM PROBABILITY_1 LIMIT 5")
        for row in cur:
            print(f"  {row}")
    finally:
        conn.close()

if __name__ == "__main__":
    inspect_probability()
