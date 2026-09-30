from __future__ import annotations

import json
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collector.accounts import AccountTarget, load_targets, probe_target
from collector.mt5_snapshot import collect
from collector.process import ProcessSnapshot
from server.alerts import evaluate
from server.ingest import open_database
from server.ingest import IngestHandler
from server.snapshots import store_snapshot
from server.web import render_accounts, render_dashboard


class FakeMt5:
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    DEAL_TYPE_BALANCE = 2
    DEAL_TYPE_CREDIT = 3
    DEAL_TYPE_COMMISSION = 7

    def __init__(self, path: str, login: int = 123) -> None:
        self.path = path
        self.login = login
        self.initialized = False
        self.shutdown_called = False
        self.connected = True
        self.trading = False
        self.positions = []
        self.deals = []

    def initialize(self, executable, **kwargs):
        self.initialized = True
        return True

    def shutdown(self):
        self.shutdown_called = True

    def terminal_info(self):
        return SimpleNamespace(data_path=self.path, path=r"C:\Apps\MT5", connected=self.connected,
                               trade_allowed=self.trading, build=5000)

    def account_info(self):
        return SimpleNamespace(login=self.login, server="Broker-Live", currency="USD")

    def positions_get(self):
        return self.positions

    def history_deals_get(self, start, end):
        return self.deals


class AccountSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = open_database(Path(self.temp.name) / "central.db")
        self.addCleanup(self.connection.close)
        self.path = r"C:\Users\Operator\AppData\Roaming\MetaQuotes\Terminal\ONE"
        self.now = datetime.now(timezone.utc)
        with self.connection:
            self.connection.execute("INSERT INTO hosts VALUES (?,?)", ("vps", self.now.isoformat()))
            self.connection.execute("INSERT INTO terminals VALUES (?,?,?,?)", ("vps", "terminal-one", self.path, self.now.isoformat()))
            state = {
                "coverage_configured": True, "expected": [self.path], "missing": [],
                "terminals": [{"terminal_id": "terminal-one", "data_path": self.path,
                               "process": {"state": "running"}}],
            }
            self.connection.execute(
                "INSERT INTO collector_heartbeats (host_id,received_utc,observed_at_utc,pending_count,payload_json) "
                "VALUES (?,?,?,?,?)", ("vps", self.now.isoformat(), self.now.isoformat(), 0, json.dumps(state)),
            )

    def payload(self, login=123, *, connected=True):
        mt5 = FakeMt5(self.path, login)
        mt5.connected = connected
        deal_time = int(self.now.timestamp())
        mt5.positions = [SimpleNamespace(ticket=99, symbol="XAUUSD", type=0, magic=42, volume=0.1,
                                         price_open=3000.0, price_current=3010.0, profit=10.0,
                                         swap=-1.0, comment="EA")]
        mt5.deals = [
            SimpleNamespace(ticket=1, time=deal_time, time_msc=deal_time * 1000, type=1, entry=1,
                            position_id=99, symbol="XAUUSD", magic=42, comment="EA", profit=20.0,
                            commission=-2.0, swap=-1.0, fee=-0.5),
            SimpleNamespace(ticket=2, time=deal_time, time_msc=deal_time * 1000, type=2, entry=0,
                            position_id=0, symbol="", magic=0, comment="Deposit", profit=1000.0,
                            commission=0.0, swap=0.0, fee=0.0),
        ]
        result = collect(mt5, self.path, r"C:\Apps\MT5\terminal64.exe", 123, "Broker-Live", "UTC", 7, self.now)
        self.assertTrue(mt5.shutdown_called)
        if result.pop("data_complete", False):
            result["status"]["data_complete"] = True
        if result["status"].get("data_complete"):
            result["strategies"] = {"42": "Gold EA"}
        return {
            "host_id": "vps", "terminal_id": "terminal-one", "data_path": self.path,
            "observed_at_utc": self.now.isoformat(), "expected_login": 123,
            "expected_server": "Broker-Live", "day_timezone": "UTC",
            **result,
        }

    def test_pnl_positions_and_idempotent_deal_upload(self) -> None:
        payload = self.payload()
        self.assertEqual(store_snapshot(self.connection, payload, "vps")["deals"], 2)
        store_snapshot(self.connection, payload, "vps")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0], 2)
        self.assertEqual(self.connection.execute("SELECT strategy FROM positions").fetchone()[0], "Gold EA")
        page = render_accounts(self.connection, {"day": [self.now.date().isoformat()]}, self.now)
        self.assertIn("16.50 USD", page)
        self.assertIn("1000.00 USD", page)
        self.assertIn("Gold EA", page)
        self.assertIn("Strategy PnL", page)
        status = render_dashboard(self.connection, self.now + timedelta(seconds=1))
        self.assertIn("Connected", status)
        self.assertIn("Disabled", status)

    def test_account_mismatch_never_updates_account_data(self) -> None:
        payload = self.payload(login=456)
        self.assertEqual(payload["status"]["state"], "account_mismatch")
        store_snapshot(self.connection, payload, "vps")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)
        self.assertIn("Account mismatch", render_dashboard(self.connection, self.now + timedelta(seconds=1)))

    def test_disconnected_alert_and_recovery_keep_last_positions(self) -> None:
        complete = self.payload()
        store_snapshot(self.connection, complete, "vps")
        disconnected = self.payload(connected=False)
        store_snapshot(self.connection, disconnected, "vps")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 1)
        messages = []
        evaluate(self.connection, messages.append, self.now + timedelta(seconds=1))
        self.assertEqual(len(messages), 1)
        self.assertIn("Broker disconnected", messages[0])
        store_snapshot(self.connection, complete, "vps")
        evaluate(self.connection, messages.append, self.now + timedelta(seconds=2))
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[-1].startswith("Recovered:"))

    def test_stale_probe_does_not_claim_broker_recovered(self) -> None:
        disconnected = self.payload(connected=False)
        store_snapshot(self.connection, disconnected, "vps")
        messages = []
        evaluate(self.connection, messages.append, self.now + timedelta(seconds=1))
        self.assertEqual(len(messages), 1)
        later = self.now + timedelta(seconds=151)
        with self.connection:
            self.connection.execute("UPDATE hosts SET last_heartbeat_utc=? WHERE host_id='vps'",
                                    (later.isoformat(),))
        evaluate(self.connection, messages.append, later)
        self.assertEqual(len(messages), 1)
        state = self.connection.execute(
            "SELECT state FROM alerts WHERE alert_type='broker_disconnected'"
        ).fetchone()[0]
        self.assertEqual(state, "active")

    def test_rejects_wrong_host_and_account_data(self) -> None:
        payload = self.payload()
        with self.assertRaisesRegex(ValueError, "host_id"):
            store_snapshot(self.connection, payload, "other-host")
        payload["account"]["login"] = 999
        with self.assertRaisesRegex(ValueError, "account does not match"):
            store_snapshot(self.connection, payload, "vps")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0], 0)

    def test_snapshot_http_route_requires_host_token(self) -> None:
        payload = self.payload()
        body = json.dumps(payload).encode()
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=Path(self.temp.name) / "central.db",
                                         host_tokens={"vps": "secret"})
        handler.path = "/v1/snapshot"
        handler.headers = {"Authorization": "Bearer wrong", "Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        replies = []
        handler._respond = lambda status, value: replies.append((status, value))
        handler.do_POST()
        self.assertEqual(replies[-1][0], 401)
        handler.headers["Authorization"] = "Bearer secret"
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        self.assertEqual(replies[-1][0], 200)
        self.assertEqual(replies[-1][1]["deals"], 2)

    def test_same_deal_ticket_on_two_accounts_stays_separate(self) -> None:
        first = self.payload()
        store_snapshot(self.connection, first, "vps")
        second_path = self.path.replace("ONE", "TWO")
        with self.connection:
            self.connection.execute("INSERT INTO terminals VALUES (?,?,?,?)",
                                    ("vps", "terminal-two", second_path, self.now.isoformat()))
        second = self.payload()
        second.update({"terminal_id": "terminal-two", "data_path": second_path, "expected_login": 456})
        second["account"]["login"] = 456
        second["deals"][0]["profit"] = -8.0
        store_snapshot(self.connection, second, "vps")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM deals").fetchone()[0], 4)
        profits = dict(self.connection.execute("SELECT login,profit FROM deals WHERE ticket=1"))
        self.assertEqual(profits, {123: "20.0", 456: "-8.0"})

    def test_unmapped_trade_stays_visible_in_strategy_breakdown(self) -> None:
        payload = self.payload()
        extra = dict(payload["deals"][0])
        extra.update({"ticket": 3, "magic": 777, "profit": 4.0,
                      "commission": 0.0, "swap": 0.0, "fee": 0.0})
        payload["deals"].append(extra)
        store_snapshot(self.connection, payload, "vps")
        page = render_accounts(self.connection, {"day": [self.now.date().isoformat()]},
                               self.now + timedelta(seconds=1))
        self.assertIn("unmapped", page)
        self.assertIn("20.50 USD", page)

    def test_stopped_terminal_does_not_start_mt5(self) -> None:
        folder = Path(self.temp.name) / "DATA"
        folder.mkdir()
        (folder / "origin.txt").write_text(r"C:\Apps\MT5")
        target = AccountTarget(folder, 123, "Broker-Live", "UTC", 7, {})
        snapshot = ProcessSnapshot((), True, self.now.isoformat())
        with patch("collector.accounts.subprocess.run") as run:
            result = probe_target(target, "vps", snapshot)
        run.assert_not_called()
        self.assertEqual(result["status"]["state"], "stopped")

    def test_config_requires_explicit_account_mapping(self) -> None:
        config = Path(self.temp.name) / "accounts.json"
        config.write_text(json.dumps({"terminals": [{"data_path": self.path, "login": 123,
                                                      "server": "Broker-Live", "day_timezone": "UTC"}]}))
        self.assertEqual(load_targets(config)[0].login, 123)
        config.write_text(json.dumps({"terminals": [{"data_path": self.path, "login": 123}]}))
        with self.assertRaises(ValueError):
            load_targets(config)


if __name__ == "__main__":
    unittest.main()
