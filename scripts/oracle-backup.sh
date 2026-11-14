#!/usr/bin/env bash
# Nightly Postgres dump, keeps 7 days. Cron: 0 2 * * * /home/ubuntu/-ariel-dev/scripts/oracle-backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
docker compose -f docker-compose.oracle.yml --env-file .env.oracle exec -T db \
  pg_dump -U horilla horilla | gzip > "backups/horilla_$(date +%Y%m%d).sql.gz"
find backups -name 'horilla_*.sql.gz' -mtime +7 -delete
