from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from collector.core import open_database as open_collector_database
from collector.run_host import service_plan as host_service_plan
from server.ingest import open_database as open_server_database
from server.run_all import service_plan
from server.run_central import service_plan as central_service_plan


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
        with self.assertRaisesRegex(ValueError, "differs from DASHBOARD_HOST_TOKENS"):
            service_plan(self.args, bad)
        missing_token = dict(self.environ)
        del missing_token["DASHBOARD_COLLECTOR_TOKEN"]
        with self.assertRaisesRegex(ValueError, "not set in this PowerShell window"):
            service_plan(self.args, missing_token)
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

    def test_account_target_missing_from_expected_names_folder(self) -> None:
        self.accounts.write_text(json.dumps({"terminals": [{
            "data_path": str(self.root / "TWO"), "login": 123, "server": "Broker",
        }]}))
        with self.assertRaisesRegex(ValueError, "account target\\(s\\) missing from inventory expected: .*TWO"):
            service_plan(self.args, self.environ)

    def test_split_central_and_host_plans_keep_secrets_out_of_commands(self) -> None:
        central_args = argparse.Namespace(db=self.central_db, port=8765, no_alerts=False)
        central_services = central_service_plan(central_args, self.environ)
        self.assertEqual([service.name for service in central_services], ["server", "alerts"])
        host_args = argparse.Namespace(
            collector_db=self.collector_db, inventory=self.inventory, accounts=self.accounts,
            host_id=None, server_url="https://dashboard.example.com", root=[], terminal=[],
        )
        host, host_services = host_service_plan(host_args, self.environ)
        self.assertEqual(host, "vps")
        self.assertEqual([service.name for service in host_services], ["collector", "accounts"])
        commands = " ".join(" ".join(service.command) for service in central_services + host_services)
        self.assertIn("https://dashboard.example.com", commands)
        self.assertNotIn("secret", commands)
        self.assertNotIn(str(self.central_db), " ".join(host_services[0].command))

    def test_split_host_requires_https_and_token(self) -> None:
        host_args = argparse.Namespace(
            collector_db=self.collector_db, inventory=self.inventory, accounts=None,
            host_id=None, server_url="http://dashboard.example.com", root=[], terminal=[],
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            host_service_plan(host_args, self.environ)
        host_args.server_url = "https://dashboard.example.com"
        with self.assertRaisesRegex(ValueError, "DASHBOARD_COLLECTOR_TOKEN"):
            host_service_plan(host_args, {})

    def test_split_central_rejects_duplicate_host_tokens(self) -> None:
        args = argparse.Namespace(db=self.central_db, port=8765, no_alerts=True)
        bad = {"DASHBOARD_HOST_TOKENS": '{"vps1":"same","vps2":"same"}'}
        with self.assertRaisesRegex(ValueError, "distinct token"):
            central_service_plan(args, bad)

    def test_postgres_central_plan_keeps_dsn_out_of_process_arguments(self) -> None:
        args = argparse.Namespace(db=None, postgres=True, port=8765, no_alerts=False)
        environment = dict(self.environ, DASHBOARD_POSTGRES_DSN="postgresql://user:password@host/db")
        services = central_service_plan(args, environment)
        self.assertEqual([service.name for service in services], ["server", "alerts"])
        self.assertTrue(all("--postgres" in service.command for service in services))
        self.assertNotIn(environment["DASHBOARD_POSTGRES_DSN"], " ".join(
            " ".join(service.command) for service in services))
        del environment["DASHBOARD_POSTGRES_DSN"]
        with self.assertRaisesRegex(ValueError, "DASHBOARD_POSTGRES_DSN"):
            central_service_plan(args, environment)


if __name__ == "__main__":
    unittest.main()
