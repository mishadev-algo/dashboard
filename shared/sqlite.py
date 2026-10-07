"""Open SQLite files for inspection without creating missing databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def open_readonly(path: Path, timeout: float = 30) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
