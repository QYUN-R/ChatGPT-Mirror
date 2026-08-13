#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.commercial.yml \
  -f docker-compose.override.yml \
  --profile postgres \
  --profile maintenance \
  run --rm db-tools backup-postgres.sh
