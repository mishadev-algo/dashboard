from __future__ import annotations

import hmac
import json
import re
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from collector.protocol import event_id


MAX_BODY_BYTES = 4 * 1024 * 1024
DATE_LOG = re.compile(r"^\d{8}\.log$", re.IGNORECASE)


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS hosts (
            host_id TEXT PRIMARY KEY,
            last_heartbeat_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS terminals (
            host_id TEXT NOT NULL,
            terminal_id TEXT NOT NULL,
            data_path TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            PRIMARY KEY (host_id, terminal_id)
        );
        CREATE TABLE IF NOT EXISTS log_events (
            event_id TEXT PRIMARY KEY,
            host_id TEXT NOT NULL,
            terminal_id TEXT NOT NULL,
            stream TEXT NOT NULL,
            file_name TEXT NOT NULL,
            generation INTEGER NOT NULL,
            byte_offset INTEGER NOT NULL,
            raw_line TEXT NOT NULL,
            received_utc TEXT NOT NULL,
            UNIQUE (host_id, terminal_id, stream, file_name, generation, byte_offset)
        );
        CREATE TABLE IF NOT EXISTS collector_heartbeats (
            heartbeat_id INTEGER PRIMARY KEY,
            host_id TEXT NOT NULL,
            received_utc TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL,
            pending_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );
    """)
    return connection


def _valid_event(event: object, host_id: str) -> bool:
    if not isinstance(event, dict):
        return False
    if event.get("host_id") != host_id:
        return False
    if not all(isinstance(event.get(key), str) and event[key] for key in ("event_id", "terminal_id", "data_path", "raw_line")):
        return False
    if event.get("stream") not in ("journal", "experts"):
        return False
    if not isinstance(event.get("file_name"), str) or not DATE_LOG.fullmatch(event["file_name"]):
        return False
    if not all(type(event.get(key)) is int and event[key] >= 0 for key in ("generation", "byte_offset")):
        return False
    return event["event_id"] == event_id(event)


def ingest(connection: sqlite3.Connection, payload: object, authorized_host: str) -> dict:
    if not isinstance(payload, dict) or payload.get("host_id") != authorized_host:
        raise ValueError("host_id does not match token")
    events = payload.get("events")
    state = payload.get("heartbeat")
    if not isinstance(events, list) or len(events) > 500 or not all(_valid_event(event, authorized_host) for event in events):
        raise ValueError("invalid events")
    if not isinstance(state, dict) or not isinstance(state.get("observed_at_utc"), str) or not state["observed_at_utc"]:
        raise ValueError("invalid heartbeat")
    if type(state.get("pending_count")) is not int or state["pending_count"] < 0:
        raise ValueError("invalid pending_count")
    if not isinstance(state.get("terminals"), list) or not isinstance(state.get("missing"), list) or not isinstance(state.get("unknown"), list):
        raise ValueError("invalid terminal coverage")
    if not all(isinstance(path, str) for path in state["missing"] + state["unknown"]):
        raise ValueError("invalid terminal coverage")
    for terminal in state["terminals"]:
        if not isinstance(terminal, dict) or not isinstance(terminal.get("terminal_id"), str) or not isinstance(terminal.get("data_path"), str):
            raise ValueError("invalid terminal")
    now = datetime.now(timezone.utc).isoformat()
    acknowledged = []
    stored = 0
    with connection:
        connection.execute(
            "INSERT INTO hosts VALUES (?, ?) ON CONFLICT(host_id) DO UPDATE SET last_heartbeat_utc=excluded.last_heartbeat_utc",
            (authorized_host, now),
        )
        for terminal in state["terminals"]:
            connection.execute(
                "INSERT INTO terminals VALUES (?, ?, ?, ?) ON CONFLICT(host_id, terminal_id) "
                "DO UPDATE SET data_path=excluded.data_path, last_seen_utc=excluded.last_seen_utc",
                (authorized_host, terminal["terminal_id"], terminal["data_path"], now),
            )
        for event in events:
            connection.execute(
                "INSERT INTO terminals VALUES (?, ?, ?, ?) ON CONFLICT(host_id, terminal_id) "
                "DO UPDATE SET data_path=excluded.data_path",
                (authorized_host, event["terminal_id"], event["data_path"], now),
            )
            existing = connection.execute(
                "SELECT raw_line FROM log_events WHERE event_id=?", (event["event_id"],)
            ).fetchone()
            if existing is not None:
                if existing[0] != event["raw_line"]:
                    raise ValueError("event key conflicts with stored line")
            else:
                connection.execute(
                    "INSERT INTO log_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event["event_id"], authorized_host, event["terminal_id"], event["stream"],
                     event["file_name"], event["generation"], event["byte_offset"], event["raw_line"], now),
                )
                stored += 1
            acknowledged.append(event["event_id"])
        connection.execute(
            "INSERT INTO collector_heartbeats (host_id, received_utc, observed_at_utc, pending_count, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (authorized_host, now, state["observed_at_utc"], state["pending_count"], json.dumps(state, ensure_ascii=False)),
        )
    return {"acknowledged": acknowledged, "received": len(events), "stored": stored}


class IngestServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], db_path: Path, host_tokens: dict[str, str]):
        super().__init__(address, IngestHandler)
        self.db_path = db_path
        self.host_tokens = host_tokens
        connection = open_database(db_path)
        connection.close()


class IngestHandler(BaseHTTPRequestHandler):
    server: IngestServer

    def _respond(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/ingest":
            self._respond(404, {"error": "not found"})
            return
        authorization = self.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            self._respond(401, {"error": "unauthorized"})
            return
        token = authorization[7:]
        authorized_host = next(
            (host for host, secret in self.server.host_tokens.items() if hmac.compare_digest(token, secret)), None
        )
        if authorized_host is None:
            self._respond(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("invalid body length")
            payload = json.loads(self.rfile.read(length))
            connection = open_database(self.server.db_path)
            try:
                result = ingest(connection, payload, authorized_host)
            finally:
                connection.close()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._respond(400, {"error": str(exc)})
            return
        self._respond(200, result)
