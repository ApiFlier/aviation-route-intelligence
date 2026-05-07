#!/usr/bin/env bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env file not found. Run ./setup.sh first." >&2
    exit 1
fi

# Source .env
set -a
source "$ENV_FILE"
set +a

BACKUP_DIR="$REPO_DIR/backups"
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/flightconn_$TIMESTAMP.sql.gz"

echo "==> Backing up FlightConn database..."
if ! docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysqldump -uroot flightconn | gzip > "$BACKUP_FILE"; then
    echo "ERROR: Backup failed." >&2
    exit 1
fi

echo "    Backup created: $BACKUP_FILE"
