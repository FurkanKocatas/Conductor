#!/usr/bin/env bash
# Conductor yedekleme — conductor-db'yi sıkıştırılmış SQL dump olarak alır.
# Kullanım: ./backup.sh   (cron ile günlük çalıştırılabilir)
# Geri yükleme: gunzip -c <dosya> | docker exec -i conductor-db psql -U conductor -d conductor
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/backups"
mkdir -p "$DIR"
TS="$(date +%Y%m%d_%H%M%S)"
OUT="$DIR/conductor_${TS}.sql.gz"
docker exec conductor-db pg_dump -U conductor -d conductor | gzip > "$OUT"
# Son 30 yedeği tut
ls -1t "$DIR"/conductor_*.sql.gz | tail -n +31 | xargs -r rm -f
echo "Yedek: $OUT ($(du -h "$OUT" | cut -f1))"
