from __future__ import annotations

import argparse
import os
from pathlib import Path

from .ingest import IngestServer
from shared.config import host_tokens as load_host_tokens


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard alpha ingest API")
    storage = parser.add_mutually_exclusive_group(required=True)
    storage.add_argument("--db", type=Path, help="Central SQLite database path")
    storage.add_argument("--postgres", action="store_true", help="Use DASHBOARD_POSTGRES_DSN")
    parser.add_argument("--bind", default="127.0.0.1", help="Loopback listen address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Listen port (default: 8765)")
    args = parser.parse_args()
    if args.bind not in ("127.0.0.1", "::1", "localhost"):
        parser.error("the server must bind to loopback; use an authenticated HTTPS reverse proxy for remote access")
    try:
        tokens = load_host_tokens(os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    postgres_dsn = os.environ.get("DASHBOARD_POSTGRES_DSN") if args.postgres else None
    if args.postgres and not postgres_dsn:
        parser.error("set DASHBOARD_POSTGRES_DSN for PostgreSQL storage")
    server = IngestServer((args.bind, args.port), args.db, tokens, postgres_dsn)
    print(f"ingest listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
