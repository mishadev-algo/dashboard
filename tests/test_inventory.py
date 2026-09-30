from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from collector.core import Collector, canonical, open_database
from collector.inventory import load_inventory
from collector.remote import heartbeat


class InventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "Terminal"
        self.root.mkdir()
        self.active = self.root / "ACTIVE"
        self.archived = self.root / "OLD"
        self.unknown = self.root / "NEW"
        self.portable = self.base / "Portable"
        for folder in (self.active, self.archived, self.unknown, self.portable):
            (folder / "Logs").mkdir(parents=True)
        self.missing = self.root / "MISSING"
        self.file = self.base / "inventory.json"
        self.file.write_text(json.dumps({
            "expected": [str(self.active), str(self.portable), str(self.missing)],
            "archived": [str(self.archived)],
        }), encoding="utf-8")
        self.connection = open_database(self.base / "local.db")
        self.addCleanup(self.connection.close)

    def test_persistent_inventory_covers_portable_and_classifies_folders(self) -> None:
        inventory = load_inventory(self.file)
        collector = Collector(
            self.connection, "host", [self.root], expected=inventory.expected,
            archived=inventory.archived, coverage_configured=inventory.configured,
        )
        result = collector.run_once()
        self.assertEqual(result.discovered, 4)
        self.assertEqual(result.missing, (canonical(self.missing),))
        self.assertEqual(result.unknown, (canonical(self.unknown),))
        self.assertEqual(result.archived, (canonical(self.archived),))
        state = heartbeat(self.connection, "host", result)
        self.assertEqual(len(state["expected"]), 3)
        self.assertEqual(state["archived"], [canonical(self.archived)])

    def test_overlap_is_rejected(self) -> None:
        self.file.write_text(json.dumps({
            "expected": [str(self.active)], "archived": [str(self.active)],
        }), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_inventory(self.file)


if __name__ == "__main__":
    unittest.main()
