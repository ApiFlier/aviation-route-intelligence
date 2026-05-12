#!/usr/bin/env bash
#
# update.sh - Production update script for FlightConn
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${REPO_DIR}/.env"
DEPLOY_ENV="${REPO_DIR}/deploy.env"

# --- Helpers ---
log_info()  { echo -e "[INFO]  $*"; }
log_warn()  { echo -e "[WARN]  $*"; }
log_error() { echo -e "[ERROR] $*" >&2; }

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed."
        exit 1
    fi
    if ! docker compose version &>/dev/null; then
        log_error "Docker Compose plugin is not available."
        exit 1
    fi
}

load_env() {
    if [ ! -f "$ENV_FILE" ]; then
        log_error ".env file not found. Please run ./setup.sh first."
        exit 1
    fi
    # Source .env
    set -a
    # shellcheck disable=SC1091
    source "$ENV_FILE"
    if [ -f "$DEPLOY_ENV" ]; then
        # shellcheck disable=SC1090
        source "$DEPLOY_ENV"
    fi
    set +a
}

detect_swim() {
    local has_user="${FAA_USER:-}"
    local has_pass="${FAA_PASS:-}"
    local has_queue=false
    for q in QUEUE_SFDPS QUEUE_STDDS QUEUE_TFMS; do
        if [ -n "${!q:-}" ]; then
            has_queue=true
            break
        fi
    done

    if [ -n "$has_user" ] && [ -n "$has_pass" ] && [ "$has_queue" = true ]; then
        echo "--profile swim"
    else
        echo ""
    fi
}

check_health() {
    log_info "Verifying health..."
    local port="${APP_PORT:-8082}"
    local health_url="http://localhost:${port}/health"
    local status_url="http://localhost:${port}/api/recent-activity/status"
    local app_ok=false

    for i in $(seq 1 15); do
        if curl -sf -o /dev/null "$health_url"; then
            log_info "App is healthy."
            app_ok=true
            break
        fi
        sleep 2
    done

    if [ "$app_ok" = false ]; then
        log_error "App health check failed at $health_url"
        exit 1
    fi

    # Check SWIM freshness if enabled
    local swim_opts
    swim_opts=$(detect_swim)
    if [[ "$swim_opts" == *"--profile swim"* ]]; then
        log_info "Checking SWIM freshness..."
        local status_json
        status_json=$(curl -s "$status_url" || echo "{}")
        if echo "$status_json" | grep -q "minutes_stale"; then
            local stale
            stale=$(echo "$status_json" | sed -n 's/.*"minutes_stale": \([0-9]*\).*/\1/p')
            log_info "SWIM freshness: ${stale:-unknown} minutes stale."
        else
            log_warn "Could not verify SWIM freshness from $status_url"
        fi
    fi
}

git_pull() {
    log_info "Checking repository state..."
    local status
    status=$(git status --porcelain)
    if [ -n "$status" ] && [ "${FORCE_UPDATE:-false}" != "true" ]; then
        log_error "Working tree is dirty. Please commit or stash changes."
        log_error "Or use --force to override."
        exit 1
    fi

    log_info "Pulling latest code..."
    local upstream
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "origin dev")

    # Split upstream into remote and branch
    local remote="${upstream%%/*}"
    local branch="${upstream#*/}"

    if ! git pull "$remote" "$branch"; then
        log_error "Git pull failed."
        exit 1
    fi
}

# --- Command Line Arguments ---
FORCE_UPDATE=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --force) FORCE_UPDATE=true; shift ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

# --- Main ---
check_docker
load_env

# 1. Disk Cleanup before
"${REPO_DIR}/scripts/health-cleanup.sh"

# 2. Git Pull
git_pull

# 3. Update Containers
log_info "Updating containers..."
SWIM_OPTS=$(detect_swim)
# shellcheck disable=SC2086
if ! docker compose $SWIM_OPTS up -d --build; then
    log_error "Docker Compose update failed."
    exit 1
fi

# 4. Health Check
check_health

# 5. Disk Cleanup after
"${REPO_DIR}/scripts/health-cleanup.sh"

log_info "Update successful!"
docker ps --filter "name=flightconn"
