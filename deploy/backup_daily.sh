#!/usr/bin/env bash
# Daily NEXORA backup: Postgres dump + consistent SQLite copy. Keeps 14 days.
set -euo pipefail
set -a; . /etc/nexora.env; set +a

DEST=/var/backups/nexora
STAMP="$(date +%Y-%m-%d_%H%M)"
mkdir -p "$DEST"

if [ -n "${DATABASE_URL:-}" ]; then
  pg_dump "$DATABASE_URL" | gzip > "$DEST/postgres_$STAMP.sql.gz"
fi

SQLITE_DB="${PERSISTENT_DISK_PATH:-/var/data}/centralized_db.sqlite3"
if [ -f "$SQLITE_DB" ]; then
  sqlite3 "$SQLITE_DB" ".backup '$DEST/sqlite_$STAMP.sqlite3'"
  gzip -f "$DEST/sqlite_$STAMP.sqlite3"
fi

find "$DEST" -type f -mtime +14 -delete
echo "$(date -Is) backup ok"
