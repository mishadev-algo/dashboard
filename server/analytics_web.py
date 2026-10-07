"""Server-rendered charts and tables shared by account and portfolio views."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from hashlib import sha256
import re

from .performance import Deal, Performance, StrategyStats, ZERO


def number(value: Decimal | None, suffix: str = "") -> str:
    return f"{value:,.2f}{escape(suffix)}" if value is not None else "—"


def line_chart(points: list[tuple[str, Decimal]], title: str, unit: str, color: str = "#2774b8",
               *, chart_class: str = "performance-chart", include_zero: bool = True) -> str:
    if not points:
        return '<p class="muted">No recorded values for this period.</p>'
    ordinals = [date.fromisoformat(day).toordinal() for day, _ in points]
    first, last = min(ordinals), max(ordinals)
    low = min(value for _, value in points)
    high = max(value for _, value in points)
    if include_zero:
        low, high = min(ZERO, low), max(ZERO, high)
    padding = max((high - low) / 10, Decimal("0.1"))
    low, high = low - padding, high + padding
    coords = [(80 + 864 * (day - first) / (last - first) if last > first else 512,
               250 - 214 * float((value - low) / (high - low)))
              for day, (_, value) in zip(ordinals, points)]
    color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#2774b8"
    if color == "#2774b8":
        color = "#d15d72" if points[-1][1] < 0 else "#257bb8"
    chart_id = "curve-" + sha256(repr((points, title, unit, color)).encode()).hexdigest()[:16]
    grid = []
    for i in range(5):
        value = low + (high - low) * Decimal(i) / 4
        y = 250 - 214 * i / 4
        label = f'{value / 1000000:.1f}M' if abs(value) >= 1000000 else f'{value / 1000:.1f}K' if abs(value) >= 10000 else f'{value:.2f}'
        grid.append(f'<line x1="80" x2="944" y1="{y:.1f}" y2="{y:.1f}" stroke="#e8eef5"/>'
                    f'<text x="65" y="{y + 4:.1f}" text-anchor="end" fill="#8191a6" font-size="12">{label}</text>')
    for ordinal in sorted({round(first + (last - first) * i / 4) for i in range(5)}):
        x = 80 + 864 * (ordinal - first) / (last - first) if last > first else 512
        day = date.fromordinal(ordinal)
        grid.append(f'<text x="{x:.1f}" y="278" text-anchor="middle" fill="#8191a6" font-size="12">{day:%d %b}</text>')
    if low <= 0 <= high:
        y = 250 - 214 * float(-low / (high - low))
        grid.append(f'<line x1="80" x2="944" y1="{y:.1f}" y2="{y:.1f}" stroke="#a8bacb" stroke-dasharray="4 5"/>')
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    # Preserve every recorded point in the path. Hover/focus targets use the last
    # recorded point per day, so overlapping intraday points remain legible.
    daily_indices = list({day: i for i, (day, _) in enumerate(points)}.values())
    indices = daily_indices[::max(1, (len(daily_indices) + 119) // 120)]
    if indices[-1] != len(points) - 1:
        indices.append(len(points) - 1)
    targets = []
    for i in indices:
        day, value = points[i]
        x, y = coords[i]
        tx, ty = max(80, min(744, x - 100)), max(8, y - 68)
        label = f'{escape(day)} · {number(value, " " + unit)}'
        targets.append(
            f'<g class="chart-point" tabindex="0" role="img" aria-label="{label}"><title>{label}</title>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="transparent"/>'
            '<g class="chart-tooltip" pointer-events="none">'
            f'<line x1="{x:.1f}" x2="{x:.1f}" y1="30" y2="250" stroke="{color}" stroke-dasharray="3 4" opacity=".5"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="white" stroke="{color}" stroke-width="3"/>'
            f'<rect x="{tx:.1f}" y="{ty:.1f}" width="200" height="53" rx="9" fill="#142e4b"/>'
            f'<text x="{tx + 13:.1f}" y="{ty + 20:.1f}" fill="#abc3dc" font-size="12">{escape(day)} · day end</text>'
            f'<text x="{tx + 13:.1f}" y="{ty + 40:.1f}" fill="white" font-size="14" font-weight="600">{number(value, " " + unit)}</text>'
            '</g></g>')
    return (f'<div class="chart-wrap polished-chart"><div class="chart-heading"><span>{escape(title)}</span>'
            f'<strong style="color:{color}">{number(points[-1][1], " " + unit)}</strong></div>'
            f'<div class="chart-canvas"><svg class="{escape(chart_class, quote=True)}" viewBox="0 0 1000 300" role="img" '
            f'aria-label="{escape(title, quote=True)}"><title>{escape(title)}</title>'
            f'<defs><linearGradient id="{chart_id}" x1="0" x2="0" y1="0" y2="1">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity=".20"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity=".01"/></linearGradient></defs>'
            + "".join(grid)
            + f'<polygon points="{coords[0][0]:.1f},250 {line} {coords[-1][0]:.1f},250" fill="url(#{chart_id})"/>'
            + f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2.7" stroke-linejoin="round" stroke-linecap="round"/>'
            + f'<circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="5" fill="{color}" stroke="white" stroke-width="2"/>'
            + "".join(targets) + '</svg></div><div class="chart-axis">'
            + f'<span>{escape(points[0][0])} → {escape(points[-1][0])}</span>'
            '<span>Hover or focus a point · recorded results</span></div></div>')


def pnl_breakdown(deals: list[Deal], currency: str, start: str, end: str) -> str:
    selected = [d for d in deals if start <= d.day <= end and d.type != 3]
    gross = sum((d.net - d.costs for d in selected if d.kind == "trade"), ZERO)
    costs = sum((d.costs for d in selected if d.kind == "trade"), ZERO)
    commission = sum((d.net for d in selected if d.kind == "commission"), ZERO)
    income = sum((d.net for d in selected if d.kind == "income"), ZERO)
    rows = (("Trade profit before costs", gross), ("Trade commission / swap / fees", costs),
            ("Separate commissions", commission), ("Dividends / interest / charges / taxes", income),
            ("Net realized PnL · chart total", gross + costs + commission + income))
    return ('<details><summary>How trading profit is calculated</summary>'
            '<p class="muted">Selected recorded period. Deposits, withdrawals, credit and open-position profit '
            'are excluded. Account-level income and charges are shown separately from trade results.</p>'
            '<table><thead><tr><th>Component</th><th>' + escape(currency) + '</th></tr></thead><tbody>'
            + ''.join(f'<tr><th scope="row">{label}</th><td>{number(value)}</td></tr>' for label, value in rows)
            + '</tbody></table></details>')


def performance_panel(result: Performance, currency: str) -> str:
    body = '<section class="account-block"><h3>Trading profit · transfers excluded</h3>'
    body += line_chart(result.pnl_points, "Cumulative trading PnL excluding transfers", currency)
    body += '<details><summary>Realized return · transfers excluded (%)</summary>'
    body += '<div class="account-summary">'
    for label, value in (("Period return", number(result.total_return, "%")),
                         ("Max return drawdown", number(result.max_drawdown, "%")),
                         ("Net trading PnL", number(result.pnl, " " + currency))):
        body += f'<div><span class="muted">{label}</span><strong>{value}</strong></div>'
    body += '</div><p class="muted">Realized balance return, net of recorded commission, swap and fees. '
    body += 'Recorded interest, dividends and taxes are included as unallocated income / charges. '
    body += 'Deposits and withdrawals change capital without changing the return index. '
    body += 'Floating PnL is excluded; this is not an equity curve. The selected period starts at 0%.</p>'
    body += '<p class="muted">The percentage index measures results against changing account capital. '
    body += 'After transfers it can have a different sign from cumulative money profit; it is not profit in account currency.</p>'
    if result.reason:
        body += f'<p class="note">{escape(result.reason)} Trading PnL remains available below.</p>'
    else:
        body += line_chart(result.points, "Realized return excluding deposits and withdrawals", "%")
        peak = Decimal(1)
        drawdowns = []
        for day, value in result.points:
            index = 1 + value / 100
            peak = max(peak, index)
            drawdowns.append((day, (index / peak - 1) * 100))
        body += '<details><summary>Drawdown over the selected period</summary>'
        body += line_chart(drawdowns, "Realized return drawdown", "%", "#bd5555") + '</details>'
    return body + '</details></section>'


def strategy_table(stats: dict[str, StrategyStats], currency: str, links: dict[str, str] | None = None) -> str:
    if not stats:
        return '<p class="muted">No strategy activity in this period.</p>'
    gross_contribution = sum((abs(s.pnl) for s in stats.values()), ZERO)
    rows = []
    for name, s in sorted(stats.items(), key=lambda item: (-item[1].pnl, item[0])):
        label = escape(name)
        if links and name in links:
            label = f'<a href="{escape(links[name], quote=True)}">{label}</a>'
        factor = number(s.profit_factor) if s.gross_losses else ("∞" if s.gross_wins else "—")
        share = number(abs(s.pnl) / gross_contribution * 100, "%") if gross_contribution else "—"
        rows.append(f'<tr><th scope="row">{label}<small>{escape(", ".join(sorted(s.symbols)))}</small></th>'
                    f'<td>{number(s.pnl)}</td><td>{share}</td><td>{number(s.costs)}</td>'
                    f'<td>{s.closes}</td><td>{number(s.win_rate, "%")}</td><td>{factor}</td>'
                    f'<td>{number(s.average_close)}</td><td>{number(s.drawdown)}</td>'
                    f'<td>{s.positions}</td><td>{number(s.floating)}</td></tr>')
    return ('<details><summary>Metric definitions · ' + escape(currency) + '</summary><p class="muted">'
            'Money values in ' + escape(currency) + '. PnL includes all entry and exit costs; '
            'interest, dividends and taxes appear as unallocated income / charges. '
            'Win rate, profit factor and average use exit deals only (partial exits count separately; entry costs '
            'are not reassigned). Drawdown is the peak-to-trough decline in cumulative realized PnL, starting at zero. '
            'Contribution = |strategy PnL| / sum of |strategy PnL|, not a capital or risk weight. '
            'Open positions and floating PnL are from the latest snapshots, independent of the date filter.</p></details>'
            '<div class="table-wrap"><table class="symbol-table"><thead><tr><th>Strategy</th><th>Net PnL</th>'
            '<th>Abs. contribution</th><th>Costs / swap</th><th>Exit deals</th><th>Exit win rate</th>'
            '<th>Exit profit factor</th><th>Avg. exit</th><th>Max PnL drawdown</th><th>Open</th>'
            '<th>Floating PnL</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')
