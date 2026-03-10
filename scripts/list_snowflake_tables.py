import snowflake.connector
import os

# Load connection details from environment variables or hardcode for test
USER = os.getenv('SNOWFLAKE_USER', 'your_user')
PASSWORD = os.getenv('SNOWFLAKE_PASSWORD', 'your_password')
ACCOUNT = os.getenv('SNOWFLAKE_ACCOUNT', 'your_account')
WAREHOUSE = os.getenv('SNOWFLAKE_WAREHOUSE', 'your_warehouse')
DATABASE = os.getenv('SNOWFLAKE_DATABASE', 'ANALYTICS_DB')
SCHEMA = os.getenv('SNOWFLAKE_SCHEMA', 'SEMANTIC_LAYER')

conn = snowflake.connector.connect(
    user=USER,
    password=PASSWORD,
    account=ACCOUNT,
    warehouse=WAREHOUSE,
    database=DATABASE,
    schema=SCHEMA,
)
cur = conn.cursor()
try:
    cur.execute(f"SHOW TABLES IN SCHEMA {SCHEMA}")
    print("\nTables in schema:")
    for row in cur.fetchall():
        print(row[1])
finally:
    cur.close()
    conn.close()
