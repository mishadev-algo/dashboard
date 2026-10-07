from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from shared.cadence import STALE_AFTER_SECONDS
from server.alerts import evaluate
from server.ingest import IngestHandler, open_database
from server.web import render_dashboard
from server.uptime_monitor import step


class AlertTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = open_database(Path(self.temp.name) / "central.db")
        self.addCleanup(self.connection.close)
        self.now = datetime.now(timezone.utc)
        self.path = r"C:\MetaQuotes\Terminal\FIRST"
        self.messages: list[str] = []
        self.heartbeat(self.now)

    def heartbeat(self, when: datetime, *, missing: bool = False, process: str = "running") -> None:
        state = {
            "coverage_configured": True, "expected": [self.path],
            "missing": [self.path.casefold()] if missing else [],
            "terminals": [] if missing else [{"data_path": self.path, "process": {"state": process}}],
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO hosts VALUES (?,?) ON CONFLICT(host_id) DO UPDATE SET last_heartbeat_utc=excluded.last_heartbeat_utc",
                ("vps", when.isoformat()),
            )
            self.connection.execute(
                "INSERT INTO collector_heartbeats (host_id,received_utc,observed_at_utc,pending_count,payload_json) "
                "VALUES (?,?,?,?,?)",
                ("vps", when.isoformat(), when.isoformat(), 0, json.dumps(state)),
            )

    def test_offline_alert_recovery_and_cooldown(self) -> None:
        evaluate(self.connection, self.messages.append, self.now)
        self.assertEqual(self.messages, [])
        evaluate(self.connection, self.messages.append,
                 self.now + timedelta(seconds=STALE_AFTER_SECONDS))
        self.assertEqual(self.messages, [])
        failed_at = self.now + timedelta(seconds=STALE_AFTER_SECONDS + 1)
        evaluate(self.connection, self.messages.append, failed_at)
        evaluate(self.connection, self.messages.append, failed_at + timedelta(seconds=10))
        self.assertEqual(len(self.messages), 1)
        self.assertIn("stopped reporting", self.messages[0])
        self.assertIn("active", render_dashboard(self.connection, failed_at))
        evaluate(self.connection, self.messages.append, failed_at + timedelta(minutes=30))
        self.assertEqual(len(self.messages), 2)
        recovered_at = failed_at + timedelta(minutes=31)
        self.heartbeat(recovered_at)
        evaluate(self.connection, self.messages.append, recovered_at)
        evaluate(self.connection, self.messages.append, recovered_at + timedelta(seconds=1))
        self.assertEqual(len(self.messages), 3)
        self.assertTrue(self.messages[-1].startswith("Recovered:"))
        state = self.connection.execute("SELECT state FROM alerts WHERE alert_type='collector_offline'").fetchone()[0]
        self.assertEqual(state, "resolved")

    def test_missing_and_stopped_are_distinct_and_offline_holds_them(self) -> None:
        missing_at = self.now + timedelta(seconds=1)
        self.heartbeat(missing_at, missing=True)
        evaluate(self.connection, self.messages.append, missing_at)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("folder missing", self.messages[0])
        page = render_dashboard(self.connection, missing_at)
        self.assertIn("Expected terminal missing: Account unavailable", page)
        self.assertNotIn(self.path, page)
        offline_at = missing_at + timedelta(seconds=STALE_AFTER_SECONDS + 1)
        evaluate(self.connection, self.messages.append, offline_at)
        self.assertEqual(len(self.messages), 2)
        self.assertNotIn("Recovered:", self.messages[-1])
        running_at = offline_at + timedelta(seconds=1)
        self.heartbeat(running_at, process="stopped")
        evaluate(self.connection, self.messages.append, running_at)
        self.assertEqual(len(self.messages), 5)
        self.assertTrue(any(message.startswith("Recovered:") for message in self.messages))
        self.assertTrue(any("process stopped" in message for message in self.messages))

    def test_failed_send_waits_before_retry(self) -> None:
        attempts: list[str] = []

        def send(message: str) -> None:
            attempts.append(message)
            if len(attempts) == 1:
                raise RuntimeError("test failure")

        failed_at = self.now + timedelta(seconds=STALE_AFTER_SECONDS + 1)
        evaluate(self.connection, send, failed_at)
        evaluate(self.connection, send, failed_at + timedelta(seconds=10))
        self.assertEqual(len(attempts), 1)
        evaluate(self.connection, send, failed_at + timedelta(seconds=61))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            self.connection.execute("SELECT active_notified FROM alerts WHERE alert_type='collector_offline'").fetchone()[0], 1
        )

    def test_stopped_account_probe_fills_unknown_process_state(self) -> None:
        later = self.now + timedelta(seconds=1)
        state = {"coverage_configured": True, "expected": [self.path], "missing": [],
                 "terminals": [{"terminal_id": "one", "data_path": self.path,
                                "process": {"state": "unknown"}}]}
        with self.connection:
            self.connection.execute("UPDATE hosts SET last_heartbeat_utc=? WHERE host_id='vps'", (later.isoformat(),))
            self.connection.execute(
                "INSERT INTO collector_heartbeats (host_id,received_utc,observed_at_utc,pending_count,payload_json) "
                "VALUES (?,?,?,?,?)", ("vps", later.isoformat(), later.isoformat(), 0, json.dumps(state)))
            self.connection.execute(
                "INSERT INTO terminal_status VALUES (?,?,?,?,?,?)",
                ("vps", "one", self.path, later.isoformat(), later.isoformat(), json.dumps({"state": "stopped"})))
        evaluate(self.connection, self.messages.append, later)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("process stopped", self.messages[0])

    def test_archiving_stopped_terminal_resolves_without_false_recovery(self) -> None:
        stopped_at = self.now + timedelta(seconds=1)
        self.heartbeat(stopped_at, process="stopped")
        evaluate(self.connection, self.messages.append, stopped_at)
        self.assertEqual(len(self.messages), 1)
        archived_at = stopped_at + timedelta(seconds=1)
        state = {"coverage_configured": True, "expected": [], "archived": [self.path],
                 "missing": [], "terminals": [{"data_path": self.path,
                                                  "process": {"state": "stopped"}}]}
        with self.connection:
            self.connection.execute("UPDATE hosts SET last_heartbeat_utc=? WHERE host_id='vps'",
                                    (archived_at.isoformat(),))
            self.connection.execute(
                "INSERT INTO collector_heartbeats (host_id,received_utc,observed_at_utc,pending_count,payload_json) "
                "VALUES (?,?,?,?,?)", ("vps", archived_at.isoformat(), archived_at.isoformat(), 0,
                                         json.dumps(state)))
        evaluate(self.connection, self.messages.append, archived_at)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.connection.execute(
            "SELECT state FROM alerts WHERE alert_type='terminal_stopped'").fetchone()[0], "resolved")

    def test_independent_monitor_alerts_during_outage_and_recovers(self) -> None:
        path = Path(self.temp.name) / "monitor.json"
        url = "https://dashboard.example.com/health"
        step(url, path, self.messages.append, healthy=True)
        step(url, path, self.messages.append, healthy=False)
        self.assertEqual(self.messages, [])
        step(url, path, self.messages.append, healthy=False)
        self.assertIn("unavailable", self.messages[0])
        step(url, path, self.messages.append, healthy=False)
        self.assertEqual(len(self.messages), 1)
        step(url, path, self.messages.append, healthy=True)
        self.assertEqual(len(self.messages), 2)
        self.assertTrue(self.messages[-1].startswith("Recovered:"))

    def test_health_route_checks_the_database(self) -> None:
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=Path(self.temp.name) / "central.db")
        handler.path = "/health"
        replies = []
        handler._respond = lambda status, body: replies.append((status, body))
        handler.do_GET()
        self.assertEqual(replies[-1], (200, {"status": "ok"}))
        handler.server.db_path = Path(self.temp.name) / "missing.db"
        handler.do_GET()
        self.assertEqual(replies[-1], (503, {"status": "unavailable"}))

    def test_independent_monitor_retries_failed_telegram_delivery(self) -> None:
        path = Path(self.temp.name) / "monitor-retry.json"
        url = "https://dashboard.example.com/health"
        attempts = []

        def send(message):
            attempts.append(message)
            if len(attempts) == 1:
                raise RuntimeError("temporary failure")

        step(url, path, send, healthy=False)
        with self.assertRaises(RuntimeError):
            step(url, path, send, healthy=False)
        self.assertEqual(len(attempts), 1)
        step(url, path, send, healthy=False)
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
