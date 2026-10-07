from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from collector.core import Collector, open_database as open_collector_database
from collector.ea_probe import PROBE_FILE, read_probe
from collector.remote import heartbeat
from server.ingest import IngestHandler, ingest, open_database as open_server_database
from server.web import render_dashboard, render_eas, render_logs


class EaInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.terminal = self.base / "terminal"
        (self.terminal / "Logs").mkdir(parents=True)
        self.local = open_collector_database(self.base / "collector.db")
        self.addCleanup(self.local.close)
        self.central = open_server_database(self.base / "central.db")
        self.addCleanup(self.central.close)

    def write_probe(self, *experts: tuple[str, str, str, str]) -> None:
        path = self.terminal / PROBE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-16", newline="") as output:
            writer = csv.writer(output, delimiter="\t")
            writer.writerow(("EA_PROBE_V1", int(datetime.now(timezone.utc).timestamp()), 12345, "Broker-Live"))
            writer.writerows(experts)

    def publish(self) -> None:
        result = Collector(self.local, "vps-one", [], [self.terminal]).run_once()
        ingest(self.central, {"host_id": "vps-one", "events": [],
                              "heartbeat": heartbeat(self.local, "vps-one", result)}, "vps-one")

    def test_two_eas_are_attributed_to_host_terminal_and_account(self) -> None:
        self.write_probe(("101", "Gold <EA>", "XAUUSD", "PERIOD_M5"),
                         ("102", "Trend", "EURUSD", "PERIOD_H1"))
        self.publish()
        page = render_eas(self.central)
        self.assertIn("Gold &lt;EA&gt;", page)
        self.assertIn("Trend", page)
        self.assertIn("12345 @ Broker-Live", page)
        self.assertIn("vps-one", page)
        self.assertIn("2 attached EAs", page)
        self.assertNotIn("Gold <EA>", page)
        self.assertNotIn(str(self.terminal), page)
        self.assertNotIn("chart 101", page)
        self.assertIn("12345 @ Broker-Live", render_dashboard(self.central))
        self.assertIn("12345 @ Broker-Live", render_logs(self.central, {}))

    def test_no_probe_and_empty_probe_have_distinct_states(self) -> None:
        self.publish()
        self.assertIn("No probe report", render_eas(self.central))
        self.write_probe()
        self.publish()
        self.assertIn("No EA attached", render_eas(self.central))

    def test_old_report_is_not_claimed_as_running(self) -> None:
        self.write_probe(("101", "Gold", "XAUUSD", "PERIOD_M5"))
        self.publish()
        page = render_eas(self.central, datetime.now(timezone.utc) + timedelta(minutes=15))
        self.assertIn("Stale report", page)
        self.assertIn("0 attached EAs", page)

    def test_ten_minute_report_remains_visible_with_live_collector(self) -> None:
        self.write_probe(("101", "Gold", "XAUUSD", "PERIOD_M5"))
        self.publish()
        later = datetime.now(timezone.utc) + timedelta(minutes=10)
        with self.central:
            self.central.execute("UPDATE hosts SET last_heartbeat_utc=?", (later.isoformat(),))
        page = render_eas(self.central, later)
        self.assertIn("1 attached EAs", page)

    def test_eas_http_page_is_available(self) -> None:
        self.write_probe(("101", "Gold", "XAUUSD", "PERIOD_M5"))
        self.publish()
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=self.base / "central.db", postgres_dsn=None)
        handler.path = "/eas"
        replies = []
        handler._respond_html = lambda status, page: replies.append((status, page))
        handler.do_GET()
        self.assertEqual(replies[0][0], 200)
        self.assertIn("Gold", replies[0][1])

    def test_corrupt_report_is_rejected(self) -> None:
        self.write_probe(("101", "Gold", "XAUUSD", "PERIOD_M5"),
                         ("101", "Duplicate", "EURUSD", "PERIOD_H1"))
        self.assertEqual(read_probe(self.terminal), {"state": "invalid"})
        self.publish()
        self.assertIn("Invalid probe data", render_eas(self.central))

    def test_empty_chart_rows_do_not_hide_attached_eas(self) -> None:
        self.write_probe(("101", "Gold", "XAUUSD", "PERIOD_M5"),
                         ("102", "", "EURUSD", "PERIOD_H1"))
        self.publish()
        page = render_eas(self.central)
        self.assertIn("Gold", page)
        self.assertIn("1 attached EAs", page)
        self.assertNotIn("Invalid probe data", page)


if __name__ == "__main__":
    unittest.main()
