import sqlite3
conn = sqlite3.connect(r"C:\Users\paul_\PycharmProjects\Hyperliquid_TWAP_Analyzer\data\twap.db")

# What types of orphan events?
print("Orphan events by type:")
for row in conn.execute("""
    SELECT event_type, COUNT(*) 
    FROM events 
    WHERE order_hash NOT IN (SELECT order_hash FROM orders)
    GROUP BY event_type
"""):
    print(f"  {row[0]}: {row[1]}")

# How many have empty order_hash?
empty = conn.execute("SELECT COUNT(*) FROM events WHERE order_hash = '' OR order_hash IS NULL").fetchone()[0]
print(f"\nEvents with empty order_hash: {empty}")

# Sample orphan events with full details
print("\nSample orphan events:")
for row in conn.execute("""
    SELECT order_hash, event_type, symbol, side, size, timestamp
    FROM events 
    WHERE order_hash NOT IN (SELECT order_hash FROM orders)
    AND order_hash != ''
    LIMIT 10
"""):
    print(f"  {row[0][:16]}... | {row[1]:10} | {row[2]:8} | {row[3]} | {row[4]} | {row[5][:19]}")

conn.close()