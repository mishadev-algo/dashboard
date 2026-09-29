from __future__ import annotations

import argparse
import os
import socket
import time
from pathlib import Path

from .core import Collector, open_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Journal and Experts logs from MT5 data folders")
    parser.add_argument("--db", type=Path, required=True, help="Local SQLite database file")
    parser.add_argument("--host-id", default=socket.gethostname(), help="Stable name for this Windows host")
    parser.add_argument("--root", type=Path, action="append", default=[], help="Parent folder to scan (repeatable)")
    parser.add_argument("--terminal", type=Path, action="append", default=[], help="Exact MT5 data folder (repeatable)")
    parser.add_argument("--expected", type=Path, action="append", default=[], help="Expected data folder for coverage (repeatable)")
    parser.add_argument("--lookback-days", type=int, default=2, help="Days to scan on first startup (default: 2)")
    parser.add_argument("--follow", action="store_true", help="Keep polling instead of collecting once")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    args = parser.parse_args()

    if args.lookback_days < 0 or args.interval <= 0:
        parser.error("lookback-days must be nonnegative and interval must be positive")

    roots = list(args.root)
    if not roots and not args.terminal and os.environ.get("APPDATA"):
        roots.append(Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal")
    if not roots and not args.terminal:
        parser.error("provide --root or --terminal (APPDATA is not set)")

    connection = open_database(args.db)
    collector = Collector(connection, args.host_id, roots, args.terminal, args.expected, args.lookback_days)
    try:
        while True:
            result = collector.run_once()
            print(f"discovered={result.discovered} events={result.events} missing={result.missing} unknown={result.unknown}", flush=True)
            if not args.follow:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        connection.close()


if __name__ == "__main__":
    main()
