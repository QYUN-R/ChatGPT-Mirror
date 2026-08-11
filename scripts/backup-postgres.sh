#!/bin/sh
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_BACKUP_PASSPHRASE:?POSTGRES_BACKUP_PASSPHRASE is required}"

BACKUP_ROOT="${BACKUP_ROOT:-/app/backups/postgres}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DAY_OF_WEEK="$(date +%u)"
mkdir -p "$BACKUP_ROOT/daily" "$BACKUP_ROOT/weekly"

TARGET="$BACKUP_ROOT/daily/chatgpt-mirror-$STAMP.dump.enc"
export PGPASSWORD="$POSTGRES_PASSWORD"
export POSTGRES_BACKUP_PASSPHRASE

pg_dump \
  --host "$POSTGRES_HOST" \
  --port "${POSTGRES_PORT:-5432}" \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --format custom \
  --no-owner \
  --no-acl \
  | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:POSTGRES_BACKUP_PASSPHRASE -out "$TARGET"

if [ "$DAY_OF_WEEK" = "7" ]; then
  cp "$TARGET" "$BACKUP_ROOT/weekly/$(basename "$TARGET")"
fi

find "$BACKUP_ROOT/daily" -type f -name '*.dump.enc' -mtime +14 -delete
find "$BACKUP_ROOT/weekly" -type f -name '*.dump.enc' -mtime +56 -delete

printf 'Encrypted PostgreSQL backup created: %s\n' "$TARGET"
