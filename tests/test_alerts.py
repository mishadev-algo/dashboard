from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.alerts import evaluate
from server.ingest import open_database
from server.web import render_dashboard


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
        failed_at = self.now + timedelta(seconds=31)
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
        offline_at = missing_at + timedelta(seconds=31)
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

        failed_at = self.now + timedelta(seconds=31)
        evaluate(self.connection, send, failed_at)
        evaluate(self.connection, send, failed_at + timedelta(seconds=10))
        self.assertEqual(len(attempts), 1)
        evaluate(self.connection, send, failed_at + timedelta(seconds=61))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            self.connection.execute("SELECT active_notified FROM alerts WHERE alert_type='collector_offline'").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
