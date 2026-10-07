from __future__ import annotations

import json
import ntpath
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from shared.paths import windows_path


PROCESS_POLL_SECONDS = 30


def _decode_origin(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    pairs = len(raw) // 2
    if pairs and raw[1::2].count(0) > pairs // 3:
        return raw.decode("utf-16-le", errors="replace")
    if pairs and raw[0::2].count(0) > pairs // 3:
        return raw.decode("utf-16-be", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "cp1252", errors="replace")


def install_path(data_path: Path) -> str | None:
    origin = data_path / "origin.txt"
    if origin.is_file():
        try:
            raw = origin.read_bytes()
            decoded = _decode_origin(raw).strip("\ufeff\x00\r\n ")
            first_line = next((line.strip() for line in decoded.splitlines() if line.strip()), "")
            if first_line:
                normalized = windows_path(first_line)
                if ntpath.basename(normalized) in ("terminal64.exe", "terminal.exe"):
                    normalized = ntpath.dirname(normalized)
                return normalized
        except OSError:
            return None
    if any((data_path / name).is_file() for name in ("terminal64.exe", "terminal.exe")):
        return windows_path(str(data_path))
    return None


@dataclass(frozen=True)
class ProcessSnapshot:
    processes: tuple[tuple[int, str | None], ...]
    complete: bool
    checked_at_utc: str
    error: str | None = None


def list_terminal_processes() -> ProcessSnapshot:
    checked_at = datetime.now(timezone.utc).isoformat()
    if os.name != "nt":
        return ProcessSnapshot((), False, checked_at, "Windows process probe unavailable")
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe' OR Name='terminal.exe'\" "
        "| Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False,
        )
        if completed.returncode:
            return ProcessSnapshot((), False, checked_at, "Windows process query failed")
        output = completed.stdout.decode("utf-8-sig", errors="replace").strip()
        records = json.loads(output) if output else []
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise ValueError("unexpected process response")
        processes = []
        complete = True
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("unexpected process record")
            pid = record.get("ProcessId")
            path = record.get("ExecutablePath")
            if type(pid) is not int:
                raise ValueError("missing process ID")
            if not isinstance(path, str) or not path:
                complete = False
                path = None
            processes.append((pid, path))
        return ProcessSnapshot(tuple(processes), complete, checked_at)
    except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
        return ProcessSnapshot((), False, checked_at, "Windows process query unavailable")


def classify_terminal(data_path: Path, snapshot: ProcessSnapshot) -> dict:
    result = {"state": "unknown", "checked_at_utc": snapshot.checked_at_utc}
    if snapshot.error:
        result["reason"] = snapshot.error
        return result
    expected_install = install_path(data_path)
    if not expected_install:
        result["reason"] = "No readable origin.txt or portable terminal executable"
        return result
    for pid, executable in snapshot.processes:
        if executable and windows_path(ntpath.dirname(executable)) == expected_install:
            result.update({"state": "running", "pid": pid})
            return result
    if not snapshot.complete:
        result["reason"] = "Some MT5 process paths are inaccessible"
        return result
    result["state"] = "stopped"
    return result


class ProcessProbe:
    def __init__(self, interval: float = PROCESS_POLL_SECONDS) -> None:
        self.interval = interval
        self._snapshot: ProcessSnapshot | None = None
        self._last_poll = 0.0
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    def _poll(self) -> None:
        snapshot = list_terminal_processes()
        with self._lock:
            self._snapshot = snapshot

    def check(self, paths: Iterable[Path]) -> dict[str, dict]:
        now = time.monotonic()
        with self._lock:
            if (self._worker is None or not self._worker.is_alive()) and now - self._last_poll >= self.interval:
                self._worker = threading.Thread(target=self._poll, daemon=True)
                self._last_poll = now
                self._worker.start()
            snapshot = self._snapshot
        if snapshot is None:
            snapshot = ProcessSnapshot((), False, datetime.now(timezone.utc).isoformat(), "Process probe pending")
        return {str(path): classify_terminal(path, snapshot) for path in paths}
