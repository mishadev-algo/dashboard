from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from collector.protocol import event_id
from shared.sqlite import open_readonly
from .snapshots import ensure_schema, store_snapshot
from .postgres import PostgresConnection
from .web import render_accounts, render_dashboard, render_eas, render_logs
from .portfolio import render_portfolio
from .strategy import render_strategy
from .backtest_lab import ensure_backtest_schema, import_backtest, render_backtest_lab


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
        CREATE TABLE IF NOT EXISTS alerts (
            host_id TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            target TEXT NOT NULL,
            state TEXT NOT NULL,
            detail TEXT NOT NULL,
            changed_utc TEXT NOT NULL,
            last_attempt_utc TEXT,
            last_sent_utc TEXT,
            active_notified INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            PRIMARY KEY (host_id, alert_type, target)
        );
        CREATE INDEX IF NOT EXISTS idx_log_events_received ON log_events(received_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_log_events_terminal_received
            ON log_events(host_id, terminal_id, received_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_heartbeats_host_latest
            ON collector_heartbeats(host_id, heartbeat_id DESC);
    """)
    ensure_schema(connection)
    ensure_backtest_schema(connection)
    return connection


def open_storage(db_path: Path | None, postgres_dsn: str | None = None):
    if postgres_dsn is not None:
        connection = PostgresConnection(postgres_dsn)
        try:
            connection.execute("SELECT 1 FROM hosts LIMIT 1")
        except Exception:
            connection.close()
            raise
        return connection
    if db_path is None:
        raise ValueError("SQLite database path or PostgreSQL configuration is required")
    return open_database(db_path)


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
    if state is None and not events:
        raise ValueError("heartbeat or events required")
    if state is not None:
        if not isinstance(state, dict) or not isinstance(state.get("observed_at_utc"), str) or not state["observed_at_utc"]:
            raise ValueError("invalid heartbeat")
        if type(state.get("pending_count")) is not int or state["pending_count"] < 0:
            raise ValueError("invalid pending_count")
        if not isinstance(state.get("terminals"), list):
            raise ValueError("invalid terminal coverage")
        for name in ("missing", "unknown", "expected", "archived"):
            paths = state.get(name, [])
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise ValueError("invalid terminal coverage")
        for terminal in state["terminals"]:
            if not isinstance(terminal, dict) or not isinstance(terminal.get("terminal_id"), str) or not isinstance(terminal.get("data_path"), str):
                raise ValueError("invalid terminal")
    now = datetime.now(timezone.utc).isoformat()
    acknowledged = []
    stored = 0
    with connection:
        if state is not None:
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
            inserted = connection.execute(
                "INSERT INTO log_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
                (event["event_id"], authorized_host, event["terminal_id"], event["stream"],
                 event["file_name"], event["generation"], event["byte_offset"], event["raw_line"], now),
            ).fetchone()
            if inserted is None:
                existing = connection.execute(
                    "SELECT raw_line FROM log_events WHERE event_id=?", (event["event_id"],)
                ).fetchone()
                if existing is None or existing[0] != event["raw_line"]:
                    raise ValueError("event key conflicts with stored line")
            else:
                stored += 1
            acknowledged.append(event["event_id"])
        if state is not None:
            connection.execute(
                "INSERT INTO collector_heartbeats (host_id, received_utc, observed_at_utc, pending_count, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (authorized_host, now, state["observed_at_utc"], state["pending_count"], json.dumps(state, ensure_ascii=False)),
            )
    return {"acknowledged": acknowledged, "received": len(events), "stored": stored}


class IngestServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], db_path: Path | None, host_tokens: dict[str, str],
                 postgres_dsn: str | None = None):
        super().__init__(address, IngestHandler)
        self.db_path = db_path
        self.postgres_dsn = postgres_dsn
        self.host_tokens = host_tokens
        self.csrf_token = secrets.token_urlsafe(32)
        connection = open_storage(db_path, postgres_dsn)
        ensure_backtest_schema(connection)
        connection.close()


class IngestHandler(BaseHTTPRequestHandler):
    server: IngestServer

    def _respond_html(self, status: int, value: str) -> None:
        body = value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        postgres_dsn = getattr(self.server, "postgres_dsn", None)
        if parsed.path == "/health":
            try:
                connection = (open_storage(None, postgres_dsn) if postgres_dsn is not None else
                              open_readonly(self.server.db_path))
                with closing(connection) as connection:
                    connection.execute("SELECT 1 FROM hosts LIMIT 1").fetchone()
            except Exception:
                self._respond(503, {"status": "unavailable"})
                return
            self._respond(200, {"status": "ok"})
            return
        if parsed.path not in ("/", "/logs", "/accounts", "/eas", "/portfolio", "/strategy", "/backtests"):
            self._respond(404, {"error": "not found"})
            return
        connection = open_storage(self.server.db_path, postgres_dsn)
        try:
            if parsed.path == "/":
                page = render_dashboard(connection)
            elif parsed.path == "/logs":
                page = render_logs(connection, parse_qs(parsed.query))
            elif parsed.path == "/portfolio":
                page = render_portfolio(connection, parse_qs(parsed.query))
            elif parsed.path == "/backtests":
                page = render_backtest_lab(connection, parse_qs(parsed.query),
                                           "Backtest imported." if "imported" in parse_qs(parsed.query) else "",
                                           self.server.csrf_token)
            elif parsed.path == "/strategy":
                page = render_strategy(connection, parse_qs(parsed.query))
            elif parsed.path == "/eas":
                page = render_eas(connection)
            else:
                page = render_accounts(connection, parse_qs(parsed.query))
        finally:
            connection.close()
        self._respond_html(200, page)

    def do_POST(self) -> None:
        if self.path == "/backtests/import":
            self._import_backtest()
            return
        if self.path not in ("/v1/ingest", "/v1/snapshot"):
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
            connection = open_storage(self.server.db_path, getattr(self.server, "postgres_dsn", None))
            try:
                result = (ingest(connection, payload, authorized_host) if self.path == "/v1/ingest"
                          else store_snapshot(connection, payload, authorized_host))
            finally:
                connection.close()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._respond(400, {"error": str(exc)})
            return
        self._respond(200, result)

    def _import_backtest(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= MAX_BODY_BYTES + 65536:
                raise ValueError("Upload exceeds the 4 MB file limit")
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data;"):
                raise ValueError("Expected a file upload")
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + self.rfile.read(length))
            fields = {}
            filename = ""
            content = None
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    filename = part.get_filename() or ""
                    content = part.get_payload(decode=True)
                elif name in ("strategy", "currency", "starting_capital", "csrf_token"):
                    fields[name] = (part.get_payload(decode=True) or b"").decode("utf-8")
            if not hmac.compare_digest(fields.get("csrf_token", ""), self.server.csrf_token):
                self._respond(403, {"error": "invalid form token"})
                return
            if content is None:
                raise ValueError("Choose a backtest file")
            connection = open_storage(self.server.db_path, getattr(self.server, "postgres_dsn", None))
            try:
                import_backtest(connection, strategy=fields.get("strategy", ""),
                                currency=fields.get("currency", ""),
                                starting_capital=fields.get("starting_capital", ""),
                                file_name=filename, content=content)
            finally:
                connection.close()
        except (ValueError, UnicodeDecodeError, UnicodeEncodeError) as exc:
            connection = open_storage(self.server.db_path, getattr(self.server, "postgres_dsn", None))
            try:
                page = render_backtest_lab(connection, {}, str(exc), self.server.csrf_token)
            finally:
                connection.close()
            self._respond_html(400, page)
            return
        self.send_response(303)
        self.send_header("Location", "/backtests?imported=1")
        self.send_header("Content-Length", "0")
        self.end_headers()
