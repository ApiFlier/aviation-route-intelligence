#!/usr/bin/env bash
#
# FlightConn Maintenance: Merge duplicate SWIM observations
#
# This script is a wrapper around the swim-ingestor CLI command.
# It handles the docker compose orchestration and safety defaults.
#

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_OPTS="--profile swim"

# Default to dry-run
MODE="--dry-run"
if [[ "$1" == "--apply" ]]; then
    MODE="--apply"
fi

echo "===================================================="
echo "  FlightConn SWIM Duplicate Cleanup"
echo "===================================================="
echo "  Mode: $MODE"
echo ""

if [[ "$MODE" == "--apply" ]]; then
    echo "  WARNING: Destructive mode selected."
    echo "  This will permanently merge and delete rows in the database."
    echo "  Ensure you have a current backup (./backup.sh) before proceeding."
    echo ""
    printf "  Proceed with --apply? [y/N] "
    read -r CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "  Aborted."
        exit 0
    fi
fi

cd "$REPO_DIR"

# Ensure the container image is built
docker compose $COMPOSE_OPTS build swim-ingestor >/dev/null

# Run the cleanup command
docker compose $COMPOSE_OPTS run --rm --entrypoint python swim-ingestor main.py cleanup-duplicates $MODE
