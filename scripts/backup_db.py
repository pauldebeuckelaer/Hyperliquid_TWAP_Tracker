import sqlite3
from datetime import datetime, timezone

# Connect to source database
source = sqlite3.connect('twap.db')

# Create backup filename with timestamp
backup_name = f"twap_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.db"

# Perform backup
backup = sqlite3.connect(backup_name)
source.backup(backup)
backup.close()
source.close()

print(f"Backup created: {backup_name}")
