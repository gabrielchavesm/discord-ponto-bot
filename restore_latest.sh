#!/bin/bash
set -e

BACKUP_DIR=~/discord-ponto-bot/backups

# First look for compressed backups (.sql.gz), then for uncompressed (.sql)
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | head -n1)
if [ -z "$LATEST_BACKUP" ]; then
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/backup_*.sql 2>/dev/null | head -n1)
fi

if [ -z "$LATEST_BACKUP" ]; then
    echo "ERROR: No backup found in $BACKUP_DIR"
    exit 1
fi

echo "Latest backup: $LATEST_BACKUP"

# If it is compressed, decompress temporarily
if [[ "$LATEST_BACKUP" == *.gz ]]; then
    gunzip -c "$LATEST_BACKUP" > /tmp/restore_temp.sql
    RESTORE_FILE=/tmp/restore_temp.sql
else
    RESTORE_FILE="$LATEST_BACKUP"
fi

cd ~/discord-ponto-bot

echo "Stopping bot (prevents writes during restore)..."
docker compose stop discord-bot

echo "Deleting and recreating the database..."
# Use -i (not -it) for non-interactive execution
docker exec -i discord-ponto-db psql -U postgres -c "DROP DATABASE IF EXISTS ponto;"
docker exec -i discord-ponto-db psql -U postgres -c "CREATE DATABASE ponto;"

echo "Restoring backup..."
cat "$RESTORE_FILE" | docker exec -i discord-ponto-db psql -U postgres -d ponto

# Remove temporary file if created
if [[ "$LATEST_BACKUP" == *.gz ]]; then
    rm /tmp/restore_temp.sql
fi

echo "Starting the bot..."
docker compose start discord-bot

echo "Restoration successfully completed!"