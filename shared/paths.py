"""Windows MT5 paths have Windows comparison rules on every host OS."""

from __future__ import annotations

import ntpath


def windows_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value.strip().strip('"')))


def same_windows_path(left: str, right: str) -> bool:
    return windows_path(left) == windows_path(right)
