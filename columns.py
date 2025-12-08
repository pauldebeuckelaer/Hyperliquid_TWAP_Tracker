import sqlite3
conn = sqlite3.connect(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\data\twap.db")

# Get schema for each table
for table in ['snapshots', 'orders', 'events', 'addresses']:
    print(f"\n{table}:")
    cursor = conn.execute(f"PRAGMA table_info({table})")
    for col in cursor:
        print(f"  {col[1]}: {col[2]}")

conn.close()