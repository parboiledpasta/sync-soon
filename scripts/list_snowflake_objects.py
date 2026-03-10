
import os
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

def list_snowflake_objects():
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
        print("Tables:")
        cur.execute("SHOW TABLES")
        for row in cur:
            print(f"  {row[1]}")
            
        print("\nViews:")
        cur.execute("SHOW VIEWS")
        for row in cur:
            print(f"  {row[1]}")
    finally:
        conn.close()

if __name__ == "__main__":
    list_snowflake_objects()
