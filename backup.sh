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

# Parse arguments
DRY_RUN=false
for arg in "$@"; do
    if [ "$arg" == "--dry-run" ]; then
        DRY_RUN=true
    fi
done

if [ ! -f "$ENV_FILE" ]; then
    log_error ".env file not found. Run ./setup.sh first."
    exit 1
fi

# Source .env to get credentials for local script context if needed,
# but we primarily rely on container environment variables.
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

# 1. Verify DB container is running
if [ "$(docker inspect -f '{{.State.Running}}' flightconn-db 2>/dev/null || echo false)" != "true" ]; then
    log_error "flightconn-db container is not running."
    exit 1
fi

# 2. Verify DB is reachable
if ! docker exec flightconn-db sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" -e "SELECT 1;"' >/dev/null 2>&1; then
    log_error "Database is not reachable inside the container."
    exit 1
fi

if [ "$DRY_RUN" = "true" ]; then
    log_info "DRY RUN: Commands that would be executed:"
    IGNORE_OPTS=""
    for TABLE in "${EXCLUDE_DATA_TABLES[@]}"; do
        IGNORE_OPTS="${IGNORE_OPTS} --ignore-table=\$MYSQL_DATABASE.${TABLE}"
    done
    echo "docker exec flightconn-db sh -lc 'mysqldump -uroot -p\"\$MYSQL_ROOT_PASSWORD\" \"\$MYSQL_DATABASE\" $IGNORE_OPTS' > TEMP_DUMP"
    echo "docker exec flightconn-db sh -lc 'mysqldump -uroot -p\"\$MYSQL_ROOT_PASSWORD\" --no-data \"\$MYSQL_DATABASE\" ${EXCLUDE_DATA_TABLES[*]}' >> TEMP_DUMP"
    echo "gzip -c TEMP_DUMP > TEMP_GZ"
    echo "mv TEMP_GZ $BASELINE_PATH"
    exit 0
fi

# Create temporary files for the dump and compression
TEMP_DUMP=$(mktemp)
TEMP_GZ=$(mktemp)

# Ensure temp files are removed on exit
trap 'rm -f "$TEMP_DUMP" "$TEMP_GZ"' EXIT

# Pass 1: Dump all tables EXCEPT the excluded ones
IGNORE_OPTS=""
for TABLE in "${EXCLUDE_DATA_TABLES[@]}"; do
    IGNORE_OPTS="${IGNORE_OPTS} --ignore-table=\$MYSQL_DATABASE.${TABLE}"
done

log_info "Dumping core data..."
# shellcheck disable=SC2086
if ! docker exec flightconn-db sh -lc "mysqldump -uroot -p\"\$MYSQL_ROOT_PASSWORD\" \"\$MYSQL_DATABASE\" $IGNORE_OPTS" > "$TEMP_DUMP"; then
    log_error "Core data dump failed."
    exit 1
fi

# Pass 2: Dump ONLY the schema for the excluded tables
log_info "Dumping SWIM runtime schemas (no data)..."
if ! docker exec flightconn-db sh -lc "mysqldump -uroot -p\"\$MYSQL_ROOT_PASSWORD\" --no-data \"\$MYSQL_DATABASE\" ${EXCLUDE_DATA_TABLES[*]}" >> "$TEMP_DUMP"; then
    log_warn "Schema-only dump failed for one or more runtime tables. The baseline backup was created, but runtime table schemas may be incomplete."
    # We continue anyway if core data was successful
fi

# Compress to temp file
log_info "Compressing..."
if ! gzip -c "$TEMP_DUMP" > "$TEMP_GZ"; then
    log_error "Compression failed."
    exit 1
fi

if [ ! -s "$TEMP_GZ" ]; then
    log_error "Generated backup file is empty!"
    exit 1
fi

# Move to baseline path
mv "$TEMP_GZ" "$BASELINE_PATH"

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
