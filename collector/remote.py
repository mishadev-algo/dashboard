from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .core import RunResult, terminal_id
from .protocol import EVENT_KEY_FIELDS, event_id


class RemoteUploadError(Exception):
    pass


MAX_BATCH_BYTES = 3 * 1024 * 1024


def pending_count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM log_events WHERE delivered_utc IS NULL").fetchone()[0]


def heartbeat(
    connection: sqlite3.Connection, host_id: str, result: RunResult,
    process_states: dict[str, dict] | None = None,
) -> dict:
    terminals = []
    process_states = process_states or {}
    for path in result.paths:
        tid = terminal_id(host_id, path)
        streams = {}
        for stream, file_name, generation, byte_offset in connection.execute(
            "SELECT stream, file_name, generation, byte_offset FROM file_cursors "
            "WHERE terminal_id=? ORDER BY file_name DESC", (tid,)
        ):
            streams.setdefault(stream, {
                "file_name": file_name, "generation": generation, "byte_offset": byte_offset
            })
        terminals.append({
            "terminal_id": tid, "data_path": str(path), "streams": streams,
            "process": process_states.get(str(path), {"state": "unknown"}),
        })
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "terminals": terminals,
        "missing": list(result.missing),
        "unknown": list(result.unknown),
        "coverage_configured": result.coverage_configured,
        "pending_count": pending_count(connection),
    }


class RemoteUploader:
    def __init__(
        self, connection: sqlite3.Connection, server_url: str, host_id: str, token: str,
        batch_size: int = 200, timeout: float = 10.0,
    ) -> None:
        parsed = urlsplit(server_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("server-url must be an HTTP(S) origin")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("server-url must use HTTPS except for localhost")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("server-url must be an origin without a path or query")
        if not token:
            raise ValueError("collector token is required")
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        self.connection = connection
        self.url = server_url.rstrip("/") + "/v1/ingest"
        self.host_id = host_id
        self.token = token
        self.batch_size = batch_size
        self.timeout = timeout

    def upload(self, state: dict) -> int:
        delivered = 0
        while True:
            rows = self.connection.execute(
                "SELECT e.host_id, e.terminal_id, e.stream, e.file_name, e.generation, "
                "e.byte_offset, e.raw_line, t.data_path FROM log_events e "
                "JOIN terminals t USING (terminal_id) WHERE e.delivered_utc IS NULL "
                "ORDER BY e.rowid LIMIT ?", (self.batch_size,)
            ).fetchall()
            events = []
            for host_id, tid, stream, file_name, generation, offset, raw_line, data_path in rows:
                event = {
                    "host_id": host_id, "terminal_id": tid, "stream": stream,
                    "file_name": file_name, "generation": generation,
                    "byte_offset": offset, "raw_line": raw_line, "data_path": data_path,
                }
                event["event_id"] = event_id(event)
                events.append(event)
            while True:
                body = json.dumps(
                    {"host_id": self.host_id, "events": events, "heartbeat": state},
                    ensure_ascii=False,
                ).encode("utf-8")
                if len(body) <= MAX_BATCH_BYTES:
                    break
                if len(events) <= 1:
                    raise RemoteUploadError("upload payload exceeds 3 MiB")
                events.pop()
            request = Request(
                self.url, data=body, method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    acknowledgement = json.load(response)
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                raise RemoteUploadError(str(exc)) from exc
            expected_ids = {event["event_id"] for event in events}
            received_ids = acknowledgement.get("acknowledged") if isinstance(acknowledgement, dict) else None
            if not isinstance(received_ids, list) or set(received_ids) != expected_ids or len(received_ids) != len(events):
                raise RemoteUploadError("server acknowledgement does not match sent events")
            with self.connection:
                self.connection.executemany(
                    "UPDATE log_events SET delivered_utc=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE host_id=? AND terminal_id=? AND stream=? AND file_name=? "
                    "AND generation=? AND byte_offset=? AND delivered_utc IS NULL",
                    [tuple(event[field] for field in EVENT_KEY_FIELDS) for event in events],
                )
            delivered += len(events)
            if len(events) == len(rows) and len(rows) < self.batch_size:
                return delivered
