#!/bin/sh
set -eu

if [ -z "${REDIS_PASSWORD:-}" ]; then
  echo "REDIS_PASSWORD is required" >&2
  exit 1
fi

umask 077
config_file="$(mktemp)"
trap 'rm -f "$config_file"' EXIT HUP INT TERM

cat >"$config_file" <<EOF
bind 0.0.0.0 ::
protected-mode yes
port 6379
dir /data
appendonly yes
appendfsync everysec
save 900 1
save 300 10
maxmemory 160mb
maxmemory-policy allkeys-lru
requirepass ${REDIS_PASSWORD}
loglevel notice
logfile ""
EOF

exec redis-server "$config_file"
