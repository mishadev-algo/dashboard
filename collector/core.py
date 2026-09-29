from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


DATE_LOG = re.compile(r"^\d{8}\.log$", re.IGNORECASE)
STREAMS = (("journal", Path("Logs")), ("experts", Path("MQL5") / "Logs"))
CHUNK_SIZE = 1024 * 1024


def canonical(path: Path) -> str:
    return str(path.expanduser().resolve()).casefold()


def terminal_id(host_id: str, path: Path) -> str:
    return hashlib.sha256(f"{host_id}\0{canonical(path)}".encode()).hexdigest()[:24]


def is_terminal(path: Path) -> bool:
    return any((path / relative).is_dir() for _, relative in STREAMS)


def discover(roots: Iterable[Path], terminals: Iterable[Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for root in [*roots, *terminals]:
        candidates = [root]
        if root.is_dir():
            try:
                candidates.extend(child for child in root.iterdir() if child.is_dir())
            except OSError:
                continue
        for candidate in candidates:
            if is_terminal(candidate):
                found[canonical(candidate)] = candidate.resolve()
    return [found[key] for key in sorted(found)]


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS terminals (
            terminal_id TEXT PRIMARY KEY,
            host_id TEXT NOT NULL,
            data_path TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS file_cursors (
            terminal_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            file_name TEXT NOT NULL,
            generation INTEGER NOT NULL,
            file_identity TEXT NOT NULL,
            byte_offset INTEGER NOT NULL,
            PRIMARY KEY (terminal_id, stream, file_name)
        );
        CREATE TABLE IF NOT EXISTS log_events (
            terminal_id TEXT NOT NULL,
            host_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            file_name TEXT NOT NULL,
            generation INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            raw_line TEXT NOT NULL,
            received_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            delivered_utc TEXT,
            PRIMARY KEY (terminal_id, stream, file_name, generation, byte_offset)
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(log_events)")}
    if "delivered_utc" not in columns:
        connection.execute("ALTER TABLE log_events ADD COLUMN delivered_utc TEXT")
    connection.commit()
    return connection


def resolve_host_id(connection: sqlite3.Connection, requested: str | None, hostname: str) -> str:
    known = {
        row[0] for row in connection.execute(
            "SELECT host_id FROM terminals UNION SELECT host_id FROM log_events"
        )
    }
    if len(known) > 1:
        raise ValueError(
            "collector database contains multiple host IDs; keep it as an archive and "
            "use a new --db for the central ingest trial"
        )
    existing = next(iter(known), None)
    if requested and existing and requested != existing:
        raise ValueError(f"database host ID is {existing!r}; use that ID or a new --db")
    return requested or existing or hostname


def encoding_and_bom(path: Path) -> tuple[str, int, bytes]:
    with path.open("rb") as source:
        prefix = source.read(256)
    if prefix.startswith(b"\xff\xfe"):
        return "utf-16-le", 2, b"\n\x00"
    if prefix.startswith(b"\xfe\xff"):
        return "utf-16-be", 2, b"\x00\n"
    if prefix.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", 3, b"\n"
    pairs = len(prefix) // 2
    if pairs and prefix[1::2].count(0) > pairs // 3:
        return "utf-16-le", 0, b"\n\x00"
    if pairs and prefix[0::2].count(0) > pairs // 3:
        return "utf-16-be", 0, b"\x00\n"
    return "utf-8", 0, b"\n"


def read_lines(path: Path, offset: int, encoding: str, separator: bytes) -> tuple[list[tuple[int, str]], int]:
    lines: list[tuple[int, str]] = []
    with path.open("rb") as source:
        source.seek(offset)
        data = source.read(CHUNK_SIZE)
    boundaries = [index for index in range(0, len(data) - len(separator) + 1, len(separator)) if data[index:index + len(separator)] == separator]
    end = boundaries[-1] if boundaries else -1
    if end < 0:
        return lines, offset
    complete = data[:end + len(separator)]
    cursor = offset
    start = 0
    for end in boundaries:
        part = complete[start:end]
        raw = part.decode(encoding, errors="replace").rstrip("\r\n\ufeff")
        if raw:
            lines.append((cursor, raw))
        cursor += len(part) + len(separator)
        start = end + len(separator)
    return lines, offset + len(complete)


@dataclass(frozen=True)
class RunResult:
    discovered: int
    events: int
    missing: tuple[str, ...]
    unknown: tuple[str, ...]
    paths: tuple[Path, ...] = ()


class Collector:
    def __init__(
        self,
        connection: sqlite3.Connection,
        host_id: str,
        roots: Iterable[Path],
        terminals: Iterable[Path] = (),
        expected: Iterable[Path] = (),
        lookback_days: int = 2,
    ) -> None:
        self.connection = connection
        self.host_id = host_id
        self.roots = tuple(roots)
        self.terminals = tuple(terminals)
        self.expected = frozenset(canonical(path) for path in expected)
        self.lookback_days = lookback_days

    def run_once(self) -> RunResult:
        paths = discover(self.roots, self.terminals)
        discovered = {canonical(path) for path in paths}
        events = 0
        for path in paths:
            tid = terminal_id(self.host_id, path)
            with self.connection:
                self.connection.execute(
                    "INSERT INTO terminals VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) "
                    "ON CONFLICT(terminal_id) DO UPDATE SET last_seen_utc=excluded.last_seen_utc, data_path=excluded.data_path",
                    (tid, self.host_id, str(path)),
                )
            for stream, relative in STREAMS:
                folder = path / relative
                if not folder.is_dir():
                    continue
                for log_path in sorted(folder.iterdir()):
                    if log_path.is_file() and self._should_read(tid, stream, log_path):
                        events += self._read_file(tid, stream, log_path)
        return RunResult(
            discovered=len(paths),
            events=events,
            missing=tuple(sorted(self.expected - discovered)),
            unknown=tuple(sorted(discovered - self.expected)) if self.expected else (),
            paths=tuple(paths),
        )

    def _should_read(self, tid: str, stream: str, path: Path) -> bool:
        if not DATE_LOG.match(path.name):
            return False
        cursor = self.connection.execute(
            "SELECT 1 FROM file_cursors WHERE terminal_id=? AND stream=? AND file_name=?",
            (tid, stream, path.name),
        ).fetchone()
        if cursor:
            return True
        try:
            file_date = datetime.strptime(path.stem, "%Y%m%d").date()
        except ValueError:
            return False
        return file_date >= date.today() - timedelta(days=self.lookback_days)

    def _read_file(self, tid: str, stream: str, path: Path) -> int:
        stat = path.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
        row = self.connection.execute(
            "SELECT generation, file_identity, byte_offset FROM file_cursors "
            "WHERE terminal_id=? AND stream=? AND file_name=?",
            (tid, stream, path.name),
        ).fetchone()
        encoding, bom, separator = encoding_and_bom(path)
        if row is None:
            generation, offset = 0, bom
        else:
            generation, previous_identity, offset = row
            if previous_identity != identity or stat.st_size < offset:
                generation, offset = generation + 1, bom
            elif offset == 0 and bom:
                offset = bom
        lines, next_offset = read_lines(path, offset, encoding, separator)
        with self.connection:
            self.connection.executemany(
                "INSERT OR IGNORE INTO log_events "
                "(terminal_id, host_id, stream, file_name, generation, byte_offset, raw_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(tid, self.host_id, stream, path.name, generation, position, line) for position, line in lines],
            )
            self.connection.execute(
                "INSERT INTO file_cursors VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(terminal_id, stream, file_name) DO UPDATE SET "
                "generation=excluded.generation, file_identity=excluded.file_identity, byte_offset=excluded.byte_offset",
                (tid, stream, path.name, generation, identity, next_offset),
            )
        return len(lines)
