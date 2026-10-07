from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.audit import build_report
from server.backup import create_backup
from server.export_postgres import export_backup
from server.ingest import open_database
from server.restore_check import check_restore


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
                "INSERT INTO accounts (server,login,currency,day_timezone,host_id,terminal_id,"
                "latest_snapshot_utc,history_start_day) VALUES (?,?,?,?,?,?,?,?)",
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

    def test_audit_names_account_mismatch(self) -> None:
        self.seed()
        with self.connection:
            self.connection.execute("UPDATE terminal_status SET status_json=?",
                                    (json.dumps({"state": "account_mismatch"}),))
        report = build_report(self.connection, self.now + timedelta(seconds=1))
        self.assertTrue(any("MT5 account does not match configured login/server" in item
                            for item in report["findings"]))
        self.assertFalse(any("probe unavailable" in item for item in report["findings"]))

    def test_online_backup_is_consistent_and_independent(self) -> None:
        self.seed()
        backup = create_backup(self.db_path, self.root / "backups", self.now)
        self.assertTrue(backup.is_file())
        with closing(sqlite3.connect(backup)) as copied:
            self.assertEqual(copied.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM log_events").fetchone()[0], 2)
        restored = check_restore(backup, self.root / "restore-scratch")
        self.assertEqual(restored["restore_integrity"], "ok")
        self.assertEqual(restored["counts"]["log_events"], 2)
        self.assertFalse(list((self.root / "restore-scratch").iterdir()))
        with self.connection:
            self.connection.execute("DELETE FROM log_events")
        with closing(sqlite3.connect(backup)) as copied:
            self.assertEqual(copied.execute("SELECT COUNT(*) FROM log_events").fetchone()[0], 2)

    def test_restore_check_rejects_non_database_and_cleans_scratch(self) -> None:
        invalid = self.root / "invalid.sqlite3"
        invalid.write_text("not a SQLite database", encoding="utf-8")
        scratch = self.root / "restore-scratch"
        with self.assertRaises(sqlite3.DatabaseError):
            check_restore(invalid, scratch)
        self.assertFalse(list(scratch.iterdir()))

    def test_postgres_export_keeps_all_rows_and_copy_escapes(self) -> None:
        self.seed()
        raw_line = "path\\segment\tvalue\nnext\rline"
        with self.connection:
            self.connection.execute("UPDATE log_events SET raw_line=? WHERE event_id='journal'", (raw_line,))
            self.connection.execute(
                "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("run-one", "strategy", "test.csv", "hash-one", "USD", "1000",
                 "2026-09-30", "2026-09-30", 1, self.now.isoformat()),
            )
            self.connection.execute(
                "INSERT INTO backtest_days VALUES (?,?,?)", ("run-one", "2026-09-30", "12.5"),
            )
            self.connection.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("vps", "offline", "one", "active", "detail", self.now.isoformat(),
                 None, None, 0, None),
            )
        backup = create_backup(self.db_path, self.root / "backups", self.now)
        output = self.root / "postgres-export"
        manifest = export_backup(backup, output)
        self.assertEqual(manifest["tables"]["log_events"]["rows"], 2)
        self.assertEqual(manifest["tables"]["alerts"]["rows"], 1)
        self.assertEqual(manifest["tables"]["backtest_runs"]["rows"], 1)
        self.assertEqual(manifest["tables"]["backtest_days"]["rows"], 1)
        self.assertEqual(len(manifest["tables"]), 13)
        log_bytes = (output / "log_events.copy").read_bytes()
        self.assertIn(b"path\\\\segment\\tvalue\\nnext\\rline", log_bytes)
        self.assertIn(b"\t\\N\t\\N\t0\t\\N\n", (output / "alerts.copy").read_bytes())
        self.assertEqual(manifest["tables"]["log_events"]["sha256"],
                         hashlib.sha256(log_bytes).hexdigest())
        self.assertIn("\\copy log_events", (output / "load.psql").read_text(encoding="utf-8"))
        self.assertIn("\\copy backtest_days", (output / "load.psql").read_text(encoding="utf-8"))
        self.assertIn("row count mismatch for log_events", (output / "load.psql").read_text(encoding="utf-8"))
        self.assertIn("CREATE TABLE log_events", (output / "schema.sql").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "output already exists"):
            export_backup(backup, output)

    def test_postgres_export_rejects_schema_drift_without_publishing(self) -> None:
        with self.connection:
            self.connection.execute("DROP TABLE alerts")
        output = self.root / "postgres-export"
        with self.assertRaisesRegex(ValueError, "alerts columns differ"):
            export_backup(self.db_path, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
