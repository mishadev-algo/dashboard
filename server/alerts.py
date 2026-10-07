"""Persistent health alerts and the optional Telegram worker."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.cadence import STALE_AFTER_SECONDS
from .ingest import open_storage


OFFLINE_AFTER = timedelta(seconds=STALE_AFTER_SECONDS)
RETRY_AFTER = timedelta(seconds=60)
COOLDOWN = timedelta(minutes=30)
PROBE_STALE_AFTER = timedelta(seconds=STALE_AFTER_SECONDS)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _elapsed(now: datetime, value: str | None, duration: timedelta) -> bool:
    then = _parse(value)
    return then is None or now - then >= duration


def _observations(connection: sqlite3.Connection, now: datetime) -> tuple[dict[tuple[str, str, str], str], set[str], set[tuple[str, str, str]], dict[str, set[str]]]:
    desired: dict[tuple[str, str, str], str] = {}
    offline_hosts: set[str] = set()
    held: set[tuple[str, str, str]] = set()
    expected_targets: dict[str, set[str]] = {}
    rows = connection.execute(
        "SELECT h.host_id,h.last_heartbeat_utc,c.payload_json FROM hosts h "
        "LEFT JOIN collector_heartbeats c ON c.heartbeat_id=("
        "SELECT MAX(heartbeat_id) FROM collector_heartbeats WHERE host_id=h.host_id)"
    ).fetchall()
    for host_id, last_seen, payload_json in rows:
        seen = _parse(last_seen)
        if seen is None or now - seen > OFFLINE_AFTER:
            offline_hosts.add(host_id)
            desired[(host_id, "collector_offline", "")] = f"Collector {host_id} has stopped reporting."
            continue
        try:
            state = json.loads(payload_json) if payload_json else {}
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict) or not state.get("coverage_configured"):
            continue
        expected = {path.casefold(): path for path in state.get("expected", []) if isinstance(path, str)}
        expected_targets[host_id] = set(expected)
        missing = {path.casefold() for path in state.get("missing", []) if isinstance(path, str)}
        terminals = {
            item["data_path"].casefold(): item for item in state.get("terminals", [])
            if isinstance(item, dict) and isinstance(item.get("data_path"), str)
        }
        for key, path in expected.items():
            broker_key = (host_id, "broker_disconnected", key)
            held.add(broker_key)
            terminal_key = (host_id, "terminal_stopped", key)
            terminal = terminals.get(key, {})
            process = terminal.get("process") if isinstance(terminal.get("process"), dict) else {}
            process_state = process.get("state")
            tid = terminal.get("terminal_id")
            probe = connection.execute(
                "SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?",
                (host_id, tid),
            ).fetchone() if tid else None
            probe_status = {}
            if probe:
                received = _parse(probe[0])
                if received and timedelta(0) <= now - received <= PROBE_STALE_AFTER:
                    try:
                        probe_status = json.loads(probe[1])
                    except (TypeError, json.JSONDecodeError):
                        pass
            if key in missing:
                desired[(host_id, "folder_missing", key)] = f"Expected MT5 folder missing on {host_id}: {path}"
            elif process_state == "stopped" or (process_state != "running" and probe_status.get("state") == "stopped"):
                desired[terminal_key] = f"MT5 process stopped on {host_id}: {path}"
            elif process_state == "running":
                if probe_status.get("state") == "ok" and probe_status.get("connected") is False:
                    desired[broker_key] = f"Broker disconnected on {host_id}: {path}"
                    held.discard(broker_key)
                elif probe_status.get("state") == "ok" and probe_status.get("connected") is True:
                    held.discard(broker_key)
            else:
                held.add(terminal_key)  # Unknown process state cannot prove recovery.
    return desired, offline_hosts, held, expected_targets


def evaluate(
    connection: sqlite3.Connection, send: Callable[[str], None], now: datetime | None = None,
    cooldown: timedelta = COOLDOWN, retry_after: timedelta = RETRY_AFTER,
    report: Callable[[str], None] | None = None,
) -> None:
    """Evaluate current states; send is an injectable function accepting one text message."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    desired, offline_hosts, held, expected_targets = _observations(connection, now)
    existing = {
        (row[0], row[1], row[2]): row
        for row in connection.execute(
            "SELECT host_id,alert_type,target,state,detail,changed_utc,last_attempt_utc,"
            "last_sent_utc,active_notified FROM alerts"
        )
    }
    for key, detail in desired.items():
        row = existing.get(key)
        if row is None or row[3] == "resolved":
            with connection:
                connection.execute(
                    "INSERT INTO alerts (host_id,alert_type,target,state,detail,changed_utc) "
                    "VALUES (?,?,?,'active',?,?) ON CONFLICT(host_id,alert_type,target) "
                    "DO UPDATE SET state='active',detail=excluded.detail,changed_utc=excluded.changed_utc,"
                    "last_attempt_utc=NULL,last_sent_utc=NULL,active_notified=0,last_error=NULL",
                    (*key, detail, stamp),
                )
            row = None
        elif row[3] == "recovering":
            with connection:
                connection.execute(
                    "UPDATE alerts SET state='active',detail=?,changed_utc=? "
                    "WHERE host_id=? AND alert_type=? AND target=?", (detail, stamp, *key)
                )
        elif row[4] != detail:
            with connection:
                connection.execute(
                    "UPDATE alerts SET detail=? WHERE host_id=? AND alert_type=? AND target=?", (detail, *key)
                )
        attempt = row[6] if row else None
        last_sent = row[7] if row else None
        notified = bool(row[8]) if row else False
        if _elapsed(now, attempt, retry_after) and (not notified or _elapsed(now, last_sent, cooldown)):
            _send(connection, send, key, detail, stamp, active=True, report=report)

    for key, row in existing.items():
        if key in desired or row[3] == "resolved":
            continue
        if (key[1] in ("terminal_stopped", "folder_missing", "broker_disconnected")
                and key[0] in expected_targets and key[2] not in expected_targets[key[0]]):
            with connection:
                connection.execute(
                    "UPDATE alerts SET state='resolved',changed_utc=?,last_attempt_utc=NULL,"
                    "active_notified=0,last_error=NULL WHERE host_id=? AND alert_type=? AND target=?",
                    (stamp, *key),
                )
            if report:
                report(f"{key[0]} {key[1]}: monitoring ended after inventory change")
            continue
        if key in held:
            continue
        if key[0] in offline_hosts and key[1] != "collector_offline":
            continue  # Terminal state is unknown while its collector is offline.
        if row[3] == "active":
            new_state = "recovering" if row[8] else "resolved"
            with connection:
                connection.execute(
                    "UPDATE alerts SET state=?,changed_utc=?,last_attempt_utc=NULL "
                    "WHERE host_id=? AND alert_type=? AND target=?", (new_state, stamp, *key)
                )
            if new_state == "resolved":
                continue
        if _elapsed(now, row[6] if row[3] == "recovering" else None, retry_after):
            _send(connection, send, key, f"Recovered: {row[4]}", stamp, active=False, report=report)


def _send(
    connection: sqlite3.Connection, send: Callable[[str], None], key: tuple[str, str, str],
    message: str, stamp: str, active: bool, report: Callable[[str], None] | None = None,
) -> None:
    with connection:
        connection.execute(
            "UPDATE alerts SET last_attempt_utc=? WHERE host_id=? AND alert_type=? AND target=?",
            (stamp, *key),
        )
    try:
        send(message)
    except Exception as exc:
        with connection:
            connection.execute(
                "UPDATE alerts SET last_error=? WHERE host_id=? AND alert_type=? AND target=?",
                (str(exc)[:200], *key),
            )
        if report:
            report(f"{key[0]} {key[1]}: delivery failed ({str(exc)[:200]})")
        return
    with connection:
        connection.execute(
            "UPDATE alerts SET state=?,last_sent_utc=?,active_notified=?,last_error=NULL "
            "WHERE host_id=? AND alert_type=? AND target=?",
            ("active" if active else "resolved", stamp, 1 if active else 0, *key),
        )
    if report:
        report(f"{key[0]} {key[1]}: {'alert' if active else 'recovery'} sent")


def send_close_notifications(
    connection: sqlite3.Connection, send: Callable[[str], None],
    now: datetime | None = None, report: Callable[[str], None] | None = None,
) -> None:
    """Send each new exit deal once; failed sends remain queued for retry."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    rows = connection.execute(
        "SELECT n.server,n.login,n.ticket,n.last_attempt_utc,d.time_utc,d.position_id,"
        "d.symbol,d.strategy,d.volume,d.profit,d.commission,d.swap,d.fee,a.currency "
        "FROM close_notifications n JOIN deals d USING (server,login,ticket) "
        "JOIN accounts a USING (server,login) WHERE n.state='pending' "
        "ORDER BY d.time_utc,n.ticket LIMIT 50"
    ).fetchall()
    for server, login, ticket, attempt, closed_at, position_id, symbol, strategy, volume, profit, commission, swap, fee, currency in rows:
        if not _elapsed(now, attempt, RETRY_AFTER):
            continue
        result = sum((Decimal(value) for value in (profit, commission, swap, fee)), Decimal(0))
        message = (f"Position close: {server} / {login}, {symbol}, strategy {strategy}; "
                   f"position {position_id}, deal {ticket}, volume {volume:g}, "
                   f"time {closed_at}, deal PnL {result:.2f} {currency}.")
        with connection:
            connection.execute(
                "UPDATE close_notifications SET last_attempt_utc=? WHERE server=? AND login=? AND ticket=?",
                (stamp, server, login, ticket),
            )
        try:
            send(message)
        except Exception as exc:
            with connection:
                connection.execute(
                    "UPDATE close_notifications SET last_error=? WHERE server=? AND login=? AND ticket=?",
                    (str(exc)[:200], server, login, ticket),
                )
            if report:
                report(f"close notification {server}/{login}/{ticket}: delivery failed")
            continue
        with connection:
            connection.execute(
                "UPDATE close_notifications SET state='sent',sent_utc=?,last_error=NULL "
                "WHERE server=? AND login=? AND ticket=?",
                (stamp, server, login, ticket),
            )
        if report:
            report(f"close notification {server}/{login}/{ticket}: sent")


class TelegramSender:
    def __init__(self, token: str, chat_id: str) -> None:
        if not token or not chat_id or "/" in token:
            raise ValueError("Telegram bot token and chat ID are required")
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def __call__(self, message: str) -> None:
        body = json.dumps({"chat_id": self.chat_id, "text": message[:4096]}).encode("utf-8")
        request = Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as exc:
            try:
                description = json.load(exc).get("description", "")
            except (OSError, ValueError, AttributeError):
                description = ""
            reason = f": {description[:150]}" if isinstance(description, str) and description else ""
            raise RuntimeError(f"Telegram HTTP {exc.code}{reason}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError("Telegram request failed") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            description = result.get("description", "") if isinstance(result, dict) else ""
            reason = f": {description[:150]}" if isinstance(description, str) and description else ""
            raise RuntimeError(f"Telegram rejected the message{reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard Telegram health alert worker")
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--db", type=Path)
    storage.add_argument("--postgres", action="store_true", help="Use DASHBOARD_POSTGRES_DSN")
    parser.add_argument("--interval", type=float, default=10.0)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("interval must be positive")
    try:
        sender = TelegramSender(os.environ["DASHBOARD_TELEGRAM_BOT_TOKEN"], os.environ["DASHBOARD_TELEGRAM_CHAT_ID"])
    except (KeyError, ValueError):
        parser.error("set DASHBOARD_TELEGRAM_BOT_TOKEN and DASHBOARD_TELEGRAM_CHAT_ID")
    postgres_dsn = os.environ.get("DASHBOARD_POSTGRES_DSN") if args.postgres else None
    if args.postgres and not postgres_dsn:
        parser.error("set DASHBOARD_POSTGRES_DSN for PostgreSQL storage")
    connection = open_storage(args.db, postgres_dsn)
    print(f"alert worker started; database={'PostgreSQL' if args.postgres else args.db}; "
          f"interval={args.interval:g}s", flush=True)
    next_status = time.monotonic() + 60
    try:
        while True:
            evaluate(connection, sender, report=lambda line: print(line, flush=True))
            send_close_notifications(connection, sender, report=lambda line: print(line, flush=True))
            if time.monotonic() >= next_status:
                count = connection.execute("SELECT COUNT(*) FROM alerts WHERE state!='resolved'").fetchone()[0]
                print(f"alert worker running; open alerts={count}", flush=True)
                next_status = time.monotonic() + 60
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        connection.close()


if __name__ == "__main__":
    main()
