#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/arielhr"
SERVICE_NAME="arielhr"
BACKUP_ROOT="$HOME/rollback_$(date +%Y%m%d_%H%M%S)"
DB_BACKUP_DIR="/opt/arielhr_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
KEEP_ROLLBACKS=3

# Deletes all but the newest $1 rollback_YYYYMMDD_HHMMSS folders in $HOME. The
# timestamp in the name sorts chronologically, so newest-first is a reverse
# sort. Anything that doesn't match that exact name shape, or is a symlink,
# is left alone.
prune_old_rollbacks() {
  local keep="$1" seen=0 d
  for d in $(ls -1d "$HOME"/rollback_* 2>/dev/null | sort -r); do
    [[ "$d" =~ /rollback_[0-9]{8}_[0-9]{6}$ ]] || continue
    [ -d "$d" ] && [ ! -L "$d" ] || continue
    seen=$((seen + 1))
    if [ "$seen" -gt "$keep" ]; then
      echo "   removing old rollback point $d"
      rm -rf -- "$d"
    fi
  done
}

echo "==> Deploy started: $TIMESTAMP"

echo "==> Stopping $SERVICE_NAME"
sudo systemctl stop "$SERVICE_NAME"

# A rollback point is the commit that was running plus the untracked config
# files - NOT a copy of the whole app folder. The old version copied
# everything under $APP_DIR (the virtualenv, media, static files, ...) into
# $HOME on every deploy - about 3.2 GB each, never deleted - which filled
# the /home disk to 99%. Code is restored from git and dependencies are
# reinstalled from requirements.txt, so neither needs copying; .env and
# local_settings.py are untracked (git reset leaves them alone) but are kept
# here in case they were edited.
echo "==> Recording the rollback point in $BACKUP_ROOT"
mkdir -p -m 700 "$BACKUP_ROOT"
git -C "$APP_DIR" rev-parse HEAD > "$BACKUP_ROOT/previous_commit.txt" 2>/dev/null   || echo "unknown" > "$BACKUP_ROOT/previous_commit.txt"
cp "$APP_DIR/.env" "$BACKUP_ROOT/.env" 2>/dev/null || true
[ -f "$APP_DIR/horilla/settings/local_settings.py" ]   && cp "$APP_DIR/horilla/settings/local_settings.py" "$BACKUP_ROOT/local_settings.py" || true
chmod 600 "$BACKUP_ROOT"/.env "$BACKUP_ROOT"/local_settings.py 2>/dev/null || true
echo "   commit that was running: $(cat "$BACKUP_ROOT/previous_commit.txt")"

echo "==> Pruning old rollback points (keeping the newest $KEEP_ROLLBACKS)"
prune_old_rollbacks "$KEEP_ROLLBACKS"

if [ -n "${DB_HOST:-}" ]; then
  if grep -q '^DATABASE_URL=' "$APP_DIR/.env" 2>/dev/null; then
    echo "==> Disabling existing DATABASE_URL in .env (it would override DB_* vars)"
    sed -i 's|^DATABASE_URL=|#DATABASE_URL=|' "$APP_DIR/.env"
  fi
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

if [ -n "${CHANNEL_LAYER_BACKEND:-}" ]; then
  echo "==> Updating chat WebSocket channel layer config in .env from pipeline secrets"
  for VAR in CHANNEL_LAYER_BACKEND CHANNEL_LAYER_REDIS_URL; do
    VALUE="${!VAR:-}"
    [ -z "$VALUE" ] && continue
    if grep -q "^${VAR}=" "$APP_DIR/.env" 2>/dev/null; then
      sed -i "s|^${VAR}=.*|${VAR}=${VALUE}|" "$APP_DIR/.env"
    else
      echo "${VAR}=${VALUE}" >> "$APP_DIR/.env"
    fi
  done
  echo "==> .env channel-layer lines after update (values masked):"
  grep -E '^(CHANNEL_LAYER_BACKEND|CHANNEL_LAYER_REDIS_URL)=' "$APP_DIR/.env" | sed -E 's/=.*/=***/'
fi

mkdir -p "$DB_BACKUP_DIR"
if grep -qE '^(DB_ENGINE=django.db.backends.postgresql|DATABASE_URL=postgres)' "$APP_DIR/.env" 2>/dev/null; then
  echo "==> Backing up Postgres database"
  command -v pg_dump >/dev/null || {
    echo "!! pg_dump not found. One-time setup on this server:"
    echo "   sudo apt-get install -y postgresql-client"
    exit 1
  }
  ENV_DATABASE_URL=$(grep -m1 '^DATABASE_URL=' "$APP_DIR/.env" | cut -d= -f2- || true)
  ENV_DB_HOST=$(grep -m1 '^DB_HOST=' "$APP_DIR/.env" | cut -d= -f2- || true)
  ENV_DB_PORT=$(grep -m1 '^DB_PORT=' "$APP_DIR/.env" | cut -d= -f2- || true)
  ENV_DB_USER=$(grep -m1 '^DB_USER=' "$APP_DIR/.env" | cut -d= -f2- || true)
  ENV_DB_PASSWORD=$(grep -m1 '^DB_PASSWORD=' "$APP_DIR/.env" | cut -d= -f2- || true)
  ENV_DB_NAME=$(grep -m1 '^DB_NAME=' "$APP_DIR/.env" | cut -d= -f2- || true)
  echo "==> pg_dump connection source: DATABASE_URL=${ENV_DATABASE_URL:+present} DB_HOST=${ENV_DB_HOST:-<empty>} DB_PORT=${ENV_DB_PORT:-<empty>} DB_USER=${ENV_DB_USER:-<empty>} DB_NAME=${ENV_DB_NAME:-<empty>} DB_PASSWORD=${ENV_DB_PASSWORD:+present}"
  PG_DUMP_FILE="$DB_BACKUP_DIR/arielhr_predeploy_${TIMESTAMP}.dump"
  if [ -n "$ENV_DATABASE_URL" ]; then
    pg_dump --dbname="$ENV_DATABASE_URL" -F c -f "$PG_DUMP_FILE"
  else
    PGPASSWORD="$ENV_DB_PASSWORD" pg_dump -h "$ENV_DB_HOST" -p "${ENV_DB_PORT:-5432}" -U "$ENV_DB_USER" -d "$ENV_DB_NAME" -F c -f "$PG_DUMP_FILE"
  fi
else
  echo "==> Backing up SQLite database"
  for f in "$APP_DIR"/*.sqlite3; do
    [ -e "$f" ] || continue
    cp "$f" "$DB_BACKUP_DIR/$(basename "$f" .sqlite3)_predeploy_${TIMESTAMP}.sqlite3"
  done
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
  echo "   cd $APP_DIR && git reset --hard $(cat "$BACKUP_ROOT/previous_commit.txt")"
  echo "   source venv/bin/activate && pip install -r requirements.txt"
  echo "   (the .env from before this deploy is saved at $BACKUP_ROOT/.env)"
  if grep -qE '^(DB_ENGINE=django.db.backends.postgresql|DATABASE_URL=postgres)' "$APP_DIR/.env" 2>/dev/null; then
    echo "   pg_restore --clean --if-exists -h \$DB_HOST -p \$DB_PORT -U \$DB_USER -d \$DB_NAME $DB_BACKUP_DIR/arielhr_predeploy_${TIMESTAMP}.dump"
  else
    echo "   cp $DB_BACKUP_DIR/*_predeploy_${TIMESTAMP}.sqlite3 $APP_DIR/"
  fi
  echo "   sudo systemctl start $SERVICE_NAME"
  exit 1
}

echo "==> Deploy finished successfully: $TIMESTAMP"
