
import os
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

def list_schemas():
    conn = snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database="ANALYTICS_DB"
    )
    try:
        cur = conn.cursor()
        print("Schemas in ANALYTICS_DB:")
        cur.execute("SHOW SCHEMAS")
        for row in cur:
            print(f"  {row[1]}")
    finally:
        conn.close()

if __name__ == "__main__":
    list_schemas()
