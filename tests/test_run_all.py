from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from collector.core import open_database as open_collector_database
from server.ingest import open_database as open_server_database
from server.run_all import service_plan


class OneConsolePlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.collector_db = self.root / "collector-central.db"
        local = open_collector_database(self.collector_db)
        local.execute("INSERT INTO terminals VALUES (?,?,?,?)",
                      ("terminal-one", "vps", str(self.root / "ONE"), "2026-09-30T00:00:00+00:00"))
        local.commit()
        local.close()
        self.central_db = self.root / "central.db"
        central = open_server_database(self.central_db)
        central.close()
        self.inventory = self.root / "inventory.json"
        self.inventory.write_text(json.dumps({"expected": [str(self.root / "ONE")], "archived": []}))
        self.accounts = self.root / "accounts.json"
        self.accounts.write_text(json.dumps({"terminals": [{
            "data_path": str(self.root / "ONE"), "login": 123, "server": "Broker", "day_timezone": "UTC",
        }]}))
        self.args = argparse.Namespace(
            db=self.central_db, collector_db=self.collector_db, inventory=self.inventory,
            accounts=self.accounts, host_id=None, root=[], terminal=[], port=8765, no_alerts=False,
        )
        self.environ = {
            "DASHBOARD_HOST_TOKENS": '{"vps":"secret"}',
            "DASHBOARD_COLLECTOR_TOKEN": "secret",
            "DASHBOARD_TELEGRAM_BOT_TOKEN": "bot-token",
            "DASHBOARD_TELEGRAM_CHAT_ID": "chat-id",
        }

    def test_reuses_existing_host_and_builds_four_processes(self) -> None:
        host, services = service_plan(self.args, self.environ)
        self.assertEqual(host, "vps")
        self.assertEqual([service.name for service in services],
                         ["server", "collector", "alerts", "accounts"])
        collector = services[1].command
        self.assertIn(str(self.collector_db), collector)
        self.assertIn("vps", collector)
        self.assertIn("http://127.0.0.1:8765", collector)
        self.assertNotIn("secret", " ".join(" ".join(service.command) for service in services))

    def test_wrong_token_or_missing_inventory_fails_before_start(self) -> None:
        bad = dict(self.environ, DASHBOARD_COLLECTOR_TOKEN="wrong")
        with self.assertRaisesRegex(ValueError, "must match"):
            service_plan(self.args, bad)
        self.inventory.unlink()
        with self.assertRaisesRegex(ValueError, "inventory file is missing"):
            service_plan(self.args, self.environ)

    def test_optional_account_worker_and_alerts_can_be_omitted(self) -> None:
        self.args.accounts = None
        self.args.no_alerts = True
        host, services = service_plan(self.args, {
            "DASHBOARD_HOST_TOKENS": '{"vps":"secret"}',
            "DASHBOARD_COLLECTOR_TOKEN": "secret",
        })
        self.assertEqual(host, "vps")
        self.assertEqual([service.name for service in services], ["server", "collector"])


if __name__ == "__main__":
    unittest.main()
