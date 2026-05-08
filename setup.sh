#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
ENV_FILE="$REPO_DIR/.env"
BACKUP="$REPO_DIR/api/Data/db_backup.sql.gz"

echo "============================================="
echo "  FlightConn Setup"
echo "============================================="
echo "  Repo: $REPO_DIR"
echo ""

# Verify docker-compose.yml exists
if [ ! -f "$COMPOSE_FILE" ]; then
    echo "ERROR: docker-compose.yml not found at $REPO_DIR" >&2
    exit 1
fi

# Verify Docker is installed
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed." >&2
    echo "  Install it with: curl -fsSL https://get.docker.com | sh" >&2
    exit 1
fi

# Verify Docker Compose plugin is available
if ! docker compose version &>/dev/null 2>&1; then
    echo "ERROR: Docker Compose plugin is not available." >&2
    echo "  Ensure Docker Engine is up to date: https://docs.docker.com/engine/install/" >&2
    exit 1
fi

# Verify the database backup exists
if [ ! -f "$BACKUP" ]; then
    echo "ERROR: Database backup not found: $BACKUP" >&2
    exit 1
fi

# Helper: find next open TCP port starting at $1
next_open_port() {
    local port=$1
    while nc -z localhost "$port" 2>/dev/null; do
        port=$((port + 1))
    done
    echo "$port"
}

# ── Detect whether the persistent DB volume already exists ────────────
VOLUME_EXISTS=false
if docker volume inspect flightconn_mysql >/dev/null 2>&1; then
    VOLUME_EXISTS=true
fi

# ── .env + volume mismatch: bail before touching anything ─────────────
# The MySQL container was initialized with credentials stored in .env.
# If .env is gone but the volume still exists, any new credentials will
# be rejected by the running database — nothing will work.
if [ "$VOLUME_EXISTS" = "true" ] && [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "============================================="
    echo "  Cannot Proceed: .env Missing"
    echo "============================================="
    echo ""
    echo "  An existing FlightConn database volume was found (flightconn_mysql)"
    echo "  but .env is missing. The database was initialized with credentials"
    echo "  that setup can no longer access."
    echo ""
    echo "  Option A — Restore the original .env and re-run:"
    echo "    cp /your/backup/.env $REPO_DIR/.env"
    echo "    ./setup.sh"
    echo ""
    echo "  Option B — Reset the local FlightConn database volume and start fresh."
    echo "    WARNING: All existing local database data will be permanently deleted."
    echo ""
    printf "  Reset the FlightConn database volume and start fresh?"
    printf " This permanently deletes local database data. [y/N] "
    RESET_CHOICE=""
    read -r RESET_CHOICE < /dev/tty || true
    echo ""

    if [ "$RESET_CHOICE" = "y" ] || [ "$RESET_CHOICE" = "Y" ]; then
        echo "==> Stopping and removing existing containers..."
        docker stop flightconn-app flightconn-db 2>/dev/null || true
        docker rm   flightconn-app flightconn-db 2>/dev/null || true
        echo "==> Removing FlightConn database volume..."
        docker volume rm flightconn_mysql
        echo "    Volume removed. Proceeding with fresh install."
        VOLUME_EXISTS=false
    else
        echo "  Aborting. No data was changed."
        echo "  Restore .env from a backup, then re-run ./setup.sh"
        exit 1
    fi
fi

# ── Warn about an orphaned legacy volume (if present) ────────────────
# Older setups without an explicit volume name created a project-prefixed
# volume (e.g. flightconn_flightconn_mysql). It is no longer used and
# can be removed when you are sure you no longer need it.
LEGACY_VOL=$(docker volume ls --format '{{.Name}}' 2>/dev/null \
    | grep -E '_flightconn_mysql$' \
    | grep -v '^flightconn_mysql$' \
    | head -1)
if [ -n "$LEGACY_VOL" ]; then
    echo ""
    echo "  Note: Found an older FlightConn database volume: $LEGACY_VOL"
    echo "  It is no longer used by this setup and can be removed when ready:"
    echo "    docker volume rm $LEGACY_VOL"
fi

# ── Generate or update .env ───────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    echo "==> Generating .env with random credentials..."

    MYSQL_ROOT_PASSWORD=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40)
    DB_PASSWORD=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40)
    FLASK_SECRET=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 40)
    APP_PORT=$(next_open_port 8082)

    cat > "$ENV_FILE" <<EOF
MYSQL_ROOT_PASSWORD=$MYSQL_ROOT_PASSWORD
DB_PASSWORD=$DB_PASSWORD
FLASK_SECRET=$FLASK_SECRET
APP_PORT=$APP_PORT
EOF
    chmod 600 "$ENV_FILE"
    echo "    Created .env (APP_PORT=$APP_PORT)"
else
    echo "==> Checking .env..."
    # Source existing .env
    set -a
    source "$ENV_FILE"
    set +a

    # Migrate any old per-service port variables and ensure APP_PORT is set
    START_PORT="${APP_PORT:-${FRONTEND_PORT:-8082}}"
    FINAL_PORT=$(next_open_port "$START_PORT")

    if [ "$FINAL_PORT" != "$APP_PORT" ] || ! grep -q "APP_PORT=" "$ENV_FILE"; then
        sed -i '/FRONTEND_PORT=/d' "$ENV_FILE"
        sed -i '/API_PORT=/d' "$ENV_FILE"
        sed -i '/APP_PORT=/d' "$ENV_FILE"
        echo "APP_PORT=$FINAL_PORT" >> "$ENV_FILE"
        echo "    Updated APP_PORT to $FINAL_PORT."
        APP_PORT=$FINAL_PORT
    else
        echo "    .env is up to date (APP_PORT=$APP_PORT)."
    fi
fi

# Source .env again to ensure we have the latest
set -a
source "$ENV_FILE"
set +a

# Build and start containers
echo ""
echo "==> Building and starting containers..."
cd "$REPO_DIR"
docker compose up -d --build

# Wait for MySQL to be healthy
echo ""
echo "==> Waiting for MySQL to become healthy..."
MAX_WAIT=120
WAITED=0
until [ "$(docker inspect --format='{{.State.Health.Status}}' flightconn-db 2>/dev/null)" = "healthy" ]; do
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo "ERROR: MySQL did not become healthy within ${MAX_WAIT}s." >&2
        echo "--- DB logs ---"
        docker compose logs --tail=30 db
        exit 1
    fi
    sleep 3
    WAITED=$((WAITED + 3))
    printf "    ...%ds elapsed\r" "$WAITED"
done
echo "    MySQL is healthy.                    "

# ── Verify credentials before touching any data ───────────────────────
# Guards against the edge case where .env was regenerated while an existing
# volume still holds the original MySQL root password.
echo ""
echo "==> Verifying database credentials..."
if ! docker exec \
       -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" \
       flightconn-db \
       mysql -uroot -sNe "SELECT 1;" >/dev/null 2>&1; then
    echo ""
    echo "ERROR: Cannot authenticate with the database." >&2
    echo "  The credentials in .env do not match the existing database volume." >&2
    echo ""
    echo "  This usually means .env was regenerated while the database volume" >&2
    echo "  (flightconn_mysql) still held the original MySQL root password." >&2
    echo ""
    echo "  To fix:" >&2
    echo "    docker compose down" >&2
    echo "    docker volume rm flightconn_mysql" >&2
    echo "    ./setup.sh" >&2
    exit 1
fi
echo "    Credentials OK."

# Check if database already has data
HAS_DATA=$(docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysql -uroot flightconn -sNe "SELECT COUNT(*) FROM airports;" 2>/dev/null || echo "0")

if [ "$HAS_DATA" -eq "0" ]; then
    # Restore database from backup
    echo ""
    echo "==> Restoring database from api/Data/db_backup.sql.gz..."
    gunzip -c "$BACKUP" \
      | docker exec -i \
          -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" \
          flightconn-db \
          mysql -uroot flightconn
    echo "    Restore complete."
else
    echo ""
    echo "==> Database already contains data, skipping restore."
fi

# Verify row counts
echo ""
echo "==> Verifying row counts..."
VERIFY_OK=true
for TABLE in routes route_carriers route_fares route_schedules carrier_network; do
    COUNT=$(docker exec \
        -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" \
        flightconn-db \
        mysql -uroot flightconn -sNe "SELECT COUNT(*) FROM \`$TABLE\`;" 2>/dev/null || echo "ERROR")
    if [ "$COUNT" = "ERROR" ] || [ "$COUNT" = "0" ]; then
        echo "    WARNING: $TABLE has $COUNT rows"
        VERIFY_OK=false
    else
        printf "    %-20s %s rows\n" "$TABLE" "$COUNT"
    fi
done

# Verify app responds
echo ""
echo "==> Verifying app..."
APP_OK=false
for i in $(seq 1 15); do
    if curl -sf -o /dev/null "http://localhost:${APP_PORT}/health"; then
        echo "    App OK (API health check)."
        APP_OK=true
        break
    fi
    sleep 2
done

if [ "$APP_OK" = true ]; then
    if curl -sf -o /dev/null "http://localhost:${APP_PORT}/"; then
        echo "    App OK (Frontend check)."
    else
        echo "    WARNING: Frontend not responding at http://localhost:${APP_PORT}"
        APP_OK=false
    fi
fi

if [ "$APP_OK" = false ]; then
    echo "    WARNING: App not responding correctly at http://localhost:${APP_PORT}" >&2
    echo "    Try: docker compose logs app"
fi

# Summary
echo ""
echo "============================================="
echo "  FlightConn is running!"
echo ""
echo "  URL:       http://localhost:${APP_PORT}"
echo "  Career:    http://localhost:${APP_PORT}/career/"
echo "  API:       http://localhost:${APP_PORT}/api"
echo "  Health:    http://localhost:${APP_PORT}/health"
echo "============================================="
echo ""
echo "Deployment Details:"
echo "  - APP_PORT: Selected $APP_PORT as the host port."
echo "  - Database: Stored in a Docker named volume (flightconn_mysql)."
echo "  - Persistence: Data persists even if containers are stopped or removed."
echo "  - WARNING: Never run 'docker compose down -v' unless you want to WIPE the database."
echo "  - Repository: Local files are only needed for rebuilding (./update.sh) or setup."
echo ""
echo "Useful commands:"
echo "  ./update.sh          # Rebuild after code changes (auto-backups first)"
echo "  ./backup.sh          # Create a manual DB backup"
echo "  ./restore.sh <file>  # Restore a DB backup"
echo "  docker compose logs -f"
echo "  docker compose down"
echo ""

# Offer to remove local source files (the running app and volumes are unaffected)
printf "Delete local source files now? [y/N] "
DEL_CHOICE=""
read -r DEL_CHOICE < /dev/tty || true
if [ "${DEL_CHOICE}" = "y" ] || [ "${DEL_CHOICE}" = "Y" ]; then
    echo "==> Removing local source files..."
    cd "$HOME" 2>/dev/null || cd / 2>/dev/null || true
    rm -rf "$REPO_DIR"
    echo "    Done. The running app and Docker volumes are preserved."
else
    echo "    Source files preserved."
fi
