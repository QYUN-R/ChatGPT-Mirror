#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

docker compose \
  -f docker-compose.yml \
  -f docker-compose.commercial.yml \
  --profile frontend-build \
  run --rm frontend-builder

test -s gateway/static/index.html
printf 'Commercial admin frontend built at %s/gateway/static\n' "$PROJECT_ROOT"
