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
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

# ── Load deploy.env if present ────────────────────────────────────────
DEPLOY_ENV="$REPO_DIR/deploy.env"
if [ -f "$DEPLOY_ENV" ]; then
    echo "==> Loading deploy.env..."
    set -a
    # shellcheck disable=SC1090
    source "$DEPLOY_ENV"
    set +a
fi

# ── Auto-detect SWIM readiness from FAA credentials ───────────────────
FAA_USER_VAL=$(printenv FAA_USER 2>/dev/null || true)
FAA_PASS_VAL=$(printenv FAA_PASS 2>/dev/null || true)
QUEUE_OK=false
for Q_VAR in QUEUE_SFDPS QUEUE_STDDS QUEUE_TFMS; do
    QVAL=$(printenv "$Q_VAR" 2>/dev/null || true)
    if [ -n "$QVAL" ]; then
        QUEUE_OK=true
    fi
done

SWIM_READY=false
if [ -n "$FAA_USER_VAL" ] && [ -n "$FAA_PASS_VAL" ] && [ "$QUEUE_OK" = "true" ]; then
    SWIM_READY=true
fi

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

COMPOSE_OPTS=""
if [ "$SWIM_READY" = "true" ]; then
    echo "    SWIM detected: including 'swim' profile."
    COMPOSE_OPTS="--profile swim"
fi

# shellcheck disable=SC2086
if ! docker compose $COMPOSE_OPTS up -d --build; then
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
