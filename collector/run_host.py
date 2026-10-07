"""Supervise the collector and optional MT5 account worker on a remote host."""

from __future__ import annotations

import argparse
import os
import socket
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Mapping

from server.run_all import Service, supervise

from shared.network import server_origin
from shared.sqlite import open_readonly

from .accounts import load_targets, validate_account_coverage
from .core import resolve_host_id
from .inventory import load_inventory


def service_plan(args: argparse.Namespace, environ: Mapping[str, str]) -> tuple[str, tuple[Service, ...]]:
    if not args.collector_db.is_file():
        raise ValueError("existing collector database is missing")
    if not args.inventory.is_file():
        raise ValueError("inventory file is missing")
    inventory = load_inventory(args.inventory)
    if not inventory.expected:
        raise ValueError("inventory must list the expected running terminals")
    origin = server_origin(args.server_url)
    token = environ.get("DASHBOARD_COLLECTOR_TOKEN", "")
    if not token:
        raise ValueError("DASHBOARD_COLLECTOR_TOKEN is required")
    with closing(open_readonly(args.collector_db)) as connection:
        host_id = resolve_host_id(connection, args.host_id, socket.gethostname())
    if args.accounts:
        validate_account_coverage(inventory.expected, load_targets(args.accounts))
    python = sys.executable
    collector = [python, "-u", "-m", "collector", "--db", str(args.collector_db),
                 "--host-id", host_id, "--inventory", str(args.inventory),
                 "--server-url", origin, "--follow"]
    for root in args.root:
        collector.extend(("--root", str(root)))
    for terminal in args.terminal:
        collector.extend(("--terminal", str(terminal)))
    services = [Service("collector", tuple(collector))]
    if args.accounts:
        services.append(Service("accounts", (
            python, "-u", "-m", "collector.accounts", "--config", str(args.accounts),
            "--host-id", host_id, "--server-url", origin, "--follow",
        )))
    return host_id, tuple(services)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MT5 host workers against a remote central server")
    parser.add_argument("--collector-db", type=Path, required=True,
                        help="Existing collector database with pending rows and offsets")
    parser.add_argument("--inventory", type=Path, default=Path("inventory.json"))
    parser.add_argument("--accounts", type=Path, help="Account config; starts the account worker")
    parser.add_argument("--host-id", help="Must match the collector database's host ID")
    parser.add_argument("--server-url", required=True, help="Stable HTTPS origin of the central server")
    parser.add_argument("--root", type=Path, action="append", default=[])
    parser.add_argument("--terminal", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        host_id, services = service_plan(args, os.environ)
        print(f"Using collector host {host_id}; database={args.collector_db}; origin={args.server_url}",
              flush=True)
        supervise(services, None, Path.cwd())
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Stopping MT5 host workers...", flush=True)


if __name__ == "__main__":
    main()
