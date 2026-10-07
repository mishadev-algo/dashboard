"""Read-only portfolio analytics from recorded deals (not historical equity)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from itertools import groupby

ZERO = Decimal(0)
ONE = Decimal(1)
# ENUM_DEAL_TYPE: charges, interest, dividends, franked dividends and tax.
# Kept in the analytics layer so old stored records need no collector migration.
INCOME_TYPES = frozenset((4, 12, 15, 16, 17))
RESULT_KINDS = frozenset(("trade", "commission", "income"))
UNALLOCATED = frozenset(("unallocated commission", "unallocated income / charges"))


@dataclass(frozen=True)
class Deal:
    time: datetime
    day: str
    ticket: int
    kind: str
    type: int
    entry: int
    strategy: str
    symbol: str
    net: Decimal
    costs: Decimal


def load_deals(connection, server: str, login: int) -> list[Deal]:
    rows = connection.execute(
        "SELECT time_utc,day_local,ticket,kind,type,entry,strategy,symbol,profit,commission,swap,fee "
        "FROM deals WHERE server=? AND login=?", (server, login),
    )
    deals = []
    for stamp, day, ticket, kind, kind_id, entry, strategy, symbol, profit, commission, swap, fee in rows:
        costs = sum((Decimal(value) for value in (commission, swap, fee)), ZERO)
        if kind == "other" and kind_id in INCOME_TYPES:
            kind = "income"
        deals.append(Deal(datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc),
                          day, ticket, kind, kind_id, entry, strategy, symbol, Decimal(profit) + costs, costs))
    return sorted(deals, key=lambda deal: (deal.time, deal.ticket))


def in_period(deal: Deal, start: str, end: str) -> bool:
    return start <= deal.day <= end


@dataclass
class Performance:
    points: list[tuple[str, Decimal]] = field(default_factory=list)
    pnl_points: list[tuple[str, Decimal]] = field(default_factory=list)
    total_return: Decimal | None = None
    max_drawdown: Decimal | None = None
    pnl: Decimal = ZERO
    cash: Decimal = ZERO
    reason: str = ""


@dataclass(frozen=True)
class PerformanceSource:
    """One unique account, with its entire ledger and optional strategy subset."""

    deals: list[Deal]
    balance: str | None
    strategies: frozenset[str] | None = None
    symbols: frozenset[str] | None = None

    def includes(self, deal: Deal) -> bool:
        return (deal.type != 3 and deal.kind in RESULT_KINDS and
                (self.strategies is None or deal.kind == "trade" and deal.strategy in self.strategies)
                and (self.symbols is None or deal.symbol in self.symbols))


def combined_performance(sources: dict[tuple[str, int], PerformanceSource], start: str, end: str,
                         initial_capital: Decimal | None = None) -> Performance:
    """Selected realized PnL on a common interval, without cash-flow gains.

    With initial_capital, return = selected PnL / fixed user-supplied capital.
    Otherwise link selected PnL / actual combined account balance at each event.
    The latter is an account-capital-based index, not allocated strategy ROI.
    Nonselected trades still update actual capital, but never the selected PnL.
    """
    result = Performance(points=[(start, ZERO)], pnl_points=[(start, ZERO)])
    if start > end or not sources:
        result.reason = "No shared period or selected accounts."
        result.points = []
        return result
    fixed = initial_capital is not None
    if fixed and (not initial_capital.is_finite() or initial_capital <= 0):
        result.reason = "Starting capital must be a positive finite number."
    capitals = {}
    events = []
    for key, source in sources.items():
        capitals[key] = (Decimal(source.balance) - sum((d.net for d in source.deals if d.type != 3), ZERO)
                         if source.balance is not None else ZERO)
        if source.balance is None and not fixed:
            result.reason = "A selected account has no verified balance; enter a fixed starting capital."
        events.extend((d.time, key, d) for d in source.deals)
    index = peak = ONE
    drawdown = ZERO
    funded = bool(fixed and not result.reason)
    for _, group in groupby(sorted(events, key=lambda item: (item[0], item[1], item[2].ticket)), key=lambda item: item[0]):
        batch = list(group)
        day = batch[0][2].day  # Portfolio callers normalize every deal to UTC.
        capital = sum(capitals.values(), ZERO)
        if start <= day <= end:
            selected = sum((d.net for _, key, d in batch if sources[key].includes(d)), ZERO)
            cash = sum((d.net for _, _, d in batch if d.kind == "cash" and d.type != 3), ZERO)
            result.cash += cash
            result.pnl += selected
            result.pnl_points.append((day, result.pnl))
            if not fixed:
                if any(d.kind == "other" and d.type != 3 and d.net for _, _, d in batch):
                    result.reason = "Unclassified account adjustments prevent a reliable account-capital return."
                if selected and any(d.kind == "cash" and d.type != 3 and d.net for _, _, d in batch):
                    result.reason = "A transfer and selected result share a timestamp; their order is unknown."
                if selected and (capital <= 0 or capital + selected < 0):
                    result.reason = result.reason or "Non-positive account capital prevents a reliable percentage return."
            if not result.reason:
                if fixed:
                    index = ONE + result.pnl / initial_capital
                elif selected:
                    index *= ONE + selected / capital
                peak = max(peak, index)
                drawdown = max(drawdown, (peak - index) / peak * 100)
            funded = funded or capital > 0
            result.points.append((day, (index - ONE) * 100))
        for _, key, d in batch:
            if d.type != 3:
                capitals[key] += d.net
        if start <= day <= end:
            funded = funded or sum(capitals.values(), ZERO) > 0
    if not fixed and not funded:
        # Quiet intervals still have capital, reconstructed before their end.
        funded = sum((Decimal(s.balance) - sum((d.net for d in s.deals if d.type != 3 and d.day > end), ZERO)
                      for s in sources.values() if s.balance is not None), ZERO) > 0
    if not funded and not result.reason:
        result.reason = "No positive account capital in the common period; enter a fixed starting capital."
    result.pnl_points.append((end, result.pnl))
    if result.reason:
        result.points = []
    else:
        result.total_return = (index - ONE) * 100
        result.max_drawdown = drawdown
        result.points.append((end, result.total_return))
    return result


def account_performance(deals: list[Deal], balance: str | None, start: str, end: str) -> Performance:
    """Link changes in reconstructed balance, leaving transfers out of returns.

    All recorded deals are needed to reconstruct capital, even for a filtered
    period. Floating PnL is unavailable historically, so this is a realized
    balance return, never an equity TWR. Ambiguous transfers suppress percentages.
    """
    result = Performance(points=[(start, ZERO)], pnl_points=[(start, ZERO)])
    if start > end:
        result.reason = "Start date must not be after end date."
        return result
    capital = (Decimal(balance) - sum((d.net for d in deals if d.type != 3), ZERO)
               if balance is not None else ZERO)
    if balance is None:
        result.reason = "A verified balance is needed to calculate percentage returns."
    index = peak = ONE
    drawdown = ZERO
    funded = False
    for _, group in groupby(sorted(deals, key=lambda d: (d.time, d.ticket)), key=lambda deal: deal.time):
        batch = list(group)
        selected = [d for d in batch if in_period(d, start, end)]
        trading = sum((d.net for d in selected if d.kind in RESULT_KINDS and d.type != 3), ZERO)
        movement = sum((d.net for d in batch if d.type != 3), ZERO)
        if selected:
            flows = [d for d in selected if d.kind == "cash" and d.type != 3 and d.net]
            result.cash += sum((d.net for d in flows), ZERO)
            result.pnl += trading
            result.pnl_points.append((selected[-1].day, result.pnl))
            if any(d.kind == "other" and d.type != 3 and d.net for d in selected):
                result.reason = "Unclassified balance adjustments prevent a reliable percentage return."
            if flows and trading:
                result.reason = "A transfer and trading result share a timestamp; their order is unknown."
            if trading and not result.reason:
                if capital <= 0 or capital + trading < 0:
                    result.reason = "Non-positive capital prevents a reliable percentage return."
                else:
                    index *= (capital + trading) / capital
                    peak = max(peak, index)
                    drawdown = max(drawdown, (peak - index) / peak * 100)
            funded = funded or capital > 0 or capital + movement > 0
            result.points.append((selected[-1].day, (index - ONE) * 100))
        capital += movement
    # A quiet period with a positive balance has a valid zero realized return.
    if not any(in_period(d, start, end) for d in deals):
        period_capital = (Decimal(balance) - sum((d.net for d in deals if d.type != 3 and d.day > end), ZERO)
                          if balance is not None else ZERO)
        funded = period_capital > 0
    if not funded and not result.reason:
        result.reason = "No positive invested capital in the selected period."
    result.pnl_points.append((end, result.pnl))
    if not result.reason:
        result.total_return = (index - ONE) * 100
        result.max_drawdown = drawdown
        result.points.append((end, result.total_return))
    else:
        result.points = []
    return result


@dataclass
class StrategyStats:
    pnl: Decimal = ZERO
    costs: Decimal = ZERO
    closes: int = 0
    wins: int = 0
    gross_wins: Decimal = ZERO
    gross_losses: Decimal = ZERO
    peak: Decimal = ZERO
    drawdown: Decimal = ZERO
    floating: Decimal = ZERO
    positions: int = 0
    symbols: set[str] = field(default_factory=set)
    daily: dict[str, Decimal] = field(default_factory=dict)

    def add(self, deal: Deal) -> None:
        self.pnl += deal.net
        self.costs += deal.net if deal.kind == "commission" or deal.type in (4, 17) else deal.costs
        self.peak = max(self.peak, self.pnl)
        self.drawdown = max(self.drawdown, self.peak - self.pnl)
        if deal.symbol:
            self.symbols.add(deal.symbol)
        self.daily[deal.day] = self.daily.get(deal.day, ZERO) + deal.net
        if deal.kind == "trade" and deal.entry in (1, 2, 3):
            self.closes += 1
            self.wins += int(deal.net > 0)
            self.gross_wins += max(ZERO, deal.net)
            self.gross_losses += max(ZERO, -deal.net)

    @property
    def win_rate(self) -> Decimal | None:
        return Decimal(self.wins) / self.closes * 100 if self.closes else None

    @property
    def profit_factor(self) -> Decimal | None:
        return self.gross_wins / self.gross_losses if self.gross_losses else None

    @property
    def average_close(self) -> Decimal | None:
        return (self.gross_wins - self.gross_losses) / self.closes if self.closes else None


def strategy_stats(deals: list[Deal], start: str, end: str) -> dict[str, StrategyStats]:
    stats: dict[str, StrategyStats] = {}
    for deal in sorted(deals, key=lambda d: (d.time, d.ticket)):
        if in_period(deal, start, end) and deal.kind in RESULT_KINDS and deal.type != 3:
            label = (deal.strategy if deal.kind == "trade" else "unallocated commission"
                     if deal.kind == "commission" else "unallocated income / charges")
            stats.setdefault(label, StrategyStats()).add(deal)
    return stats
