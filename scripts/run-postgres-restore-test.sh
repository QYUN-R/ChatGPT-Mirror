#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

compose() {
  docker compose -f docker-compose.yml -f docker-compose.commercial.yml "$@"
}

cleanup() {
  compose --profile restore-test rm -sf restore-test-postgres >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

BACKUP_FILE="${1:-}"
if [ -z "$BACKUP_FILE" ]; then
  if [ ! -d "$PROJECT_ROOT/backups/postgres/daily" ]; then
    printf 'No encrypted PostgreSQL backup directory found\n' >&2
    exit 2
  fi
  BACKUP_FILE="$(find "$PROJECT_ROOT/backups/postgres/daily" -type f -name '*.dump.enc' -print | sort | tail -1)"
fi
if [ -z "$BACKUP_FILE" ] || [ ! -f "$BACKUP_FILE" ]; then
  printf 'No encrypted PostgreSQL backup found\n' >&2
  exit 2
fi

case "$BACKUP_FILE" in
  "$PROJECT_ROOT/backups/postgres/"*) ;;
  *)
    printf 'Backup file must be inside %s/backups/postgres\n' "$PROJECT_ROOT" >&2
    exit 2
    ;;
esac

RELATIVE_PATH="${BACKUP_FILE#"$PROJECT_ROOT/backups/postgres/"}"
CONTAINER_BACKUP="/app/backups/postgres/$RELATIVE_PATH"

compose --profile restore-test up -d --wait restore-test-postgres
compose \
  --profile maintenance \
  --profile restore-test \
  run --rm \
  -e POSTGRES_HOST=restore-test-postgres \
  -e POSTGRES_DB=postgres \
  -e BACKUP_FILE="$CONTAINER_BACKUP" \
  db-tools test-postgres-restore.sh
