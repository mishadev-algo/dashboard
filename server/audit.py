"""Read-only alpha trial report from the central SQLite database."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.cadence import STALE_AFTER_SECONDS
from shared.sqlite import open_readonly

from .ingest import open_storage


COLLECTOR_STALE = timedelta(seconds=STALE_AFTER_SECONDS)
PROBE_STALE = timedelta(seconds=STALE_AFTER_SECONDS)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None


def _fresh(value: str | None, now: datetime, limit: timedelta) -> bool:
    parsed = _parse(value)
    return parsed is not None and timedelta(0) <= now - parsed <= limit


def build_report(connection: sqlite3.Connection, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    report: dict = {
        "generated_utc": now.isoformat(), "hosts": [], "accounts": [],
        "findings": [],
        "live_checks_still_required": [
            "real MT5 midnight rollover", "API outage and replay with central count comparison",
            "Journal and Experts sample comparison with MT5", "two-account MT5 History reconciliation",
            "Telegram failure and recovery evidence",
        ],
    }
    hosts = connection.execute(
        "SELECT h.host_id,h.last_heartbeat_utc,c.payload_json FROM hosts h "
        "LEFT JOIN collector_heartbeats c ON c.heartbeat_id=("
        "SELECT MAX(heartbeat_id) FROM collector_heartbeats WHERE host_id=h.host_id) "
        "ORDER BY h.host_id"
    ).fetchall()
    if not hosts:
        report["findings"].append("No collector hosts have reported")
    for host_id, last_seen, payload_json in hosts:
        try:
            state = json.loads(payload_json) if payload_json else {}
        except (TypeError, json.JSONDecodeError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        online = _fresh(last_seen, now, COLLECTOR_STALE)
        expected = {path.casefold() for path in state.get("expected", []) if isinstance(path, str)}
        archived = {path.casefold() for path in state.get("archived", []) if isinstance(path, str)}
        terminals = {
            item["data_path"].casefold(): item for item in state.get("terminals", [])
            if isinstance(item, dict) and isinstance(item.get("data_path"), str)
        }
        discovered = set(terminals)
        missing = sorted(expected - discovered)
        unknown = sorted(discovered - expected - archived) if state.get("coverage_configured") else []
        host_report = {
            "host_id": host_id, "collector_online": online, "last_heartbeat_utc": last_seen,
            "coverage_configured": bool(state.get("coverage_configured")),
            "expected": len(expected), "archived": len(archived), "discovered": len(discovered),
            "missing": missing, "unknown": unknown,
            "pending_at_scan": state.get("pending_count"), "terminals": [],
        }
        if not online:
            report["findings"].append(f"{host_id}: collector offline or stale")
        if not host_report["coverage_configured"]:
            report["findings"].append(f"{host_id}: expected terminal inventory is not configured")
        if type(state.get("pending_count")) is int and state["pending_count"] > 0:
            report["findings"].append(f"{host_id}: {state['pending_count']} log lines pending upload")
        for path in missing:
            report["findings"].append(f"{host_id}: expected folder missing: {path}")
        for path in unknown:
            report["findings"].append(f"{host_id}: unknown folder discovered: {path}")
        for path, item in sorted(terminals.items()):
            tid = item.get("terminal_id")
            counts = dict(connection.execute(
                "SELECT stream,COUNT(*) FROM log_events WHERE host_id=? AND terminal_id=? GROUP BY stream",
                (host_id, tid),
            ))
            process = item.get("process") if isinstance(item.get("process"), dict) else {}
            process_state = process.get("state", "unknown") if online else "unknown"
            status_row = connection.execute(
                "SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?",
                (host_id, tid),
            ).fetchone()
            probe_state = "unknown"
            broker = autotrading = None
            if online and process_state == "running" and status_row and _fresh(status_row[0], now, PROBE_STALE):
                try:
                    status = json.loads(status_row[1])
                except (TypeError, json.JSONDecodeError):
                    status = {}
                probe_state = status.get("state", "unknown")
                if probe_state == "ok":
                    broker = status.get("connected")
                    autotrading = status.get("autotrading")
            terminal_report = {
                "terminal_id": tid, "data_path": item["data_path"],
                "inventory_state": "expected" if path in expected else "archived" if path in archived else "unknown",
                "process": process_state, "journal_events": counts.get("journal", 0),
                "experts_events": counts.get("experts", 0), "probe": probe_state,
                "broker_connected": broker, "autotrading": autotrading,
            }
            host_report["terminals"].append(terminal_report)
            if path in expected and online:
                if process_state == "stopped":
                    report["findings"].append(f"{host_id}: MT5 process stopped: {path}")
                if process_state == "running" and probe_state == "account_mismatch":
                    report["findings"].append(f"{host_id}: MT5 account does not match configured login/server: {path}")
                elif process_state == "running" and probe_state != "ok":
                    report["findings"].append(f"{host_id}: broker/account probe unavailable: {path}")
                if broker is False:
                    report["findings"].append(f"{host_id}: broker disconnected: {path}")
                if autotrading is False:
                    report["findings"].append(f"{host_id}: AutoTrading disabled: {path}")
                if not counts.get("journal") or not counts.get("experts"):
                    report["findings"].append(f"{host_id}: inspect missing log stream history: {path}")
        report["hosts"].append(host_report)
    for server, login, currency, host_id, tid, latest in connection.execute(
        "SELECT server,login,currency,host_id,terminal_id,latest_snapshot_utc "
        "FROM accounts ORDER BY server,login"
    ):
        status_row = connection.execute(
            "SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?",
            (host_id, tid),
        ).fetchone()
        try:
            complete_latest = bool(status_row and status_row[0] == latest and
                                   json.loads(status_row[1]).get("data_complete") is True)
        except (TypeError, json.JSONDecodeError):
            complete_latest = False
        fresh = _fresh(latest, now, PROBE_STALE) and complete_latest
        report["accounts"].append({
            "server": server, "login": login, "currency": currency, "host_id": host_id,
            "terminal_id": tid, "last_complete_snapshot_utc": latest, "snapshot_fresh": fresh,
            "open_positions": connection.execute(
                "SELECT COUNT(*) FROM positions WHERE server=? AND login=?", (server, login),
            ).fetchone()[0],
        })
        if not fresh:
            report["findings"].append(f"{server}/{login}: account snapshot stale")
    if not report["accounts"]:
        report["findings"].append("No verified account snapshots have arrived")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MT5 alpha trial audit")
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--db", type=Path)
    storage.add_argument("--postgres", action="store_true", help="Use DASHBOARD_POSTGRES_DSN")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path instead of stdout")
    parser.add_argument("--strict", action="store_true", help="Exit with code 1 if findings are present")
    args = parser.parse_args()
    if args.db and not args.db.is_file():
        parser.error("central database does not exist")
    postgres_dsn = os.environ.get("DASHBOARD_POSTGRES_DSN") if args.postgres else None
    if args.postgres and not postgres_dsn:
        parser.error("set DASHBOARD_POSTGRES_DSN for PostgreSQL storage")
    connection = (open_storage(None, postgres_dsn) if args.postgres else open_readonly(args.db))
    try:
        report = build_report(connection)
    finally:
        connection.close()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if args.strict and report["findings"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
