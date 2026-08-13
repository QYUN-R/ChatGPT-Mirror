#!/bin/sh
set -eu

# Warm only public, versioned browser bundles already requested from this
# service. API, user, conversation, billing and WebSocket paths are excluded.
SITE_URL="${PUBLIC_SITE_URL:-https://www.tuwugpt.com}"
ACCESS_LOG="${NGINX_ACCESS_LOG:-/var/log/nginx/access.log}"
LIMIT="${STATIC_WARM_LIMIT:-300}"
CONCURRENCY="${STATIC_WARM_CONCURRENCY:-12}"

case "$SITE_URL" in
  https://*) ;;
  *)
    printf 'PUBLIC_SITE_URL must use https://\n' >&2
    exit 2
    ;;
esac

case "$LIMIT:$CONCURRENCY" in
  *[!0-9:]*|:*)
    printf 'STATIC_WARM_LIMIT and STATIC_WARM_CONCURRENCY must be integers\n' >&2
    exit 2
    ;;
esac

if [ ! -r "$ACCESS_LOG" ]; then
  printf 'Cannot read Nginx access log: %s\n' "$ACCESS_LOG" >&2
  exit 1
fi

# Nginx access logs contain client-controlled request paths. Keep a strict
# allowlist before feeding any path to curl or xargs.
awk '
  $7 ~ /^\/(edge|cdn)\/assets\/[A-Za-z0-9._-]+\.(css|js|svg|webp|png|jpe?g|gif|woff2?|json)$/ {
    print $7
  }
' "$ACCESS_LOG" \
  | sort -u \
  | tail -n "$LIMIT" \
  | xargs -r -n 1 -P "$CONCURRENCY" sh -c '
      curl --fail --silent --show-error --location \
        --connect-timeout 5 --max-time 30 \
        --output /dev/null \
        "${0}${1}"
    ' "$SITE_URL"
