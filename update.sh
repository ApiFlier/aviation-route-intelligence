#!/usr/bin/env bash
#
# update.sh - Production update script for FlightConn
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${HOME}/.flightconn/backups"
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

backup_db() {
    local label=$1
    local timestamp
    timestamp=$(date +%Y%m%d-%H%M%S)
    local timestamp_file="${BACKUP_DIR}/flightconn-${timestamp}.sql.gz"
    local latest_file="${BACKUP_DIR}/flightconn-latest.sql.gz"

    mkdir -p "$BACKUP_DIR"
    log_info "Creating database backup ($label)..."

    # Use docker exec to dump the database
    if ! docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysqldump -uroot flightconn | gzip > "$timestamp_file"; then
        log_error "Backup failed!"
        return 1
    fi

    if [ ! -s "$timestamp_file" ]; then
        log_error "Backup file is empty!"
        rm -f "$timestamp_file"
        return 1
    fi

    cp "$timestamp_file" "$latest_file"
    log_info "Backup created: $timestamp_file"
    log_info "Latest backup link updated: $latest_file"
    return 0
}

restore_db() {
    local backup_file=$1
    if [ ! -f "$backup_file" ]; then
        log_error "Backup file not found: $backup_file"
        exit 1
    fi

    log_warn "RESTORE ACTION: This will overwrite the database with $backup_file"
    if [ "${FORCE_YES:-false}" != "true" ]; then
        echo "Type 'RESTORE' to confirm:"
        read -r confirm
        if [ "$confirm" != "RESTORE" ]; then
            log_info "Restore cancelled."
            exit 0
        fi
    fi

    local swim_opts
    swim_opts=$(detect_swim)

    log_info "Stopping app and swim containers..."
    # shellcheck disable=SC2086
    docker compose $swim_opts stop app swim-ingestor || true

    log_info "Restoring database..."
    if ! gunzip -c "$backup_file" | docker exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysql -uroot flightconn; then
        log_error "Restore failed!"
        exit 1
    fi

    log_info "Restarting containers..."
    # shellcheck disable=SC2086
    docker compose $swim_opts up -d

    check_health
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
        log_info "Rollback instruction: ./update.sh --rollback"
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
            if [ -n "$stale" ] && [ "$stale" -gt 60 ]; then
                log_warn "SWIM data is quite stale (> 60 mins)."
            fi
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

    local old_head
    old_head=$(git rev-parse HEAD)
    
    log_info "Pulling latest code..."
    local upstream
    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null || echo "origin dev")

    log_info "Upstream: $upstream"
    # Split upstream into remote and branch
    local remote="${upstream%%/*}"
    local branch="${upstream#*/}"

    if ! git pull "$remote" "$branch"; then
        log_error "Git pull failed."
        exit 1
    fi

    local new_head
    new_head=$(git rev-parse HEAD)
    log_info "Old commit: $old_head"
    log_info "New commit: $new_head"
}

# --- Command Line Arguments ---
MODE="update"
FORCE_UPDATE=false
FORCE_YES=false
RESTORE_FILE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --force) FORCE_UPDATE=true; shift ;;
        --yes|-y) FORCE_YES=true; shift ;;
        --restore-latest) MODE="restore"; RESTORE_FILE="${BACKUP_DIR}/flightconn-latest.sql.gz"; shift ;;
        --restore) MODE="restore"; RESTORE_FILE="$2"; shift 2 ;;
        --rollback) MODE="restore"; RESTORE_FILE="${BACKUP_DIR}/flightconn-latest.sql.gz"; shift ;;
        --dry-run) MODE="dry-run"; shift ;;
        *) log_error "Unknown option: $1"; exit 1 ;;
    esac
done

# --- Main Logic ---
check_docker
load_env

case $MODE in
    update)
        # 1. Disk Cleanup before
        "${REPO_DIR}/scripts/health-cleanup.sh"

        # 2. Git Pull
        git_pull

        # 3. Backup DB
        if ! backup_db "pre-update"; then
            log_error "Pre-update backup failed. Aborting."
            exit 1
        fi

        # 4. Update Containers
        log_info "Updating containers..."
        local swim_opts
        swim_opts=$(detect_swim)
        # shellcheck disable=SC2086
        if ! docker compose $swim_opts up -d --build; then
            log_error "Docker Compose update failed."
            exit 1
        fi

        # 5. Health Check
        check_health

        # 6. Disk Cleanup after
        "${REPO_DIR}/scripts/health-cleanup.sh"

        log_info "Update successful!"
        ;;

    restore)
        restore_db "$RESTORE_FILE"
        ;;

    dry-run)
        log_info "Dry run: detecting config..."
        log_info "SWIM profile: $(detect_swim)"
        log_info "App Port: ${APP_PORT:-8082}"
        log_info "Repo Dir: $REPO_DIR"
        log_info "Backup Dir: $BACKUP_DIR"
        ;;
esac
