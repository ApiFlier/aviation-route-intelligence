#!/usr/bin/env bash
#
# backup.sh - Refreshes the FlightConn repository baseline backup
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${REPO_DIR}/.env"
BASELINE_PATH="${REPO_DIR}/api/Data/flightconn-baseline.sql.gz"

# --- Helpers ---
log_info()  { echo -e "[INFO]  $*"; }
log_warn()  { echo -e "[WARN]  $*"; }
log_error() { echo -e "[ERROR] $*" >&2; }

if [ ! -f "$ENV_FILE" ]; then
    log_error ".env file not found. Run ./setup.sh first."
    exit 1
fi

# Source .env
set -a
# shellcheck disable=SC1091
source "$ENV_FILE"
set +a

# List of high-churn/disposable tables to exclude data for (schema only)
EXCLUDE_DATA_TABLES=(
    "observed_flights"
    "observed_flight_enrichment"
    "observed_flight_events"
    "swim_source_messages"
    "swim_ingestion_runs"
    "recent_route_activity"
    "recent_route_carrier_activity"
    "route_historical_recent_comparison"
)

# --- Main ---
log_info "Refreshing baseline backup: $BASELINE_PATH"

# Verify DB container is running
if ! docker inspect -f '{{.State.Running}}' flightconn-db >/dev/null 2>&1; then
    log_error "flightconn-db container is not running."
    exit 1
fi

# Create a temporary file for the dump
TEMP_DUMP=$(mktemp)

# Pass 1: Dump all tables EXCEPT the excluded ones
IGNORE_OPTS=""
for TABLE in "${EXCLUDE_DATA_TABLES[@]}"; do
    IGNORE_OPTS="${IGNORE_OPTS} --ignore-table=flightconn.${TABLE}"
done

log_info "Dumping core data..."
# shellcheck disable=SC2086
docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db \
    mysqldump -uroot flightconn $IGNORE_OPTS > "$TEMP_DUMP"

# Pass 2: Dump ONLY the schema for the excluded tables
log_info "Dumping SWIM runtime schemas (no data)..."
docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db \
    mysqldump -uroot --no-data flightconn "${EXCLUDE_DATA_TABLES[@]}" >> "$TEMP_DUMP"

# Compress and move to baseline path
log_info "Compressing..."
gzip -c "$TEMP_DUMP" > "$BASELINE_PATH"
rm "$TEMP_DUMP"

if [ ! -s "$BASELINE_PATH" ]; then
    log_error "Generated backup file is empty!"
    exit 1
fi

log_info "Baseline backup refreshed successfully: $(du -h "$BASELINE_PATH" | cut -f1)"

# Ask to push to GitHub
printf "Push the refreshed baseline backup to GitHub? [y/N] "
read -r PUSH_CHOICE < /dev/tty || true

if [ "$PUSH_CHOICE" = "y" ] || [ "$PUSH_CHOICE" = "Y" ]; then
    log_info "Staging and committing baseline backup..."
    git add "$BASELINE_PATH"
    git commit -m "Update FlightConn baseline database backup"
    
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Pushing to origin $CURRENT_BRANCH..."
    if git push origin "$CURRENT_BRANCH"; then
        log_info "Backup successfully pushed to GitHub."
    else
        log_error "Git push failed."
        exit 1
    fi
else
    log_warn "This backup exists only in the local repo working tree until committed/pushed."
    log_warn "If local files are deleted, this backup will be lost."
fi
