"""One-terminal MetaTrader5 API probe, invoked in an isolated child process."""

from __future__ import annotations

import argparse
import json
import ntpath
from datetime import datetime, timedelta, timezone

from shared.timezones import day_zone
from shared.paths import same_windows_path

from .process import list_terminal_processes


def _number(value: object) -> float:
    number = float(value or 0)
    if not -1e100 < number < 1e100:
        raise ValueError("non-finite MT5 number")
    return number


def _deal_kind(mt5: object, deal_type: int) -> str:
    if deal_type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL):
        return "trade"
    commission_names = (
        "DEAL_TYPE_COMMISSION", "DEAL_TYPE_COMMISSION_DAILY", "DEAL_TYPE_COMMISSION_MONTHLY",
        "DEAL_TYPE_COMMISSION_AGENT_DAILY", "DEAL_TYPE_COMMISSION_AGENT_MONTHLY",
    )
    if deal_type in {getattr(mt5, name) for name in commission_names if hasattr(mt5, name)}:
        return "commission"
    if deal_type in (mt5.DEAL_TYPE_BALANCE, mt5.DEAL_TYPE_CREDIT):
        return "cash"
    return "other"


def collect(mt5: object, data_path: str, executable: str, login: int, server: str,
            day_timezone: str, history_days: int, now: datetime | None = None) -> dict:
    """Connect to one running terminal; never request login or send an order."""
    now = now or datetime.now(timezone.utc)
    result: dict = {"status": {"state": "unknown", "reason": "MT5 connection unavailable"}}
    portable = same_windows_path(data_path, ntpath.dirname(executable))
    if not mt5.initialize(executable, timeout=10000, portable=portable):
        return result
    try:
        info = mt5.terminal_info()
        if info is None or not same_windows_path(str(info.data_path), data_path) or not same_windows_path(str(info.path), ntpath.dirname(executable)):
            result["status"] = {"state": "unknown", "reason": "MT5 attached to a different terminal"}
            return result
        connected = bool(info.connected)
        autotrading = bool(info.trade_allowed)
        result["status"] = {
            "state": "ok", "connected": connected, "autotrading": autotrading,
            "build": int(info.build),
        }
        account = mt5.account_info()
        if account is None:
            result["status"]["reason"] = "MT5 account details unavailable"
            return result
        if int(account.login) != login or str(account.server) != server:
            result["status"] = {
                "state": "account_mismatch", "connected": connected,
                "autotrading": autotrading, "reason": "MT5 account differs from configured login/server",
            }
            return result
        if not connected:
            return result
        positions = mt5.positions_get()
        zone = day_zone(day_timezone)
        local_now = now.astimezone(zone)
        local_start = datetime.combine(
            local_now.date() - timedelta(days=history_days - 1), datetime.min.time(), tzinfo=zone,
        )
        deals = mt5.history_deals_get(int(local_start.timestamp()), int(now.timestamp()) + 1)
        if positions is None or deals is None:
            result["status"]["reason"] = "MT5 positions or deal history unavailable"
            return result
        if len(positions) > 2000 or len(deals) > 10000:
            result["status"]["reason"] = "MT5 snapshot exceeds supported size; reduce history_days"
            return result
        result["account"] = {"login": login, "server": server, "currency": str(account.currency),
                             "balance": _number(account.balance)}
        result["history_start_day"] = local_start.date().isoformat()
        result["positions"] = [
            {
                "ticket": int(position.ticket), "symbol": str(position.symbol),
                "type": int(position.type), "magic": int(position.magic),
                "volume": _number(position.volume), "price_open": _number(position.price_open),
                "price_current": _number(position.price_current),
                "profit": _number(position.profit), "swap": _number(position.swap),
                "comment": str(position.comment),
            }
            for position in positions
        ]
        result["deals"] = [
            {
                "ticket": int(deal.ticket), "time_utc": datetime.fromtimestamp(
                    int(getattr(deal, "time_msc", 0) or deal.time * 1000) / 1000, timezone.utc,
                ).isoformat(),
                "day_local": datetime.fromtimestamp(int(deal.time), timezone.utc).astimezone(zone).date().isoformat(),
                "type": int(deal.type), "kind": _deal_kind(mt5, int(deal.type)), "entry": int(deal.entry),
                "position_id": int(deal.position_id), "symbol": str(deal.symbol),
                "volume": _number(getattr(deal, "volume", 0)),
                "magic": int(deal.magic), "comment": str(deal.comment),
                "profit": _number(deal.profit), "commission": _number(deal.commission),
                "swap": _number(deal.swap), "fee": _number(deal.fee),
            }
            for deal in deals
        ]
        result["data_complete"] = True
        return result
    finally:
        mt5.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated read-only MT5 snapshot")
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--login", type=int, required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--day-timezone", required=True)
    parser.add_argument("--history-days", type=int, required=True)
    args = parser.parse_args()
    snapshot = list_terminal_processes()
    if not any(pid == args.pid and path and same_windows_path(path, args.exe)
               for pid, path in snapshot.processes):
        print(json.dumps({"status": {"state": "unknown", "reason": "MT5 process ended before API attach"}}))
        return
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print(json.dumps({"status": {"state": "unknown", "reason": "MetaTrader5 Python package unavailable"}}))
        return
    result = collect(mt5, args.data_path, args.exe, args.login, args.server,
                     args.day_timezone, args.history_days)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
