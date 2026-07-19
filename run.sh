#!/usr/bin/env bash
# Conductor — single-command management script (local development).
#
# Docker Desktop's credsStore helper stalls during image pulls on some machines.
# Without touching the GLOBAL ~/.docker/config.json, this script sets up an
# isolated DOCKER_CONFIG (credsStore disabled; cli-plugins + contexts symlinked)
# and runs all docker compose commands with it. Harmless on a clean machine too.
#
# Usage:
#   ./run.sh up        # build + start (in background)
#   ./run.sh down      # stop (data KEPT)
#   ./run.sh reset     # stop + DELETE the DB volume (full reset)
#   ./run.sh migrate   # bring schema to head (up runs this automatically)
#   ./run.sh logs      # tail server logs
#   ./run.sh ps        # status
#   ./run.sh token     # print the admin token
#   ./run.sh restart   # restart the server
set -euo pipefail
cd "$(dirname "$0")"

# ── isolated docker config (skips the credsStore hang) ───────────────────────
CFG="${TMPDIR:-/tmp}/conductor-dockercfg"
mkdir -p "$CFG"
printf '{"auths":{}}\n' > "$CFG/config.json"          # no credsStore → pulls don't hang
[ -d "$HOME/.docker/cli-plugins" ] && ln -snf "$HOME/.docker/cli-plugins" "$CFG/cli-plugins"
[ -d "$HOME/.docker/contexts" ]    && ln -snf "$HOME/.docker/contexts"    "$CFG/contexts"
export DOCKER_CONFIG="$CFG"

dc() { docker compose "$@"; }

case "${1:-up}" in
  up)       dc build && dc up -d && echo "✓ http://localhost:8790  (admin token: ./run.sh token)";;
  down)     dc down;;
  reset)    dc down -v && echo "✓ DB reset — the next 'up' builds a clean schema via Alembic";;
  migrate)  dc run --rm migrate;;   # bring schema to head (up already runs this automatically)
  logs)     dc logs -f server;;
  ps)       dc ps;;
  restart)  dc restart server;;
  token)    grep BOOTSTRAP_ADMIN_TOKEN .env | cut -d= -f2;;
  *)        echo "usage: ./run.sh {up|down|reset|migrate|logs|ps|restart|token}"; exit 1;;
esac
