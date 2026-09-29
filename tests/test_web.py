from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collector.protocol import event_id
from server.ingest import ingest, open_database
from server.web import render_dashboard, render_logs


class WebViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = open_database(Path(self.temp.name) / "central.db")
        self.addCleanup(self.connection.close)
        path = r"C:\MetaQuotes\Terminal\FIRST"
        events = []
        for stream, line in (("journal", "normal journal"), ("experts", "error <script>alert(1)</script>")):
            event = {
                "host_id": "host-one", "terminal_id": "terminal-one", "data_path": path,
                "stream": stream, "file_name": "20260929.log", "generation": 0,
                "byte_offset": 0, "raw_line": line,
            }
            event["event_id"] = event_id(event)
            events.append(event)
        ingest(self.connection, {
            "host_id": "host-one", "events": events,
            "heartbeat": {
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "terminals": [{
                    "terminal_id": "terminal-one", "data_path": path, "streams": {},
                    "process": {"state": "running", "pid": 421},
                }],
                "missing": [], "unknown": [], "coverage_configured": False,
                "pending_count": 2,
            },
        }, "host-one")

    def test_status_uses_explicit_process_state_and_escapes_data(self) -> None:
        page = render_dashboard(self.connection)
        self.assertIn("Collector online", page)
        self.assertIn("Folder found", page)
        self.assertIn("Running", page)
        self.assertIn("Expected folders not configured", page)
        self.assertNotIn("<script>", page)

    def test_log_filter_escapes_raw_lines(self) -> None:
        page = render_logs(self.connection, {"stream": ["experts"], "q": ["error"]})
        self.assertIn("error &lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("normal journal", page)
        self.assertNotIn("<script>", page)


if __name__ == "__main__":
    unittest.main()
