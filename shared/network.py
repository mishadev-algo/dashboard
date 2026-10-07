"""Network configuration validation for collector-to-server traffic."""

from __future__ import annotations

from urllib.parse import urlsplit


def server_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("server-url must be an HTTP(S) origin") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("server-url must be an HTTP(S) origin")
    if port == 0:
        raise ValueError("server-url must have a valid port")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("server-url must use HTTPS except for localhost")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("server-url must be an origin without a path or query")
    return value.rstrip("/")
