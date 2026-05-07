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

# Generate/Update .env
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
    echo "==> Updating .env for new architecture..."
    # Source existing .env
    set -a
    source "$ENV_FILE"
    set +a

    # Determine starting port for search
    START_PORT="${APP_PORT:-${FRONTEND_PORT:-8082}}"
    
    # Check if APP_PORT is missing or if we need to verify availability
    FINAL_PORT=$(next_open_port "$START_PORT")
    
    if [ "$FINAL_PORT" != "$APP_PORT" ] || ! grep -q "APP_PORT=" "$ENV_FILE"; then
        # Remove old PORT variables if they exist to keep it clean
        sed -i '/FRONTEND_PORT=/d' "$ENV_FILE"
        sed -i '/API_PORT=/d' "$ENV_FILE"
        sed -i '/APP_PORT=/d' "$ENV_FILE"
        echo "APP_PORT=$FINAL_PORT" >> "$ENV_FILE"
        echo "    Updated .env (APP_PORT=$FINAL_PORT)"
        APP_PORT=$FINAL_PORT
    else
        echo "    .env is already up to date (APP_PORT=$APP_PORT)."
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
echo "Useful commands:"
echo "  ./backup.sh          # Create a DB backup"
echo "  ./restore.sh <file>  # Restore a DB backup"
echo "  docker compose logs -f"
echo "  docker compose down"
echo ""
