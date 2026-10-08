"""Small local diagnostic receipts; never contain credentials or trading data."""
from __future__ import annotations

import json
import os
from pathlib import Path


def read_state(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path: Path, state: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Diagnostics must not interrupt collection or retry of undelivered rows.
        print(f"diagnostic_state_unavailable={path}", flush=True)
