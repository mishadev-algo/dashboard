"""Discover an account only by attaching to a verified, already running terminal."""
from __future__ import annotations

import argparse
import json
import ntpath

from shared.paths import same_windows_path
from .process import list_terminal_processes


def identity(mt5: object, data_path: str, executable: str) -> dict:
    if not mt5.initialize(executable, timeout=10000,
                          portable=same_windows_path(data_path, ntpath.dirname(executable))):
        return {"error": "MT5 connection unavailable; check broker authorization in MT5"}
    try:
        info = mt5.terminal_info()
        if info is None or not same_windows_path(info.data_path, data_path) or not same_windows_path(info.path, ntpath.dirname(executable)):
            return {"error": "MT5 attached to a different terminal"}
        account = mt5.account_info()
        if account is None or int(account.login) <= 0 or not account.server:
            return {"error": "MT5 account details unavailable"}
        return {"login": int(account.login), "server": str(account.server),
                "connected": bool(info.connected), "autotrading": bool(info.trade_allowed)}
    finally:
        mt5.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--exe", required=True)
    parser.add_argument("--pid", type=int, required=True)
    args = parser.parse_args()
    snapshot = list_terminal_processes()
    if not any(pid == args.pid and exe and same_windows_path(exe, args.exe)
               for pid, exe in snapshot.processes):
        result = {"error": "Terminal process changed; repeat preparation"}
    else:
        try:
            import MetaTrader5 as mt5
            result = identity(mt5, args.data_path, args.exe)
        except ImportError:
            result = {"error": "Install MetaTrader5 in the selected Python"}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
