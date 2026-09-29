from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from collector.process import ProcessSnapshot, classify_terminal


class ProcessProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "DATA"
        self.folder.mkdir()
        (self.folder / "origin.txt").write_bytes(r"C:\Apps\MT5-A".encode("utf-16"))
        self.checked = "2026-09-29T12:00:00+00:00"

    def test_running_and_stopped_use_install_path(self) -> None:
        running = ProcessSnapshot(((421, r"C:\Apps\MT5-A\terminal64.exe"),), True, self.checked)
        self.assertEqual(classify_terminal(self.folder, running)["state"], "running")
        stopped = ProcessSnapshot(((422, r"C:\Apps\MT5-B\terminal64.exe"),), True, self.checked)
        self.assertEqual(classify_terminal(self.folder, stopped)["state"], "stopped")

    def test_inaccessible_process_path_does_not_claim_stopped(self) -> None:
        incomplete = ProcessSnapshot(((421, None),), False, self.checked)
        self.assertEqual(classify_terminal(self.folder, incomplete)["state"], "unknown")

    def test_origin_utf16_without_bom_is_read(self) -> None:
        (self.folder / "origin.txt").write_bytes(r"C:\Apps\MT5-A".encode("utf-16-le"))
        running = ProcessSnapshot(((421, r"C:\Apps\MT5-A\terminal64.exe"),), True, self.checked)
        self.assertEqual(classify_terminal(self.folder, running)["state"], "running")

    def test_portable_executable_can_be_matched(self) -> None:
        (self.folder / "origin.txt").unlink()
        (self.folder / "terminal64.exe").touch()
        running = ProcessSnapshot(((421, str(self.folder / "terminal64.exe")),), True, self.checked)
        self.assertEqual(classify_terminal(self.folder, running)["state"], "running")


if __name__ == "__main__":
    unittest.main()
