"""Validated terminal and account snapshots received from Windows workers."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, datetime, timezone

from shared.timezones import day_zone
from shared.paths import same_windows_path


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS terminal_status (
            host_id TEXT NOT NULL, terminal_id TEXT NOT NULL, data_path TEXT NOT NULL,
            observed_at_utc TEXT NOT NULL, received_utc TEXT NOT NULL, status_json TEXT NOT NULL,
            PRIMARY KEY (host_id, terminal_id)
        );
        CREATE TABLE IF NOT EXISTS accounts (
            server TEXT NOT NULL, login INTEGER NOT NULL, currency TEXT NOT NULL,
            day_timezone TEXT NOT NULL, host_id TEXT NOT NULL, terminal_id TEXT NOT NULL,
            latest_snapshot_utc TEXT NOT NULL, history_start_day TEXT NOT NULL, balance TEXT,
            PRIMARY KEY (server, login)
        );
        CREATE TABLE IF NOT EXISTS positions (
            server TEXT NOT NULL, login INTEGER NOT NULL, ticket INTEGER NOT NULL,
            symbol TEXT NOT NULL, type INTEGER NOT NULL, magic INTEGER NOT NULL,
            strategy TEXT NOT NULL, volume REAL NOT NULL, price_open REAL NOT NULL,
            price_current REAL NOT NULL, profit TEXT NOT NULL, swap TEXT NOT NULL,
            comment TEXT NOT NULL, PRIMARY KEY (server, login, ticket)
        );
        CREATE TABLE IF NOT EXISTS deals (
            server TEXT NOT NULL, login INTEGER NOT NULL, ticket INTEGER NOT NULL,
            time_utc TEXT NOT NULL, day_local TEXT NOT NULL, type INTEGER NOT NULL,
            kind TEXT NOT NULL, entry INTEGER NOT NULL, position_id INTEGER NOT NULL,
            symbol TEXT NOT NULL, magic INTEGER NOT NULL, strategy TEXT NOT NULL,
            comment TEXT NOT NULL, profit TEXT NOT NULL, commission TEXT NOT NULL,
            swap TEXT NOT NULL, fee TEXT NOT NULL, volume REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (server, login, ticket)
        );
        CREATE INDEX IF NOT EXISTS idx_deals_account_day ON deals(server, login, day_local);
        CREATE TABLE IF NOT EXISTS close_alert_baselines (
            server TEXT NOT NULL, login INTEGER NOT NULL, initialized_utc TEXT NOT NULL,
            PRIMARY KEY (server, login)
        );
        CREATE TABLE IF NOT EXISTS close_notifications (
            server TEXT NOT NULL, login INTEGER NOT NULL, ticket INTEGER NOT NULL,
            state TEXT NOT NULL, created_utc TEXT NOT NULL, last_attempt_utc TEXT,
            sent_utc TEXT, last_error TEXT,
            PRIMARY KEY (server, login, ticket)
        );
    """)
    if "volume" not in {row[1] for row in connection.execute("PRAGMA table_info(deals)")}:
        connection.execute("ALTER TABLE deals ADD COLUMN volume REAL NOT NULL DEFAULT 0")
    if "balance" not in {row[1] for row in connection.execute("PRAGMA table_info(accounts)")}:
        connection.execute("ALTER TABLE accounts ADD COLUMN balance TEXT")
    # Upgraded databases already contain history. Baseline those deals before the
    # first new snapshot so only closes observed after deployment are notified.
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO close_notifications (server,login,ticket,state,created_utc) "
            "SELECT d.server,d.login,d.ticket,'baseline',a.latest_snapshot_utc "
            "FROM deals d JOIN accounts a USING (server,login) "
            "WHERE d.kind='trade' AND d.entry IN (1,2,3) "
            "AND NOT EXISTS (SELECT 1 FROM close_alert_baselines b "
            "WHERE b.server=d.server AND b.login=d.login)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO close_alert_baselines "
            "SELECT server,login,latest_snapshot_utc FROM accounts"
        )


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {field}")
    return value


def _text(value: object, field: str, limit: int = 500) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"invalid {field}")
    return value


def _number(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"invalid {field}")
    return str(value)


def _time(value: object, field: str) -> str:
    stamp = _text(value, field, 50)
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid {field}") from None
    if parsed.tzinfo is None:
        raise ValueError(f"invalid {field}")
    return stamp


def _date(value: object, field: str) -> str:
    stamp = _text(value, field, 10)
    try:
        date.fromisoformat(stamp)
    except ValueError:
        raise ValueError(f"invalid {field}") from None
    return stamp


def store_snapshot(connection: sqlite3.Connection, payload: object, authorized_host: str,
                   historical_backfill: bool = False) -> dict:
    if not isinstance(payload, dict) or payload.get("host_id") != authorized_host:
        raise ValueError("host_id does not match token")
    terminal_id = _text(payload.get("terminal_id"), "terminal_id", 100)
    data_path = _text(payload.get("data_path"), "data_path", 1000)
    observed = _time(payload.get("observed_at_utc"), "observed_at_utc")
    expected_login = _integer(payload.get("expected_login"), "expected_login")
    if expected_login == 0:
        raise ValueError("invalid expected_login")
    expected_server = _text(payload.get("expected_server"), "expected_server", 200)
    day_timezone = _text(payload.get("day_timezone"), "day_timezone", 100)
    day_zone(day_timezone)
    status = payload.get("status")
    if not isinstance(status, dict) or status.get("state") not in ("ok", "unknown", "stopped", "account_mismatch"):
        raise ValueError("invalid status")
    for flag in ("connected", "autotrading"):
        if flag in status and type(status[flag]) is not bool:
            raise ValueError(f"invalid {flag}")
    if "reason" in status:
        _text(status["reason"], "reason", 200)
    if "build" in status:
        _integer(status["build"], "build")
    complete = status.get("data_complete") is True
    if "data_complete" in status and type(status["data_complete"]) is not bool:
        raise ValueError("invalid data_complete")
    if complete and (status.get("state") != "ok" or status.get("connected") is not True):
        raise ValueError("account data requires a connected, verified terminal")
    if not complete and any(key in payload for key in ("account", "positions", "deals", "strategies", "history_start_day")):
        raise ValueError("incomplete account snapshot contains data")
    known = connection.execute(
        "SELECT data_path FROM terminals WHERE host_id=? AND terminal_id=?",
        (authorized_host, terminal_id),
    ).fetchone()
    if known is None or not same_windows_path(known[0], data_path):
        raise ValueError("terminal is not registered under this host/path")
    account = None
    positions: list[tuple] = []
    deals: list[tuple] = []
    if complete:
        account = payload.get("account")
        if not isinstance(account, dict) or account.get("login") != expected_login or account.get("server") != expected_server:
            raise ValueError("account does not match configured login/server")
        currency = _text(account.get("currency"), "currency", 20)
        balance = _number(account["balance"], "account balance") if "balance" in account else None
        history_start_day = _date(payload.get("history_start_day"), "history_start_day")
        if not currency:
            raise ValueError("currency is required")
        raw_positions = payload.get("positions")
        raw_deals = payload.get("deals")
        strategies = payload.get("strategies", {})
        if not isinstance(raw_positions, list) or len(raw_positions) > 2000:
            raise ValueError("invalid positions")
        if not isinstance(raw_deals, list) or len(raw_deals) > 10000:
            raise ValueError("invalid deals")
        if not isinstance(strategies, dict) or len(strategies) > 1000:
            raise ValueError("invalid strategies")
        strategy_map = {}
        for key, value in strategies.items():
            if not isinstance(key, str) or not key.isdigit():
                raise ValueError("invalid strategy magic")
            strategy_map[int(key)] = _text(value, "strategy", 100)
        for position in raw_positions:
            if not isinstance(position, dict):
                raise ValueError("invalid position")
            magic = _integer(position.get("magic"), "position magic")
            positions.append((
                expected_server, expected_login, _integer(position.get("ticket"), "position ticket"),
                _text(position.get("symbol"), "position symbol", 100),
                _integer(position.get("type"), "position type"), magic,
                strategy_map.get(magic, "unmapped"),
                float(_number(position.get("volume"), "volume")),
                float(_number(position.get("price_open"), "price_open")),
                float(_number(position.get("price_current"), "price_current")),
                _number(position.get("profit"), "position profit"),
                _number(position.get("swap"), "position swap"),
                _text(position.get("comment"), "position comment", 500),
            ))
        for deal in raw_deals:
            if not isinstance(deal, dict) or deal.get("kind") not in ("trade", "commission", "cash", "other"):
                raise ValueError("invalid deal")
            magic = _integer(deal.get("magic"), "deal magic")
            kind = deal["kind"]
            deals.append((
                expected_server, expected_login, _integer(deal.get("ticket"), "deal ticket"),
                _time(deal.get("time_utc"), "deal time"), _date(deal.get("day_local"), "deal day"),
                _integer(deal.get("type"), "deal type"), kind,
                _integer(deal.get("entry"), "deal entry"),
                _integer(deal.get("position_id"), "position_id"),
                _text(deal.get("symbol"), "deal symbol", 100), magic,
                strategy_map.get(magic, "unmapped") if kind == "trade" else "unmapped",
                _text(deal.get("comment"), "deal comment", 500),
                *(_number(deal.get(field), field) for field in ("profit", "commission", "swap", "fee")),
                float(_number(deal.get("volume", 0), "deal volume")),
            ))
    received = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.execute(
            "INSERT INTO terminal_status VALUES (?,?,?,?,?,?) ON CONFLICT(host_id,terminal_id) "
            "DO UPDATE SET data_path=excluded.data_path,observed_at_utc=excluded.observed_at_utc,"
            "received_utc=excluded.received_utc,status_json=excluded.status_json",
            (authorized_host, terminal_id, data_path, observed, received, json.dumps(status)),
        )
        if complete:
            baseline_exists = connection.execute(
                "SELECT 1 FROM close_alert_baselines WHERE server=? AND login=?",
                (expected_server, expected_login),
            ).fetchone() is not None
            connection.execute(
                "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(server,login) DO UPDATE SET "
                "currency=excluded.currency,day_timezone=excluded.day_timezone,host_id=excluded.host_id,"
                "terminal_id=excluded.terminal_id,latest_snapshot_utc=excluded.latest_snapshot_utc,"
                "history_start_day=excluded.history_start_day,"
                "balance=COALESCE(excluded.balance,accounts.balance)",
                (expected_server, expected_login, currency, day_timezone, authorized_host, terminal_id,
                 received, history_start_day, balance),
            )
            connection.execute("DELETE FROM positions WHERE server=? AND login=?", (expected_server, expected_login))
            connection.executemany("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", positions)
            connection.executemany(
                "INSERT INTO deals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(server,login,ticket) DO UPDATE SET "
                "time_utc=excluded.time_utc,day_local=excluded.day_local,type=excluded.type,kind=excluded.kind,"
                "entry=excluded.entry,position_id=excluded.position_id,symbol=excluded.symbol,magic=excluded.magic,"
                "strategy=excluded.strategy,comment=excluded.comment,profit=excluded.profit,"
                "commission=excluded.commission,swap=excluded.swap,fee=excluded.fee,volume=excluded.volume",
                deals,
            )
            close_tickets = [(expected_server, expected_login, row[2],
                              "pending" if baseline_exists and not historical_backfill else "baseline", received)
                             for row in deals if row[6] == "trade" and row[7] in (1, 2, 3)]
            connection.executemany(
                "INSERT INTO close_notifications (server,login,ticket,state,created_utc) "
                "VALUES (?,?,?,?,?) ON CONFLICT(server,login,ticket) DO NOTHING", close_tickets,
            )
            connection.execute(
                "INSERT INTO close_alert_baselines VALUES (?,?,?) "
                "ON CONFLICT(server,login) DO NOTHING",
                (expected_server, expected_login, received),
            )
    return {"terminal_id": terminal_id, "positions": len(positions), "deals": len(deals)}
