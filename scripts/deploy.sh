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
cp "$APP_DIR/TestDB.sqlite3" "$DB_BACKUP_DIR/TestDB_predeploy_${TIMESTAMP}.sqlite3"

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
  echo "   cp $DB_BACKUP_DIR/TestDB_predeploy_${TIMESTAMP}.sqlite3 $APP_DIR/TestDB.sqlite3"
  echo "   sudo systemctl start $SERVICE_NAME"
  exit 1
}

echo "==> Deploy finished successfully: $TIMESTAMP"
