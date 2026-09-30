from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .core import canonical


@dataclass(frozen=True)
class Inventory:
    expected: tuple[Path, ...] = ()
    archived: tuple[Path, ...] = ()
    configured: bool = False


def load_inventory(path: Path) -> Inventory:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read inventory {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) - {"expected", "archived"}:
        raise ValueError("inventory must contain only expected and archived lists")
    groups = {}
    for name in ("expected", "archived"):
        values = data.get(name, [])
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError(f"inventory {name} must be a list of paths")
        paths = tuple(Path(value.strip()) for value in values)
        if not all(path.is_absolute() for path in paths):
            raise ValueError(f"inventory {name} paths must be absolute")
        if len({canonical(item) for item in paths}) != len(paths):
            raise ValueError(f"inventory {name} contains duplicate paths")
        groups[name] = paths
    if {canonical(path) for path in groups["expected"]} & {canonical(path) for path in groups["archived"]}:
        raise ValueError("a folder cannot be both expected and archived")
    return Inventory(groups["expected"], groups["archived"], True)
