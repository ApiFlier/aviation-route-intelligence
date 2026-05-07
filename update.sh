#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"

echo "============================================="
echo "  FlightConn Update"
echo "============================================="

# Verify Docker is installed
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker is not installed." >&2
    exit 1
fi

# Verify Docker Compose plugin is available
if ! docker compose version &>/dev/null 2>&1; then
    echo "ERROR: Docker Compose plugin is not available." >&2
    exit 1
fi

# Verify .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found. Please run ./setup.sh first." >&2
    exit 1
fi

# Source .env
set -a
source "$ENV_FILE"
set +a

# 1. Run backup before making changes
echo "==> 1/3: Creating pre-update backup..."
if ! "$REPO_DIR/backup.sh"; then
    echo "ERROR: Backup failed. Aborting update for safety." >&2
    exit 1
fi

# 2. Rebuild and start containers
echo ""
echo "==> 2/3: Rebuilding and starting containers..."
cd "$REPO_DIR"
if ! docker compose up -d --build; then
    echo "ERROR: Docker Compose build/up failed." >&2
    exit 1
fi

# 3. Health check
echo ""
echo "==> 3/3: Verifying app health..."
APP_PORT="${APP_PORT:-8082}"
HEALTH_URL="http://localhost:${APP_PORT}/health"
APP_OK=false

for i in $(seq 1 15); do
    if curl -sf -o /dev/null "$HEALTH_URL"; then
        echo "    App is healthy."
        APP_OK=true
        break
    fi
    sleep 2
done

if [ "$APP_OK" = false ]; then
    echo "ERROR: App health check failed at $HEALTH_URL" >&2
    echo "--- App logs ---"
    docker compose logs --tail=50 app
    exit 1
fi

echo ""
echo "============================================="
echo "  Update Complete!"
echo "  URL: http://localhost:${APP_PORT}"
echo "============================================="
