"""Supervise only the central API and alert worker on a separate host."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

from .run_all import Service, supervise
from shared.config import host_tokens as load_host_tokens


def service_plan(args: argparse.Namespace, environ: Mapping[str, str]) -> tuple[Service, ...]:
    postgres = getattr(args, "postgres", False)
    if postgres and not environ.get("DASHBOARD_POSTGRES_DSN"):
        raise ValueError("set DASHBOARD_POSTGRES_DSN for PostgreSQL storage")
    if not postgres and (args.db is None or not args.db.is_file()):
        raise ValueError("central database is missing; restore or create it explicitly first")
    if args.port < 1 or args.port > 65535:
        raise ValueError("port must be between 1 and 65535")
    load_host_tokens(environ)
    if not args.no_alerts and not all(environ.get(key) for key in (
        "DASHBOARD_TELEGRAM_BOT_TOKEN", "DASHBOARD_TELEGRAM_CHAT_ID",
    )):
        raise ValueError("set both Telegram environment variables or use --no-alerts")
    python = sys.executable
    storage = ("--postgres",) if postgres else ("--db", str(args.db))
    services = [Service("server", (python, "-u", "-m", "server", *storage,
                                   "--port", str(args.port)))]
    if not args.no_alerts:
        services.append(Service("alerts", (python, "-u", "-m", "server.alerts", *storage)))
    return tuple(services)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the central dashboard services")
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--db", type=Path, help="Existing central SQLite database")
    storage.add_argument("--postgres", action="store_true", help="Use DASHBOARD_POSTGRES_DSN")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-alerts", action="store_true")
    args = parser.parse_args()
    try:
        services = service_plan(args, os.environ)
        supervise(services, args.port, Path.cwd())
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Stopping central services...", flush=True)


if __name__ == "__main__":
    main()
