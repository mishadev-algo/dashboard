from __future__ import annotations

import argparse
import os
import socket
import time
from pathlib import Path

from .core import Collector, open_database, resolve_host_id
from .inventory import Inventory, load_inventory
from .process import ProcessProbe
from .remote import RemoteUploadError, RemoteUploader, heartbeat, pending_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Journal and Experts logs from MT5 data folders")
    parser.add_argument("--db", type=Path, required=True, help="Local SQLite database file")
    parser.add_argument("--host-id", help="Stable host ID; existing database ID is reused by default")
    parser.add_argument("--root", type=Path, action="append", default=[], help="Parent folder to scan (repeatable)")
    parser.add_argument("--terminal", type=Path, action="append", default=[], help="Exact MT5 data folder (repeatable)")
    parser.add_argument("--expected", type=Path, action="append", default=[], help="Expected data folder for coverage (repeatable)")
    parser.add_argument("--inventory", type=Path, help="JSON inventory file (defaults to inventory.json when present)")
    parser.add_argument("--lookback-days", type=int, default=2, help="Days to scan on first startup (default: 2)")
    parser.add_argument("--follow", action="store_true", help="Keep polling instead of collecting once")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--server-url", help="Central ingest origin; HTTPS required except for localhost")
    parser.add_argument("--batch-size", type=int, default=200, help="Events per upload (default: 200)")
    parser.add_argument("--pending-warning", type=int, default=100000, help="Warn when unsent lines reach this count")
    args = parser.parse_args()

    if args.lookback_days < 0 or args.interval <= 0 or args.pending_warning < 1:
        parser.error("lookback-days must be nonnegative; interval and pending-warning must be positive")

    inventory_path = args.inventory or Path("inventory.json")
    try:
        inventory = load_inventory(inventory_path) if args.inventory or inventory_path.is_file() else Inventory()
    except ValueError as exc:
        parser.error(str(exc))

    roots = list(args.root)
    if not roots and not args.terminal and os.environ.get("APPDATA"):
        roots.append(Path(os.environ["APPDATA"]) / "MetaQuotes" / "Terminal")
    if not roots and not args.terminal and not args.expected and not inventory.expected:
        parser.error("provide --root, --terminal, or expected inventory folders (APPDATA is not set)")

    connection = open_database(args.db)
    try:
        host_id = resolve_host_id(connection, args.host_id, socket.gethostname())
    except ValueError as exc:
        parser.error(str(exc))
    collector = Collector(
        connection, host_id, roots, args.terminal,
        (*args.expected, *inventory.expected), args.lookback_days,
        inventory.archived, inventory.configured or bool(args.expected),
    )
    uploader = None
    process_probe = None
    if args.server_url:
        try:
            uploader = RemoteUploader(
                connection, args.server_url, host_id,
                os.environ.get("DASHBOARD_COLLECTOR_TOKEN", ""), args.batch_size,
            )
        except ValueError as exc:
            parser.error(str(exc))
        process_probe = ProcessProbe()
    failed = False
    try:
        while True:
            result = collector.run_once()
            uploaded = 0
            upload_error = None
            if uploader:
                try:
                    process_states = process_probe.check(result.paths) if process_probe else {}
                    uploaded = uploader.upload(heartbeat(connection, host_id, result, process_states))
                except RemoteUploadError as exc:
                    upload_error = str(exc)
                    failed = True
            pending = pending_count(connection)
            warning = " QUEUE_WARNING" if pending >= args.pending_warning else ""
            print(
                f"discovered={result.discovered} events={result.events} uploaded={uploaded} "
                f"pending={pending} expected={len(result.expected)} archived={len(result.archived)} "
                f"missing={result.missing} unknown={result.unknown}"
                f"{warning}" + (f" upload_error={upload_error}" if upload_error else ""),
                flush=True,
            )
            if not args.follow:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        connection.close()
    if failed and not args.follow:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
