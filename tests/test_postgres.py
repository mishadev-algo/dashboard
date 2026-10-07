from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from server.postgres import PostgresConnection


class FakeTransaction:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("begin")

    def __exit__(self, exc_type, exc_value, traceback):
        self.events.append("rollback" if exc_type else "commit")


class FakeCursor:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def executemany(self, query, params):
        self.events.append((query, params))


class FakeConnection:
    def __init__(self):
        self.events = []

    def execute(self, query, params):
        self.events.append((query, params))
        return self

    def transaction(self):
        return FakeTransaction(self.events)

    def cursor(self):
        return FakeCursor(self.events)

    def close(self):
        self.events.append("close")


class PostgresAdapterTest(unittest.TestCase):
    def test_parameters_and_transactions_use_psycopg_conventions(self) -> None:
        raw = FakeConnection()
        fake_driver = types.SimpleNamespace(connect=lambda dsn, autocommit: raw)
        with patch.dict(sys.modules, {"psycopg": fake_driver}):
            connection = PostgresConnection("postgresql://example")
            with connection:
                connection.execute("INSERT INTO hosts VALUES (?,?)", ("one", "now"))
                connection.executemany("INSERT INTO hosts VALUES (?,?)", [("two", "now")])
            with self.assertRaisesRegex(RuntimeError, "failure"):
                with connection:
                    raise RuntimeError("failure")
            connection.close()
        self.assertEqual(raw.events, [
            "begin", ("INSERT INTO hosts VALUES (%s,%s)", ("one", "now")),
            ("INSERT INTO hosts VALUES (%s,%s)", [("two", "now")]),
            "commit", "begin", "rollback", "close",
        ])


if __name__ == "__main__":
    unittest.main()
