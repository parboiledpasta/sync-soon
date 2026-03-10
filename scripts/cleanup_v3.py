import duckdb
conn = duckdb.connect('semabridge.db')
conn.execute("DELETE FROM snapshots WHERE version_tag = 'v3'")
conn.close()
print("Deleted existing v3 snapshots.")
