from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from collector.core import Collector, open_database
from collector.remote import RemoteUploadError, RemoteUploader, heartbeat, pending_count
from server.ingest import IngestHandler, ingest, open_database as open_server_database


class RemoteDeliveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Terminal"
        self.folder = self.root / "FIRST"
        (self.folder / "Logs").mkdir(parents=True)
        (self.folder / "MQL5" / "Logs").mkdir(parents=True)
        self.today = date.today().strftime("%Y%m%d") + ".log"
        self.local = open_database(self.base / "local.db")
        self.addCleanup(self.local.close)
        self.collector = Collector(self.local, "test-host", [self.root])
        self.central_path = self.base / "central.db"
        self.central = open_server_database(self.central_path)
        self.addCleanup(self.central.close)

    def fake_urlopen(self, request, timeout=10):
        if request.get_header("Authorization") != "Bearer test-secret":
            raise HTTPError(request.full_url, 401, "unauthorized", {}, None)
        payload = json.loads(request.data)
        result = ingest(self.central, payload, "test-host")
        return io.BytesIO(json.dumps(result).encode("utf-8"))

    def central_count(self, table: str) -> int:
        return self.central.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_outage_replay_and_lost_ack_are_idempotent(self) -> None:
        (self.folder / "Logs" / self.today).write_text("first journal\n", encoding="utf-8")
        (self.folder / "MQL5" / "Logs" / self.today).write_text("first experts\n", encoding="utf-8")
        first = self.collector.run_once()
        self.assertEqual(first.events, 2)
        self.assertEqual(pending_count(self.local), 2)

        offline = RemoteUploader(self.local, "http://127.0.0.1:8765", "test-host", "test-secret", timeout=0.5)
        with patch("collector.remote.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(RemoteUploadError):
                offline.upload(heartbeat(self.local, "test-host", first))
        self.assertEqual(pending_count(self.local), 2)

        online = RemoteUploader(self.local, "http://127.0.0.1:8765", "test-host", "test-secret")
        with patch("collector.remote.urlopen", side_effect=self.fake_urlopen):
            self.assertEqual(online.upload(heartbeat(self.local, "test-host", first)), 2)
            self.assertEqual(pending_count(self.local), 0)
            self.assertEqual(self.central_count("log_events"), 2)

            # The server committed, but the collector did not retain its acknowledgement.
            with self.local:
                self.local.execute("UPDATE log_events SET delivered_utc=NULL")
            self.assertEqual(online.upload(heartbeat(self.local, "test-host", first)), 2)
            self.assertEqual(self.central_count("log_events"), 2)
            self.assertEqual(pending_count(self.local), 0)

            with (self.folder / "Logs" / self.today).open("a", encoding="utf-8") as source:
                source.write("second journal\n")
            second = self.collector.run_once()
            self.assertEqual(second.events, 1)
            self.assertEqual(online.upload(heartbeat(self.local, "test-host", second)), 1)
            self.assertEqual(online.upload(heartbeat(self.local, "test-host", second)), 0)
        self.assertEqual(self.central_count("log_events"), 3)
        self.assertGreaterEqual(self.central_count("collector_heartbeats"), 3)

    def test_wrong_token_does_not_acknowledge(self) -> None:
        (self.folder / "Logs" / self.today).write_text("private line\n", encoding="utf-8")
        result = self.collector.run_once()
        uploader = RemoteUploader(self.local, "http://127.0.0.1:8765", "test-host", "wrong-token")
        with patch("collector.remote.urlopen", side_effect=self.fake_urlopen):
            with self.assertRaises(RemoteUploadError):
                uploader.upload(heartbeat(self.local, "test-host", result))
        self.assertEqual(pending_count(self.local), 1)
        self.assertEqual(self.central_count("log_events"), 0)

    def test_http_handler_authenticates_heartbeat(self) -> None:
        result = self.collector.run_once()
        body = json.dumps({
            "host_id": "test-host", "events": [],
            "heartbeat": heartbeat(self.local, "test-host", result),
        }).encode("utf-8")
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=self.central_path, host_tokens={"test-host": "test-secret"})
        handler.path = "/v1/ingest"
        handler.rfile = io.BytesIO(body)
        replies = []
        handler._respond = lambda status, value: replies.append((status, value))
        handler.headers = {"Authorization": "Bearer wrong-token", "Content-Length": str(len(body))}
        handler.do_POST()
        self.assertEqual(replies[-1][0], 401)
        self.assertEqual(self.central_count("collector_heartbeats"), 0)
        handler.headers["Authorization"] = "Bearer test-secret"
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        self.assertEqual(replies[-1][0], 200)
        self.assertEqual(self.central_count("collector_heartbeats"), 1)

    def test_existing_collector_database_gets_delivery_column(self) -> None:
        old_path = self.base / "old.db"
        old = sqlite3.connect(old_path)
        old.execute("CREATE TABLE log_events (terminal_id TEXT, host_id TEXT, stream TEXT, file_name TEXT, generation INTEGER, byte_offset INTEGER, raw_line TEXT, received_utc TEXT)")
        old.commit()
        old.close()
        migrated = open_database(old_path)
        try:
            columns = {row[1] for row in migrated.execute("PRAGMA table_info(log_events)")}
            self.assertIn("delivered_utc", columns)
        finally:
            migrated.close()

    def test_previous_day_and_current_day_files_both_upload(self) -> None:
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d") + ".log"
        (self.folder / "Logs" / yesterday).write_text("before midnight\n", encoding="utf-8")
        first = self.collector.run_once()
        self.assertEqual(first.events, 1)
        (self.folder / "Logs" / self.today).write_text("after midnight\n", encoding="utf-8")
        second = self.collector.run_once()
        self.assertEqual(second.events, 1)
        uploader = RemoteUploader(self.local, "http://127.0.0.1:8765", "test-host", "test-secret")
        with patch("collector.remote.urlopen", side_effect=self.fake_urlopen):
            self.assertEqual(uploader.upload(heartbeat(self.local, "test-host", second)), 2)
        self.assertEqual(self.collector.run_once().events, 0)
        self.assertEqual(self.central_count("log_events"), 2)


if __name__ == "__main__":
    unittest.main()
