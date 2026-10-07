"""Selection and shared history boundaries for portfolio comparisons."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .performance import Deal


def account_key(server: str, login: int) -> str:
    return sha256(json.dumps([server, login], ensure_ascii=False).encode()).hexdigest()[:24]


def strategy_key(server: str, login: int, name: str) -> str:
    return sha256(json.dumps([server, login, name], ensure_ascii=False).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class StrategyOption:
    server: str
    login: int
    name: str
    first_day: str | None
    last_day: str

    @property
    def key(self) -> str:
        return strategy_key(self.server, self.login, self.name)


def strategy_options(server: str, login: int, deals: list[Deal], positions: list, end: str) -> list[StrategyOption]:
    first = {}
    for deal in deals:
        if deal.kind == "trade":
            first[deal.strategy] = min(first.get(deal.strategy, deal.day), deal.day)
    names = set(first) | {row[0] for row in positions}
    return [StrategyOption(server, login, name, first.get(name), end) for name in sorted(names)]


@dataclass(frozen=True)
class SharedPeriod:
    start: str
    end: str
    reason: str = ""


def shared_period(spans: list[tuple[str | None, str]], requested_start: str = "",
                  requested_end: str = "") -> SharedPeriod:
    if requested_start and requested_end and requested_start > requested_end:
        return SharedPeriod(requested_start, requested_end, "Start date must not be after end date.")
    if not spans:
        return SharedPeriod("", "", "Select at least one account and strategy.")
    if any(start is None for start, _ in spans):
        return SharedPeriod("", "", "A selected strategy has no recorded deals yet; its shared history is unknown.")
    start = max(start for start, _ in spans)
    end = min(end for _, end in spans)
    start = max(start, requested_start) if requested_start else start
    end = min(end, requested_end) if requested_end else end
    if start > end:
        return SharedPeriod(start, end, "No shared history in the requested period. Change the selection or dates.")
    return SharedPeriod(start, end)
