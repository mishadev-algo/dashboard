from __future__ import annotations

import hashlib
import json


EVENT_KEY_FIELDS = ("host_id", "terminal_id", "stream", "file_name", "generation", "byte_offset")


def event_id(event: dict) -> str:
    key = [event[field] for field in EVENT_KEY_FIELDS]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
