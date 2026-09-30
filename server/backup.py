"""Consistent, verified online backup of the central SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def create_backup(source: Path, directory: Path, now: datetime | None = None) -> Path:
    if not source.is_file():
        raise ValueError(f"database does not exist: {source}")
    directory.mkdir(parents=True, exist_ok=True)
    if source.resolve().parent == directory.resolve() and source.name.startswith("central-backup-"):
        raise ValueError("source appears to be a backup; choose the live central database")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    name = f"central-backup-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    destination = directory / name
    temporary = directory / f".{name}.partial"
    source_connection = None
    target_connection = None
    try:
        source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
        target_connection = sqlite3.connect(temporary, timeout=30)
        source_connection.backup(target_connection, pages=1000, sleep=0.1)
        result = target_connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError(f"backup integrity check failed: {result}")
        target_connection.close()
        target_connection = None
        source_connection.close()
        source_connection = None
        temporary.replace(destination)
        return destination
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify an online central SQLite backup")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = create_backup(args.db, args.directory)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(result)


if __name__ == "__main__":
    main()
