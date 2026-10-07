from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from server.alerts import send_close_notifications
from server.ingest import open_database
from server.snapshots import ensure_schema, store_snapshot
from server.web import render_accounts


class CloseNotificationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.connection = open_database(Path(self.temp.name) / "central.db")
        self.addCleanup(self.connection.close)
        self.now = datetime.now(timezone.utc)
        self.path = r"C:\MetaQuotes\Terminal\ONE"
        with self.connection:
            self.connection.execute("INSERT INTO hosts VALUES (?,?)", ("vps", self.now.isoformat()))
            self.connection.execute("INSERT INTO terminals VALUES (?,?,?,?)",
                                    ("vps", "one", self.path, self.now.isoformat()))

    def payload(self, tickets: tuple[int, ...]) -> dict:
        stamp = self.now.isoformat()
        return {
            "host_id": "vps", "terminal_id": "one", "data_path": self.path,
            "observed_at_utc": stamp, "expected_login": 123,
            "expected_server": "Broker", "day_timezone": "UTC",
            "status": {"state": "ok", "connected": True, "data_complete": True},
            "account": {"login": 123, "server": "Broker", "currency": "USD"},
            "history_start_day": self.now.date().isoformat(),
            "positions": [], "strategies": {"42": "Gold EA"},
            "deals": [
                {"ticket": ticket, "time_utc": stamp, "day_local": self.now.date().isoformat(),
                 "type": 1, "kind": "trade", "entry": 1, "position_id": 99,
                 "symbol": "XAUUSD", "magic": 42, "comment": "EA", "volume": 0.1,
                 "profit": 20.0, "commission": -2.0, "swap": -1.0, "fee": -0.5}
                for ticket in tickets
            ],
        }

    def test_baseline_new_close_and_duplicate_snapshot(self) -> None:
        store_snapshot(self.connection, self.payload((1,)), "vps")
        store_snapshot(self.connection, self.payload((1, 2)), "vps")
        store_snapshot(self.connection, self.payload((1, 2)), "vps")
        messages: list[str] = []
        send_close_notifications(self.connection, messages.append, self.now)
        send_close_notifications(self.connection, messages.append, self.now)
        self.assertEqual(len(messages), 1)
        self.assertIn("deal 2", messages[0])
        self.assertIn("16.50 USD", messages[0])
        self.assertEqual(dict(self.connection.execute(
            "SELECT ticket,state FROM close_notifications ORDER BY ticket")),
            {1: "baseline", 2: "sent"})
        page = render_accounts(self.connection, {"from": [self.now.date().isoformat()],
                                                 "to": [self.now.date().isoformat()]}, self.now)
        self.assertIn("Last 15 closed deals", page)
        self.assertIn("Gold EA", page)

    def test_first_empty_snapshot_still_establishes_baseline(self) -> None:
        store_snapshot(self.connection, self.payload(()), "vps")
        store_snapshot(self.connection, self.payload((1,)), "vps")
        self.assertEqual(self.connection.execute(
            "SELECT state FROM close_notifications WHERE ticket=1").fetchone()[0], "pending")

    def test_history_backfill_baselines_old_closes_without_suppressing_new_ones(self) -> None:
        store_snapshot(self.connection, self.payload((1,)), "vps")
        store_snapshot(self.connection, self.payload((1, 2)), "vps", historical_backfill=True)
        messages = []
        send_close_notifications(self.connection, messages.append, self.now)
        self.assertEqual(messages, [])
        self.assertEqual(dict(self.connection.execute(
            "SELECT ticket,state FROM close_notifications ORDER BY ticket")),
            {1: "baseline", 2: "baseline"})
        store_snapshot(self.connection, self.payload((1, 2, 3)), "vps")
        self.assertEqual(self.connection.execute(
            "SELECT state FROM close_notifications WHERE ticket=3").fetchone()[0], "pending")

    def test_existing_deals_table_is_migrated_with_volume(self) -> None:
        old = Path(self.temp.name) / "old.db"
        with closing(sqlite3.connect(old)) as connection:
            connection.execute(
                "CREATE TABLE deals (server TEXT,login INTEGER,ticket INTEGER,time_utc TEXT,"
                "day_local TEXT,type INTEGER,kind TEXT,entry INTEGER,position_id INTEGER,"
                "symbol TEXT,magic INTEGER,strategy TEXT,comment TEXT,profit TEXT,"
                "commission TEXT,swap TEXT,fee TEXT,PRIMARY KEY(server,login,ticket))"
            )
        migrated = open_database(old)
        self.addCleanup(migrated.close)
        self.assertIn("volume", {row[1] for row in migrated.execute("PRAGMA table_info(deals)")})

    def test_existing_accounts_table_is_migrated_with_balance(self) -> None:
        old = Path(self.temp.name) / "old-accounts.db"
        with closing(sqlite3.connect(old)) as connection:
            connection.execute(
                "CREATE TABLE accounts (server TEXT,login INTEGER,currency TEXT,day_timezone TEXT,"
                "host_id TEXT,terminal_id TEXT,latest_snapshot_utc TEXT,history_start_day TEXT,"
                "PRIMARY KEY(server,login))"
            )
        migrated = open_database(old)
        self.addCleanup(migrated.close)
        self.assertIn("balance", {row[1] for row in migrated.execute("PRAGMA table_info(accounts)")})

    def test_existing_account_history_is_baselined_during_upgrade(self) -> None:
        store_snapshot(self.connection, self.payload((1,)), "vps")
        with self.connection:
            self.connection.execute("DELETE FROM close_notifications")
            self.connection.execute("DELETE FROM close_alert_baselines")
        ensure_schema(self.connection)
        self.assertEqual(self.connection.execute(
            "SELECT state FROM close_notifications WHERE ticket=1").fetchone()[0], "baseline")
        store_snapshot(self.connection, self.payload((1, 2)), "vps")
        self.assertEqual(self.connection.execute(
            "SELECT state FROM close_notifications WHERE ticket=2").fetchone()[0], "pending")


if __name__ == "__main__":
    unittest.main()
