from __future__ import annotations

import unittest
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from shared.config import host_tokens
from shared.network import server_origin
from shared.paths import same_windows_path
from shared.sqlite import open_readonly
from shared.timezones import day_zone


class SharedValidationTest(unittest.TestCase):
    def test_utc_works_without_system_zone_database(self) -> None:
        with patch("shared.timezones.ZoneInfo", side_effect=ZoneInfoNotFoundError("UTC")):
            self.assertIs(day_zone("UTC"), timezone.utc)
            self.assertEqual(datetime(2026, 1, 1, tzinfo=day_zone("UTC")).utcoffset().total_seconds(), 0)

    def test_missing_non_utc_zone_has_actionable_error(self) -> None:
        with patch("shared.timezones.ZoneInfo", side_effect=ZoneInfoNotFoundError("Europe/Kyiv")):
            with self.assertRaisesRegex(ValueError, "install tzdata on Windows"):
                day_zone("Europe/Kyiv")

    def test_origin_is_shared_and_rejects_credentials_and_paths(self) -> None:
        self.assertEqual(server_origin("https://example.com/"), "https://example.com")
        self.assertEqual(server_origin("http://127.0.0.1:8765"), "http://127.0.0.1:8765")
        for value in ("http://example.com", "https://user:secret@example.com",
                      "https://example.com/v1", "https://example.com:99999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server_origin(value)

    def test_host_tokens_are_distinct_and_windows_paths_compare_consistently(self) -> None:
        self.assertEqual(host_tokens({"DASHBOARD_HOST_TOKENS": '{"a":"one","b":"two"}'})["b"], "two")
        with self.assertRaisesRegex(ValueError, "distinct token"):
            host_tokens({"DASHBOARD_HOST_TOKENS": '{"a":"same","b":"same"}'})
        self.assertTrue(same_windows_path(r"C:\MT5\Terminal", r"c:/mt5/terminal"))

    def test_readonly_sqlite_never_creates_or_writes_a_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                open_readonly(path)
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE example (value INTEGER)")
                connection.commit()
            connection = open_readonly(path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO example VALUES (1)")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
