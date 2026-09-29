from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from collector.core import Collector, discover, open_database


class CollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Terminal"
        self.root.mkdir()
        self.first = self.root / "FIRST"
        self.second = self.root / "SECOND"
        self.day = date.today().strftime("%Y%m%d") + ".log"
        for terminal in (self.first, self.second):
            (terminal / "Logs").mkdir(parents=True)
            (terminal / "MQL5" / "Logs").mkdir(parents=True)
        self.connection = open_database(self.base / "events.db")
        self.addCleanup(self.connection.close)

    def rows(self) -> list[tuple[str, str, str]]:
        return self.connection.execute(
            "SELECT data_path, stream, raw_line FROM log_events "
            "JOIN terminals USING (terminal_id) ORDER BY data_path, stream, byte_offset"
        ).fetchall()

    def test_two_terminals_four_streams_and_idempotent_restart(self) -> None:
        for name, terminal in (("first", self.first), ("second", self.second)):
            (terminal / "Logs" / self.day).write_text(f"{name} journal\n", encoding="utf-8")
            (terminal / "MQL5" / "Logs" / self.day).write_bytes(
                (f"{name} experts\r\n").encode("utf-16")
            )
        collector = Collector(self.connection, "vps-92", [self.root], expected=[self.first, self.second])
        result = collector.run_once()
        self.assertEqual((result.discovered, result.events, result.missing, result.unknown), (2, 4, (), ()))
        self.assertEqual({(stream, line) for _, stream, line in self.rows()}, {
            ("journal", "first journal"), ("experts", "first experts"),
            ("journal", "second journal"), ("experts", "second experts"),
        })
        self.assertEqual(Collector(self.connection, "vps-92", [self.root]).run_once().events, 0)
        self.assertEqual(len(self.rows()), 4)

    def test_partial_line_is_ingested_once_after_completion(self) -> None:
        path = self.first / "Logs" / self.day
        path.write_bytes(b"complete\npartial")
        collector = Collector(self.connection, "vps-92", [self.root])
        self.assertEqual(collector.run_once().events, 1)
        self.assertEqual([row[2] for row in self.rows()], ["complete"])
        with path.open("ab") as destination:
            destination.write(b" line\n")
        self.assertEqual(collector.run_once().events, 1)
        self.assertEqual(collector.run_once().events, 0)
        self.assertEqual([row[2] for row in self.rows()], ["complete", "partial line"])

    def test_replacement_and_missing_expected_terminal(self) -> None:
        path = self.first / "Logs" / self.day
        path.write_text("before\n", encoding="utf-8")
        collector = Collector(self.connection, "vps-92", [self.root], expected=[self.first, self.second])
        self.assertEqual(collector.run_once().events, 1)
        path.unlink()
        path.write_text("after\n", encoding="utf-8")
        self.assertEqual(collector.run_once().events, 1)
        self.assertEqual([row[2] for row in self.rows()], ["before", "after"])
        (self.second / "Logs").rmdir()
        (self.second / "MQL5" / "Logs").rmdir()
        result = collector.run_once()
        self.assertEqual(result.discovered, 1)
        self.assertEqual(result.missing, (str(self.second.resolve()).casefold(),))

    def test_explicit_portable_terminal_is_discovered(self) -> None:
        portable = self.base / "Portable MT5"
        (portable / "Logs").mkdir(parents=True)
        self.assertEqual(discover([], [portable]), [portable.resolve()])


if __name__ == "__main__":
    unittest.main()
