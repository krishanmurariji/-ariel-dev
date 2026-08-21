#!/usr/bin/env bash
# scripts/deploy.sh
# Runs ON THE SERVER (via the self-hosted GitHub Actions runner) after
# manually triggering the workflow from the Actions tab.

set -euo pipefail

APP_DIR="/opt/arielhr"
SERVICE_NAME="arielhr"
BACKUP_ROOT="$HOME/rollback_$(date +%Y%m%d_%H%M%S)"
DB_BACKUP_DIR="/opt/arielhr_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "==> Deploy started: $TIMESTAMP"

# 1. Stop the app
echo "==> Stopping $SERVICE_NAME"
sudo systemctl stop "$SERVICE_NAME"

# 2. Backup current production code + db (safety net)
echo "==> Backing up current code to $BACKUP_ROOT"
mkdir -p "$BACKUP_ROOT"
cp -r "$APP_DIR"/* "$APP_DIR/.env" "$BACKUP_ROOT/" 2>/dev/null || true

sudo mkdir -p "$DB_BACKUP_DIR"
sudo cp "$APP_DIR/TestDB.sqlite3" "$DB_BACKUP_DIR/TestDB_predeploy_${TIMESTAMP}.sqlite3"

# 3. Pull latest code
echo "==> Pulling latest code"
cd "$APP_DIR"
git fetch origin
git reset --hard origin/main
# .env, TestDB.sqlite3, venv, staticfiles, media are all git-ignored,
# so reset --hard will not touch them.

# 4. Confirm .env wasn't touched (paranoia check)
echo "==> Verifying .env is still production config"
grep -q "DEBUG=False" "$APP_DIR/.env" || {
  echo "!! WARNING: .env does not contain DEBUG=False — check before continuing!"
  exit 1
}

# 5. Install deps, migrate, collect static
echo "==> Installing dependencies and running migrations"
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
deactivate

# 6. Start and verify
echo "==> Starting $SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager

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
