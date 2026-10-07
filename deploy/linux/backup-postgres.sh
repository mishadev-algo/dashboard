#!/usr/bin/env bash
set -euo pipefail

umask 077
backup_dir=/var/backups/mt5-dashboard
stamp=$(date -u +%Y%m%dT%H%M%SZ)
temporary="$backup_dir/dashboard-$stamp.dump.tmp"
final="$backup_dir/dashboard-$stamp.dump"

trap 'rm -f -- "$temporary"' EXIT
pg_dump --format=custom --file="$temporary" dashboard
pg_restore --list "$temporary" >/dev/null
mv -- "$temporary" "$final"
echo "Verified PostgreSQL backup: $final"
