from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.network import server_origin

from .core import RunResult, terminal_id
from .ea_probe import read_probe
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
            "ea_probe": read_probe(path),
        })
    return {
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "terminals": terminals,
        "missing": list(result.missing),
        "unknown": list(result.unknown),
        "expected": list(result.expected),
        "archived": list(result.archived),
        "coverage_configured": result.coverage_configured,
        "pending_count": pending_count(connection),
    }


class RemoteUploader:
    def __init__(
        self, connection: sqlite3.Connection, server_url: str, host_id: str, token: str,
        batch_size: int = 200, timeout: float = 10.0,
    ) -> None:
        origin = server_origin(server_url)
        if not token:
            raise ValueError("collector token is required")
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        self.connection = connection
        self.url = origin + "/v1/ingest"
        self.host_id = host_id
        self.token = token
        self.batch_size = batch_size
        self.timeout = timeout

    def upload(self, state: dict | None) -> int:
        delivered = 0
        heartbeat_pending = state is not None
        remaining = pending_count(self.connection)
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
            if not events and not heartbeat_pending:
                return delivered
            while True:
                payload = {"host_id": self.host_id, "events": events}
                include_heartbeat = heartbeat_pending and len(events) == remaining
                if include_heartbeat:
                    payload["heartbeat"] = {**state, "pending_count": 0}
                body = json.dumps(
                    payload,
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
            except HTTPError as exc:
                exc.close()
                raise RemoteUploadError(str(exc)) from exc
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                raise RemoteUploadError(str(exc)) from exc
            expected_ids = {event["event_id"] for event in events}
            received_ids = acknowledgement.get("acknowledged") if isinstance(acknowledgement, dict) else None
            if not isinstance(received_ids, list) or set(received_ids) != expected_ids or len(received_ids) != len(events):
                raise RemoteUploadError("server acknowledgement does not match sent events")
            if include_heartbeat:
                heartbeat_pending = False
            with self.connection:
                self.connection.executemany(
                    "UPDATE log_events SET delivered_utc=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE host_id=? AND terminal_id=? AND stream=? AND file_name=? "
                    "AND generation=? AND byte_offset=? AND delivered_utc IS NULL",
                    [tuple(event[field] for field in EVENT_KEY_FIELDS) for event in events],
                )
            delivered += len(events)
            remaining -= len(events)
            if len(events) == len(rows) and len(rows) < self.batch_size:
                return delivered
