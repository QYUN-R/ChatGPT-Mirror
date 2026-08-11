#!/bin/sh
set -eu

: "${BACKUP_FILE:?BACKUP_FILE is required}"
: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${POSTGRES_BACKUP_PASSPHRASE:?POSTGRES_BACKUP_PASSPHRASE is required}"

TEST_DB="chatgpt_mirror_restore_test_$(date +%Y%m%d%H%M%S)"
export PGPASSWORD="$POSTGRES_PASSWORD"
export POSTGRES_BACKUP_PASSPHRASE

cleanup() {
  dropdb --if-exists --force --host "$POSTGRES_HOST" --port "${POSTGRES_PORT:-5432}" --username "$POSTGRES_USER" "$TEST_DB"
}
trap cleanup EXIT INT TERM

createdb --host "$POSTGRES_HOST" --port "${POSTGRES_PORT:-5432}" --username "$POSTGRES_USER" "$TEST_DB"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:POSTGRES_BACKUP_PASSPHRASE -in "$BACKUP_FILE" \
  | pg_restore \
      --host "$POSTGRES_HOST" \
      --port "${POSTGRES_PORT:-5432}" \
      --username "$POSTGRES_USER" \
      --dbname "$TEST_DB" \
      --no-owner \
      --no-acl

psql --host "$POSTGRES_HOST" --port "${POSTGRES_PORT:-5432}" --username "$POSTGRES_USER" --dbname "$TEST_DB" \
  --set ON_ERROR_STOP=1 \
  --command 'SELECT COUNT(*) AS users FROM accounts_user;' \
  --command 'SELECT COUNT(*) AS orders FROM billing_order;'

printf 'Independent restore test passed for %s\n' "$BACKUP_FILE"
