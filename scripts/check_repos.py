import duckdb
import os

db_path = "semabridge.db"
if not os.path.exists(db_path):
    print(f"DB not found at {db_path}")
    exit(1)

con = duckdb.connect(db_path, read_only=True)
try:
    print("Projects:")
    projects = con.execute("SELECT project_id, name, version_tag FROM projects").fetchall()
    for p in projects:
        print(p)
    
    print("\nSnapshots for continent:")
    # Assuming the project name is 'continent'
    snapshots = con.execute("""
        SELECT s.snapshot_id, s.version_tag, s.created_at, s.comment
        FROM snapshots s
        JOIN projects p ON s.project_id = p.project_id
        WHERE p.name = 'continent'
        ORDER BY s.created_at DESC
    """).fetchall()
    for s in snapshots:
        print(s)
finally:
    con.close()
