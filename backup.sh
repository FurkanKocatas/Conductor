#!/usr/bin/env bash
# Conductor backup — dumps conductor-db as a compressed SQL file.
# Usage: ./backup.sh   (can be run daily via cron)
# Restore: gunzip -c <file> | docker exec -i conductor-db psql -U conductor -d conductor
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backups"
mkdir -p "$DIR"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$DIR/conductor_${TS}.sql.gz"
docker exec conductor-db pg_dump -U conductor -d conductor | gzip > "$OUT"
# Keep the last 30 backups
ls -1t "$DIR"/conductor_*.sql.gz | tail -n +31 | xargs -r rm -f
echo "Backup: $OUT ($(du -h "$OUT" | cut -f1))"
