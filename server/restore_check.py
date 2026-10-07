"""Exercise a SQLite backup restore without touching the live database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from shared.sqlite import open_readonly

from .audit import build_report


def check_restore(backup: Path, directory: Path | None = None) -> dict:
    """Restore a backup to a temporary file and query it through the dashboard audit."""
    if not backup.is_file():
        raise ValueError(f"backup does not exist: {backup}")
    workdir = directory or backup.parent
    workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dashboard-restore-check-", dir=workdir) as temporary:
        restored = Path(temporary) / "central-restored.sqlite3"
        with closing(open_readonly(backup)) as source:
            with closing(sqlite3.connect(restored, timeout=30)) as target:
                source.backup(target, pages=1000, sleep=0.1)
        with closing(open_readonly(restored)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result != ("ok",):
                raise RuntimeError(f"restored database integrity check failed: {result}")
            report = build_report(connection)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("hosts", "terminals", "log_events", "accounts", "positions", "deals")
            }
    return {"backup": str(backup), "restore_integrity": "ok", "counts": counts,
            "audit_findings_at_restore_time": report["findings"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Test-restore a central SQLite backup to a temporary database")
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--directory", type=Path, help="Scratch directory for the temporary restore")
    args = parser.parse_args()
    try:
        result = check_restore(args.backup, args.directory)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
