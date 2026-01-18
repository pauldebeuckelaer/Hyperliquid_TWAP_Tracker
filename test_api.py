from pathlib import Path
from storage import SQLiteBackend

storage = SQLiteBackend(Path('data/twap.db'))

# Schema
storage.cursor.execute("PRAGMA table_info(vault_snapshots)")
print("=== vault_snapshots schema ===")
for row in storage.cursor.fetchall():
    print(f"  {row[1]}: {row[2]}")

# Sample data
storage.cursor.execute("""
    SELECT * FROM vault_snapshots LIMIT 5
""")
print("\n=== Sample rows ===")
cols = [desc[0] for desc in storage.cursor.description]
print(f"  Columns: {cols}")
for row in storage.cursor.fetchall():
    print(f"  {list(row)}")

# Row counts by date
storage.cursor.execute("""
    SELECT DATE(snapshot_time) as date, COUNT(*) as rows
    FROM vault_snapshots
    GROUP BY DATE(snapshot_time)
    ORDER BY date DESC
    LIMIT 5
""")
print("\n=== Data by date ===")
for row in storage.cursor.fetchall():
    print(f"  {row[0]}: {row[1]} rows")

storage.close()