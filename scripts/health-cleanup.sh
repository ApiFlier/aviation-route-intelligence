#!/usr/bin/env bash
#
# scripts/health-cleanup.sh
# Safely prunes old backups and disposable Docker artifacts to free disk space.
#
set -euo pipefail

# --- Configuration / Thresholds ---
DISK_WARN_PCT=${DISK_WARN_PCT:-85}
DISK_CLEAN_PCT=${DISK_CLEAN_PCT:-90}
DISK_TARGET_PCT=${DISK_TARGET_PCT:-85}

BACKUP_DIR="${HOME}/.flightconn/backups"
RETENTION_DAYS=14
MIN_TIMESTAMPED_BACKUPS=7

# --- Helpers ---
log_info()  { echo -e "[INFO]  $*"; }
log_warn()  { echo -e "[WARN]  $*"; }
log_error() { echo -e "[ERROR] $*" >&2; }

get_disk_usage() {
    # Returns the % usage of the filesystem containing Docker data or /
    # On most systems, / or /var/lib/docker is the relevant one.
    df -P / | awk 'NR==2 {print $5}' | sed 's/%//'
}

cleanup_backups() {
    log_info "Checking for old backups in $BACKUP_DIR..."
    if [ ! -d "$BACKUP_DIR" ]; then
        log_info "Backup directory does not exist. Skipping backup cleanup."
        return
    fi

    # 1. Keep 'flightconn-latest.sql.gz' (always)
    # 2. Keep backups from the last $RETENTION_DAYS days
    # 3. Keep at least $MIN_TIMESTAMPED_BACKUPS newest timestamped backups

    # Find all timestamped backups (flightconn-YYYYmmdd-HHMMSS.sql.gz)
    # Note: excluding 'flightconn-latest.sql.gz'
    mapfile -t backups < <(find "$BACKUP_DIR" -maxdepth 1 -name "flightconn-*.sql.gz" ! -name "flightconn-latest.sql.gz" -type f | sort -r)

    local count=${#backups[@]}
    log_info "Found $count timestamped backups."

    local deleted_count=0
    local now
    now=$(date +%s)

    for ((i=0; i<count; i++)); do
        local file="${backups[i]}"
        local mtime
        mtime=$(stat -c %Y "$file")
        local age_days=$(( (now - mtime) / 86400 ))

        # Logic:
        # If index >= MIN_TIMESTAMPED_BACKUPS (we have enough newer ones)
        # AND age_days > RETENTION_DAYS
        # THEN delete.
        if [ "$i" -ge "$MIN_TIMESTAMPED_BACKUPS" ] && [ "$age_days" -gt "$RETENTION_DAYS" ]; then
            log_info "Pruning old backup: $(basename "$file") (Age: ${age_days}d)"
            rm "$file"
            deleted_count=$((deleted_count + 1))
        fi
    done

    log_info "Deleted $deleted_count old backups."
}

cleanup_docker() {
    log_info "Pruning Docker artifacts..."

    # Prune Docker build cache older than 24 hours
    log_info "Pruning Docker build cache (>24h)..."
    docker builder prune -af --filter "until=24h"

    # Remove dangling images
    log_info "Pruning dangling images..."
    docker image prune -f

    # Remove stopped containers
    log_info "Pruning stopped containers..."
    docker container prune -f

    # Prune unused networks
    log_info "Pruning unused networks..."
    docker network prune -f
}

# --- Main ---
USAGE=$(get_disk_usage)
log_info "Current disk usage: ${USAGE}% (Threshold: ${DISK_WARN_PCT}%)"

if [ "$USAGE" -lt "$DISK_WARN_PCT" ]; then
    log_info "Disk usage is within safe limits."
    # We still run a minimal backup cleanup to keep things tidy if called during update
    cleanup_backups
    exit 0
fi

log_warn "Disk usage is high: ${USAGE}%"

# 1. Cleanup backups first
cleanup_backups

# Re-check
USAGE=$(get_disk_usage)
if [ "$USAGE" -ge "$DISK_CLEAN_PCT" ]; then
    log_warn "Disk usage still high (${USAGE}%). Proceeding with Docker cleanup..."
    cleanup_docker
fi

# Final check
USAGE=$(get_disk_usage)
log_info "Final disk usage: ${USAGE}%"

if [ "$USAGE" -ge "$DISK_CLEAN_PCT" ]; then
    log_error "Disk usage is STILL above ${DISK_CLEAN_PCT}% after safe cleanup."
    log_error "Manual intervention may be required."
    log_info "Docker storage summary:"
    docker system df -v
fi
