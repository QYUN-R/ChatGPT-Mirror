#!/bin/sh
set -eu

PROJECT_ROOT="${PROJECT_ROOT:-/opt/chatgpt-mirror}"
MARKER_BEGIN="# BEGIN chatgpt-mirror postgres maintenance"
MARKER_END="# END chatgpt-mirror postgres maintenance"
CURRENT="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "$CURRENT" | sed "/$MARKER_BEGIN/,/$MARKER_END/d")"

{
  printf '%s\n' "$FILTERED"
  printf '%s\n' "$MARKER_BEGIN"
  printf '30 3 * * * cd %s && flock -n /var/lock/chatgpt-mirror-backup.lock ./scripts/run-postgres-backup.sh >> backend/logs/postgres-backup.log 2>&1\n' "$PROJECT_ROOT"
  printf '30 4 * * 0 cd %s && flock -n /var/lock/chatgpt-mirror-restore-test.lock ./scripts/run-postgres-restore-test.sh >> backend/logs/postgres-restore-test.log 2>&1\n' "$PROJECT_ROOT"
  printf '%s\n' "$MARKER_END"
} | crontab -

printf 'PostgreSQL backup and restore-test cron jobs installed\n'
