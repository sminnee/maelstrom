#!/usr/bin/env bash
# Refuse a DAST target that is not this worktree's own dev environment.
#
# Usage: target-guard.sh <scheme://host:port> [env-file]
# Prints the URL and exits 0 when its host is DEV_HOST, localhost, 127.0.0.1 or
# host.docker.internal, and its port is a *_PORT value in the env file (default:
# ./.env) in this worktree's own range, PORT_BASE * 10 to PORT_BASE * 10 + 9.
# The range refuses shared-service ports, which every worktree's .env lists too.
# Exits 1 with the reason otherwise.
set -euo pipefail

url="${1:?usage: target-guard.sh <scheme://host:port> [env-file]}"
env_file="${2:-.env}"

[[ -f "$env_file" ]] || { echo "refused: no $env_file in $(pwd)" >&2; exit 1; }

if [[ ! "$url" =~ ^https?://([^/:]+):([0-9]+)$ ]]; then
  echo "refused: $url is not scheme://host:port with no path" >&2
  exit 1
fi
host="${BASH_REMATCH[1]}"
port="${BASH_REMATCH[2]}"

dev_host="$(sed -n 's/^DEV_HOST=//p' "$env_file" | tail -1)"
case "$host" in
  localhost | 127.0.0.1 | host.docker.internal) ;;
  *)
    if [[ -z "$dev_host" || "$host" != "$dev_host" ]]; then
      echo "refused: host $host is not this worktree's DEV_HOST (${dev_host:-unset})" >&2
      exit 1
    fi
    ;;
esac

if ! grep -Eq "^[A-Z0-9_]+_PORT=${port}\$" "$env_file"; then
  echo "refused: port $port is not a *_PORT in $env_file" >&2
  exit 1
fi

port_base="$(sed -n 's/^PORT_BASE=//p' "$env_file" | tail -1)"
if [[ -z "$port_base" || "$((10#$port / 10))" != "$port_base" ]]; then
  echo "refused: port $port is outside this worktree's PORT_BASE ${port_base:-unset}" >&2
  exit 1
fi

echo "$url"
