from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .ingest import IngestServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Dashboard alpha ingest API")
    parser.add_argument("--db", type=Path, required=True, help="Central SQLite database path")
    parser.add_argument("--bind", default="127.0.0.1", help="Listen address (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="Listen port (default: 8765)")
    args = parser.parse_args()
    try:
        host_tokens = json.loads(os.environ["DASHBOARD_HOST_TOKENS"])
        if not isinstance(host_tokens, dict) or not host_tokens or not all(
            isinstance(host, str) and host and isinstance(token, str) and token
            for host, token in host_tokens.items()
        ):
            raise ValueError("DASHBOARD_HOST_TOKENS must be a nonempty JSON host-to-token map")
        if len(set(host_tokens.values())) != len(host_tokens):
            raise ValueError("each host must have a distinct token")
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    server = IngestServer((args.bind, args.port), args.db, host_tokens)
    print(f"ingest listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
