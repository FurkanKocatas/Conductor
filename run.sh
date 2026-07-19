#!/usr/bin/env bash
# Conductor — tek-komut yönetim script'i (local geliştirme).
#
# Docker Desktop'ın credsStore helper'ı bazı Mac'lerde image pull sırasında
# askıda kalır. Bu script, GLOBAL ~/.docker/config.json'a DOKUNMADAN, izole bir
# DOCKER_CONFIG kurar (credsStore devre dışı; cli-plugins + contexts symlink'li)
# ve tüm docker compose komutlarını onunla çalıştırır. Temiz makinede de zararsız.
#
# Kullanım:
#   ./run.sh up        # derle + başlat (arka planda)
#   ./run.sh down      # durdur (veri KALIR)
#   ./run.sh reset     # durdur + DB volume'ünü SİL (tamamen sıfırla)
#   ./run.sh logs      # server loglarını izle
#   ./run.sh ps        # durum
#   ./run.sh token     # admin token'ı yazdır
#   ./run.sh restart   # server'ı yeniden başlat
set -euo pipefail
cd "$(dirname "$0")"

# ── izole docker config (credsStore hang'ini atlar) ──────────────────────────
CFG="${TMPDIR:-/tmp}/conductor-dockercfg"
mkdir -p "$CFG"
printf '{"auths":{}}\n' > "$CFG/config.json"          # credsStore YOK → pull askıda kalmaz
[ -d "$HOME/.docker/cli-plugins" ] && ln -snf "$HOME/.docker/cli-plugins" "$CFG/cli-plugins"
[ -d "$HOME/.docker/contexts" ]    && ln -snf "$HOME/.docker/contexts"    "$CFG/contexts"
export DOCKER_CONFIG="$CFG"

dc() { docker compose "$@"; }

case "${1:-up}" in
  up)       dc build && dc up -d && echo "✓ http://localhost:8790  (admin token: ./run.sh token)";;
  down)     dc down;;
  reset)    dc down -v && echo "✓ DB sıfırlandı — bir sonraki 'up' Alembic ile temiz şema kurar";;
  migrate)  dc run --rm migrate;;   # şemayı head'e getir (up zaten otomatik çalıştırır)
  logs)     dc logs -f server;;
  ps)       dc ps;;
  restart)  dc restart server;;
  token)    grep BOOTSTRAP_ADMIN_TOKEN .env | cut -d= -f2;;
  *)        echo "kullanım: ./run.sh {up|down|reset|migrate|logs|ps|restart|token}"; exit 1;;
esac
