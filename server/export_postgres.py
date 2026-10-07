"""Export a consistent SQLite backup into PostgreSQL text COPY files.

This prepares data for migration; the running server still uses SQLite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from shared.sqlite import open_readonly


# Keep these in SQLite table order. The PostgreSQL schema has the same columns.
TABLE_COLUMNS = {
    "hosts": ("host_id", "last_heartbeat_utc"),
    "terminals": ("host_id", "terminal_id", "data_path", "last_seen_utc"),
    "log_events": ("event_id", "host_id", "terminal_id", "stream", "file_name",
                   "generation", "byte_offset", "raw_line", "received_utc"),
    "collector_heartbeats": ("heartbeat_id", "host_id", "received_utc",
                             "observed_at_utc", "pending_count", "payload_json"),
    "alerts": ("host_id", "alert_type", "target", "state", "detail", "changed_utc",
               "last_attempt_utc", "last_sent_utc", "active_notified", "last_error"),
    "terminal_status": ("host_id", "terminal_id", "data_path", "observed_at_utc",
                        "received_utc", "status_json"),
    "accounts": ("server", "login", "currency", "day_timezone", "host_id",
                 "terminal_id", "latest_snapshot_utc", "history_start_day", "balance"),
    "positions": ("server", "login", "ticket", "symbol", "type", "magic",
                  "strategy", "volume", "price_open", "price_current", "profit",
                  "swap", "comment"),
    "deals": ("server", "login", "ticket", "time_utc", "day_local", "type",
              "kind", "entry", "position_id", "symbol", "magic", "strategy",
              "comment", "profit", "commission", "swap", "fee", "volume"),
    "backtest_runs": ("run_id", "strategy", "file_name", "file_hash", "currency",
                      "starting_capital", "first_day", "last_day", "row_count", "imported_utc"),
    "backtest_days": ("run_id", "day", "net_pnl"),
    "close_alert_baselines": ("server", "login", "initialized_utc"),
    "close_notifications": ("server", "login", "ticket", "state", "created_utc",
                            "last_attempt_utc", "sent_utc", "last_error"),
}


def copy_field(value: object) -> str:
    if value is None:
        return r"\N"
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"unsupported SQLite value type: {type(value).__name__}")
    if "\x00" in str(value):
        raise ValueError("PostgreSQL text cannot store a NUL byte")
    return (str(value).replace("\\", r"\\").replace("\t", r"\t")
            .replace("\n", r"\n").replace("\r", r"\r"))


def _check_schema(connection: sqlite3.Connection) -> None:
    for table, expected in TABLE_COLUMNS.items():
        actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if actual != expected:
            raise ValueError(f"{table} columns differ from the migration schema: {actual}")


def export_backup(source: Path, output: Path) -> dict:
    """Read one SQLite snapshot and publish a new export directory on success."""
    if not source.is_file():
        raise ValueError(f"database does not exist: {source}")
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".pg-export-", dir=output.parent))
    try:
        with closing(open_readonly(source)) as connection:
            connection.execute("BEGIN")
            _check_schema(connection)
            tables = {}
            for table, columns in TABLE_COLUMNS.items():
                path = scratch / f"{table}.copy"
                count = 0
                digest = hashlib.sha256()
                with path.open("wb") as target:
                    for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
                        line = ("\t".join(copy_field(value) for value in row) + "\n").encode("utf-8")
                        target.write(line)
                        digest.update(line)
                        count += 1
                tables[table] = {"rows": count, "sha256": digest.hexdigest()}
            connection.execute("ROLLBACK")
        schema = Path(__file__).resolve().parent.parent / "docs" / "postgres-schema.sql"
        shutil.copyfile(schema, scratch / "schema.sql")
        load = [r"\set ON_ERROR_STOP on", "BEGIN;", "SET client_encoding = 'UTF8';"]
        for table, columns in TABLE_COLUMNS.items():
            load.append(f"\\copy {table} ({', '.join(columns)}) FROM '{table}.copy' WITH (FORMAT text)")
        for table, item in tables.items():
            load.append(
                f"DO $$ BEGIN IF (SELECT COUNT(*) FROM {table}) <> {item['rows']} "
                f"THEN RAISE EXCEPTION 'row count mismatch for {table}'; END IF; END $$;"
            )
        load.append("SELECT setval(pg_get_serial_sequence('collector_heartbeats', 'heartbeat_id'), "
                    "COALESCE(MAX(heartbeat_id), 1), MAX(heartbeat_id) IS NOT NULL) "
                    "FROM collector_heartbeats;")
        load.append("COMMIT;")
        (scratch / "load.psql").write_text("\n".join(load) + "\n", encoding="utf-8")
        manifest = {
            "format": "postgres-text-copy-v1",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(source.resolve()),
            "tables": tables,
        }
        (scratch / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        scratch.rename(output)
        return manifest
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a central SQLite backup for PostgreSQL")
    parser.add_argument("--backup", type=Path, required=True,
                        help="A verified SQLite backup, not the live central.db")
    parser.add_argument("--output", type=Path, required=True,
                        help="New directory for schema, COPY files, and manifest")
    args = parser.parse_args()
    try:
        manifest = export_backup(args.backup, args.output)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output),
                      "rows": {name: item["rows"] for name, item in manifest["tables"].items()}}))


if __name__ == "__main__":
    main()
