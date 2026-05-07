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

BACKUP_FILE=$1

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: ./restore.sh <backup_file.sql[.gz]>"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "  WARNING: This will overwrite the current database data!"
echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "  Type 'RESTORE FLIGHTCONN' to confirm:"
read -p "> " CONFIRM

if [ "$CONFIRM" != "RESTORE FLIGHTCONN" ]; then
    echo "Aborting."
    exit 1
fi

# Create a pre-restore backup
PRE_RESTORE_DIR="$REPO_DIR/backups/pre_restore"
mkdir -p "$PRE_RESTORE_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PRE_RESTORE_FILE="$PRE_RESTORE_DIR/flightconn_before_restore_$TIMESTAMP.sql.gz"

echo "==> Creating pre-restore backup..."
docker exec -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db \
    mysqldump -uroot flightconn | gzip > "$PRE_RESTORE_FILE"
echo "    Pre-restore backup: $PRE_RESTORE_FILE"

echo "==> Restoring database from $BACKUP_FILE..."
if [[ "$BACKUP_FILE" == *.gz ]]; then
    gunzip -c "$BACKUP_FILE" | docker exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysql -uroot flightconn
else
    cat "$BACKUP_FILE" | docker exec -i -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" flightconn-db mysql -uroot flightconn
fi

echo "    Restore complete."
