#!/bin/sh
set -eu

: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_BACKUP_PASSPHRASE:?POSTGRES_BACKUP_PASSPHRASE is required}"
: "${CONFIRM_RESTORE:?Set CONFIRM_RESTORE=RESTORE to continue}"

if [ "$CONFIRM_RESTORE" != "RESTORE" ]; then
  printf 'Restore cancelled: CONFIRM_RESTORE must equal RESTORE\n' >&2
  exit 2
fi

export PGPASSWORD="$POSTGRES_PASSWORD"
export POSTGRES_BACKUP_PASSPHRASE

openssl enc -d -aes-256-cbc -pbkdf2 -pass env:POSTGRES_BACKUP_PASSPHRASE -in "$BACKUP_FILE" \
  | pg_restore \
      --host "$POSTGRES_HOST" \
      --port "${POSTGRES_PORT:-5432}" \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB" \
      --clean \
      --if-exists \
      --no-owner \
      --no-acl

printf 'PostgreSQL restore completed for database %s\n' "$POSTGRES_DB"
