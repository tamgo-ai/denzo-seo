#!/usr/bin/env bash
# Nightly SQLite backup for Denzo SEO.
#
# Uses VACUUM INTO so the backup is consistent with concurrent writers — safer
# than `cp` while the app is running.
#
# Schedule it from cron or a systemd timer:
#
#   /etc/cron.d/denzo-backup:
#   30 3 * * * root /root/denzo-seo/scripts/backup_db.sh >> /var/log/denzo-backup.log 2>&1
#
# Keeps the last 14 daily backups + the last 8 weekly Sundays.

set -euo pipefail

DB_PATH="${DENZO_DB_PATH:-/root/denzo-seo/data/denzo.db}"
BACKUP_DIR="${DENZO_BACKUP_DIR:-/root/denzo-backups}"
RETAIN_DAYS="${DENZO_BACKUP_RETAIN_DAYS:-14}"
RETAIN_WEEKLY="${DENZO_BACKUP_RETAIN_WEEKLY:-8}"

mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

TS=$(date +%Y%m%d_%H%M%S)
DAY_OF_WEEK=$(date +%u)   # 1=Mon ... 7=Sun

DAILY_FILE="$BACKUP_DIR/daily/denzo_${TS}.db"
echo "[$(date -Is)] Starting backup → $DAILY_FILE"

# VACUUM INTO captures a consistent snapshot even with concurrent writes.
sqlite3 "$DB_PATH" "VACUUM INTO '$DAILY_FILE'"

# gzip to save space (still readable with gunzip + sqlite3)
gzip -9 "$DAILY_FILE"
echo "[$(date -Is)] Daily backup written: ${DAILY_FILE}.gz ($(du -h "${DAILY_FILE}.gz" | cut -f1))"

# Weekly snapshot every Sunday
if [ "$DAY_OF_WEEK" = "7" ]; then
  cp "${DAILY_FILE}.gz" "$BACKUP_DIR/weekly/denzo_${TS}_sunday.db.gz"
  echo "[$(date -Is)] Weekly snapshot stored."
fi

# Prune old daily backups
find "$BACKUP_DIR/daily" -name "denzo_*.db.gz" -mtime "+${RETAIN_DAYS}" -print -delete

# Prune old weekly snapshots
WEEKLY_COUNT=$(ls -1 "$BACKUP_DIR/weekly/" 2>/dev/null | wc -l)
if [ "$WEEKLY_COUNT" -gt "$RETAIN_WEEKLY" ]; then
  ls -1t "$BACKUP_DIR/weekly/" | tail -n "+$((RETAIN_WEEKLY + 1))" | while read -r f; do
    rm -v "$BACKUP_DIR/weekly/$f"
  done
fi

echo "[$(date -Is)] Backup complete."
