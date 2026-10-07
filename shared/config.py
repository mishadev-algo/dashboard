"""Configuration checks used by both central server launch modes."""

from __future__ import annotations

import json
from typing import Mapping


def host_tokens(environ: Mapping[str, str]) -> dict[str, str]:
    try:
        value = json.loads(environ["DASHBOARD_HOST_TOKENS"])
    except (KeyError, ValueError) as exc:
        raise ValueError("set DASHBOARD_HOST_TOKENS to the host-to-token JSON map") from exc
    if not isinstance(value, dict) or not value or not all(
        isinstance(host, str) and host and isinstance(token, str) and token
        for host, token in value.items()
    ):
        raise ValueError("DASHBOARD_HOST_TOKENS needs one token per host")
    if len(set(value.values())) != len(value):
        raise ValueError("DASHBOARD_HOST_TOKENS needs one distinct token per host")
    return value
