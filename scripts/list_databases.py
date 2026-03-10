
import os
import snowflake.connector
from dotenv import load_dotenv

load_dotenv()

def list_databases():
    conn = snowflake.connector.connect(
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE")
    )
    try:
        cur = conn.cursor()
        print("Databases:")
        cur.execute("SHOW DATABASES")
        for row in cur:
            print(f"  {row[1]}")
    finally:
        conn.close()

if __name__ == "__main__":
    list_databases()
