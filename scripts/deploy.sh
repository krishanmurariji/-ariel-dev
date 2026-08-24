#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/arielhr"
SERVICE_NAME="arielhr"
BACKUP_ROOT="$HOME/rollback_$(date +%Y%m%d_%H%M%S)"
DB_BACKUP_DIR="/opt/arielhr_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "==> Deploy started: $TIMESTAMP"

echo "==> Stopping $SERVICE_NAME"
sudo systemctl stop "$SERVICE_NAME"

echo "==> Backing up current code to $BACKUP_ROOT"
mkdir -p "$BACKUP_ROOT"
cp -r "$APP_DIR"/* "$APP_DIR/.env" "$BACKUP_ROOT/" 2>/dev/null || true

mkdir -p "$DB_BACKUP_DIR"
if grep -qE '^(DB_ENGINE=django.db.backends.postgresql|DATABASE_URL=postgres)' "$APP_DIR/.env" 2>/dev/null; then
  echo "==> Backing up Postgres database"
  command -v pg_dump >/dev/null || {
    echo "!! pg_dump not found. One-time setup on this server:"
    echo "   sudo apt-get install -y postgresql-client"
    exit 1
  }
  set -a
  source "$APP_DIR/.env"
  set +a
  PG_DUMP_FILE="$DB_BACKUP_DIR/arielhr_predeploy_${TIMESTAMP}.dump"
  if [ -n "${DATABASE_URL:-}" ]; then
    PGPASSWORD="" pg_dump --dbname="$DATABASE_URL" -F c -f "$PG_DUMP_FILE"
  else
    PGPASSWORD="${DB_PASSWORD:-}" pg_dump -h "${DB_HOST}" -p "${DB_PORT:-5432}" -U "${DB_USER}" -d "${DB_NAME}" -F c -f "$PG_DUMP_FILE"
  fi
else
  echo "==> Backing up SQLite database"
  for f in "$APP_DIR"/*.sqlite3; do
    [ -e "$f" ] || continue
    cp "$f" "$DB_BACKUP_DIR/$(basename "$f" .sqlite3)_predeploy_${TIMESTAMP}.sqlite3"
  done
fi

if [ -n "${DB_HOST:-}" ]; then
  echo "==> Updating database config in .env from pipeline secrets"
  for VAR in DB_ENGINE DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT; do
    VALUE="${!VAR:-}"
    [ -z "$VALUE" ] && continue
    if grep -q "^${VAR}=" "$APP_DIR/.env" 2>/dev/null; then
      sed -i "s|^${VAR}=.*|${VAR}=${VALUE}|" "$APP_DIR/.env"
    else
      echo "${VAR}=${VALUE}" >> "$APP_DIR/.env"
    fi
  done
  echo "==> .env DB-related lines after update (values masked):"
  grep -E '^(DATABASE_URL|DB_ENGINE|DB_NAME|DB_USER|DB_HOST|DB_PORT)=' "$APP_DIR/.env" | sed -E 's/=.*/=***/'
fi

echo "==> Pulling latest code"
cd "$APP_DIR"
git fetch origin
git reset --hard origin/main

echo "==> Verifying .env is still production config"
grep -q "DEBUG=False" "$APP_DIR/.env" || {
  echo "!! WARNING: .env does not contain DEBUG=False — check before continuing!"
  exit 1
}

echo "==> Installing dependencies and running migrations"
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate

echo "==> Active database connection Django is actually using:"
python manage.py shell -c "from django.db import connection; d = connection.settings_dict; print('ENGINE:', d['ENGINE'], '| HOST:', d['HOST'], '| NAME:', d['NAME'])"

if [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
  echo "==> Ensuring a superuser exists"
  python manage.py createsuperuser --noinput || echo "   (superuser already exists — skipping)"
fi

python manage.py collectstatic --noinput
deactivate

echo "==> Starting $SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME"

echo "==> Verifying app responds"
curl -sf -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000 || {
  echo "!! App did not respond after deploy. Consider rollback:"
  echo "   sudo systemctl stop $SERVICE_NAME"
  echo "   cp -r $BACKUP_ROOT/* $APP_DIR/"
  if grep -qE '^(DB_ENGINE=django.db.backends.postgresql|DATABASE_URL=postgres)' "$APP_DIR/.env" 2>/dev/null; then
    echo "   pg_restore --clean --if-exists -h \$DB_HOST -p \$DB_PORT -U \$DB_USER -d \$DB_NAME $DB_BACKUP_DIR/arielhr_predeploy_${TIMESTAMP}.dump"
  else
    echo "   cp $DB_BACKUP_DIR/*_predeploy_${TIMESTAMP}.sqlite3 $APP_DIR/"
  fi
  echo "   sudo systemctl start $SERVICE_NAME"
  exit 1
}

echo "==> Deploy finished successfully: $TIMESTAMP"
