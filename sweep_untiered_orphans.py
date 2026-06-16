# sweep_untiered_orphans.py — one-shot
import sqlite3

DB = "data/twap.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

# VIPs are exempt regardless of tier
cur.execute("""
    SELECT COUNT(*) FROM whale_addresses
    WHERE is_active=1 AND tier IS NULL
      AND address NOT IN (SELECT address FROM vip_addresses)
""")
to_sweep = cur.fetchone()[0]

cur.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active=1")
before = cur.fetchone()[0]

print(f"Active before: {before}")
print(f"Untiered orphans to deactivate (VIPs excluded): {to_sweep}")

cur.execute("""
    UPDATE whale_addresses
    SET is_active=0,
        tier=NULL, tier_position=NULL, tier_perp_amount=NULL, tier_spot=NULL,
        position_value=0, raw_usd_value=0, spot_value=0,
        pending_deactivation=NULL,
        last_updated=?
    WHERE is_active=1 AND tier IS NULL
      AND address NOT IN (SELECT address FROM vip_addresses)
""", (__import__("datetime").datetime.utcnow().isoformat(),))

conn.commit()

cur.execute("SELECT COUNT(*) FROM whale_addresses WHERE is_active=1")
after = cur.fetchone()[0]
print(f"Active after: {after}  (swept {before - after})")
conn.close()