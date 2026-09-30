from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.audit import build_report
from server.backup import create_backup
from server.ingest import open_database


class ReleaseToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db_path = self.root / "central.db"
        self.connection = open_database(self.db_path)
        self.addCleanup(self.connection.close)
        self.now = datetime.now(timezone.utc)
        self.path = r"C:\Users\Trader\MetaQuotes\Terminal\ONE"

    def seed(self, *, missing=False, pending=0, broker=True) -> None:
        payload = {
            "coverage_configured": True, "expected": [self.path], "archived": [],
            "missing": [self.path] if missing else [], "unknown": [],
            "pending_count": pending,
            "terminals": [] if missing else [{
                "terminal_id": "one", "data_path": self.path, "process": {"state": "running"},
            }],
        }
        with self.connection:
            self.connection.execute("INSERT INTO hosts VALUES (?,?)", ("vps", self.now.isoformat()))
            self.connection.execute("INSERT INTO terminals VALUES (?,?,?,?)",
                                    ("vps", "one", self.path, self.now.isoformat()))
            self.connection.execute(
                "INSERT INTO collector_heartbeats (host_id,received_utc,observed_at_utc,pending_count,payload_json) "
                "VALUES (?,?,?,?,?)",
                ("vps", self.now.isoformat(), self.now.isoformat(), pending, json.dumps(payload)),
            )
            self.connection.execute(
                "INSERT INTO terminal_status VALUES (?,?,?,?,?,?)",
                ("vps", "one", self.path, self.now.isoformat(), self.now.isoformat(),
                 json.dumps({"state": "ok", "connected": broker,
                             "autotrading": True, "data_complete": True})),
            )
            self.connection.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)",
                ("Broker", 123, "USD", "UTC", "vps", "one", self.now.isoformat(),
                 self.now.date().isoformat()),
            )
            for stream in ("journal", "experts"):
                self.connection.execute(
                    "INSERT INTO log_events VALUES (?,?,?,?,?,?,?,?,?)",
                    (stream, "vps", "one", stream, "20260930.log", 0, 0, "line", self.now.isoformat()),
                )

    def test_audit_reports_missing_inventory_and_pending_upload(self) -> None:
        self.seed(missing=True, pending=2)
        report = build_report(self.connection, self.now + timedelta(seconds=1))
        self.assertEqual(report["hosts"][0]["missing"], [self.path.casefold()])
        self.assertTrue(any("pending upload" in item for item in report["findings"]))
        self.assertTrue(any("folder missing" in item for item in report["findings"]))
        self.assertIn("real MT5 midnight rollover", report["live_checks_still_required"])

    def test_audit_has_no_database_findings_for_current_complete_state(self) -> None:
        self.seed()
        report = build_report(self.connection, self.now + timedelta(seconds=1))
        self.assertEqual(report["findings"], [])
        terminal = report["hosts"][0]["terminals"][0]
        self.assertEqual((terminal["journal_events"], terminal["experts_events"]), (1, 1))
        self.assertTrue(report["accounts"][0]["snapshot_fresh"])

    def test_audit_catches_broker_disconnect_and_stale_account(self) -> None:
        self.seed(broker=False)
        report = build_report(self.connection, self.now + timedelta(seconds=1))
        self.assertTrue(any("broker disconnected" in item for item in report["findings"]))
        with self.connection:
            self.connection.execute("UPDATE terminal_status SET status_json=?",
                                    (json.dumps({"state": "unknown"}),))
        report = build_report(self.connection, self.now + timedelta(seconds=1))
        self.assertFalse(report["accounts"][0]["snapshot_fresh"])

    def test_online_backup_is_consistent_and_independent(self) -> None:
        self.seed()
        backup = create_backup(self.db_path, self.root / "backups", self.now)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(backup)) as copied:
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM log_events").fetchone()[0], 2)
        with self.connection:
            self.connection.execute("DELETE FROM log_events")
        with closing(sqlite3.connect(backup)) as copied:
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM log_events").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
