"""Read the chart inventory written by the optional MQL5 service."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path


PROBE_FILE = Path("MQL5") / "Files" / "dashboard-eas.tsv"
MAX_PROBE_BYTES = 256 * 1024


def read_probe(data_path: Path) -> dict:
    path = data_path / PROBE_FILE
    if not path.is_file():
        return {"state": "missing"}
    try:
        if path.stat().st_size > MAX_PROBE_BYTES:
            raise ValueError("EA probe file is too large")
        raw = path.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le"
        rows = list(csv.reader(io.StringIO(raw.decode(encoding), newline=""), delimiter="\t"))
        if not rows or len(rows[0]) != 4 or rows[0][0] != "EA_PROBE_V1":
            raise ValueError("invalid EA probe header")
        observed = datetime.fromtimestamp(int(rows[0][1]), timezone.utc).isoformat()
        login = int(rows[0][2])
        server = rows[0][3]
        if login <= 0 or not server or len(server) > 200 or len(rows) > 501:
            raise ValueError("invalid EA probe account or row count")
        experts = []
        seen = set()
        for row in rows[1:]:
            if len(row) != 4:
                raise ValueError("invalid EA probe row")
            chart_id, name, symbol, period = row
            chart = int(chart_id)
            if chart <= 0 or chart in seen or not symbol or not period:
                raise ValueError("invalid EA probe chart")
            if any(len(value) > 200 for value in (name, symbol, period)):
                raise ValueError("EA probe field is too long")
            seen.add(chart)
            if name:
                experts.append({"chart_id": chart, "name": name, "symbol": symbol, "period": period})
        return {"state": "ok", "observed_at_utc": observed, "login": login,
                "server": server, "experts": experts}
    except (OSError, UnicodeError, csv.Error, ValueError, OverflowError):
        return {"state": "invalid"}
