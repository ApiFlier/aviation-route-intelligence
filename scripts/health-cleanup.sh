#!/usr/bin/env bash
#
# scripts/health-cleanup.sh
# Safely prunes disposable Docker artifacts to free disk space.
#
set -euo pipefail

# --- Configuration / Thresholds ---
DISK_WARN_PCT=${DISK_WARN_PCT:-85}
DISK_CLEAN_PCT=${DISK_CLEAN_PCT:-90}
DISK_TARGET_PCT=${DISK_TARGET_PCT:-85}

# --- Helpers ---
log_info()  { echo -e "[INFO]  $*"; }
log_warn()  { echo -e "[WARN]  $*"; }
log_error() { echo -e "[ERROR] $*" >&2; }

get_disk_usage() {
    # Returns the % usage of the filesystem containing /
    df -P / | awk 'NR==2 {print $5}' | sed 's/%//'
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
    exit 0
fi

log_warn "Disk usage is high: ${USAGE}%"

# Proceed with Docker cleanup
cleanup_docker

# Final check
USAGE=$(get_disk_usage)
log_info "Final disk usage: ${USAGE}%"

if [ "$USAGE" -ge "$DISK_CLEAN_PCT" ]; then
    log_error "Disk usage is STILL above ${DISK_CLEAN_PCT}% after safe cleanup."
    log_error "Manual intervention may be required."
    log_info "Docker storage summary:"
    docker system df -v
fi
