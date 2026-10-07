"""Linked monthly, daily and weekday views of recorded realized PnL."""

from calendar import Calendar, monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from html import escape

from .analytics_web import number
from .performance import ZERO


def activity_panels(deals, start: str, end: str, currency: str, period_url) -> str:
    daily = defaultdict(lambda: ZERO)
    exits = defaultdict(int)
    for deal in deals:
        if start <= deal.day <= end:
            daily[deal.day] += deal.net
            exits[deal.day] += int(deal.kind == "trade" and deal.entry in (1, 2, 3))
    if not daily:
        return '<section class="account-block"><h2>Performance calendar</h2><p class="muted">No recorded activity in this period.</p></section>'
    months = defaultdict(lambda: ZERO)
    for day, pnl in daily.items():
        months[day[:7]] += pnl
    tiles = []
    for month, pnl in sorted(months.items())[-24:]:
        year, month_num = map(int, month.split("-"))
        first = max(start, month + "-01")
        last = min(end, f"{month}-{monthrange(year, month_num)[1]:02}")
        days = [d for d in daily if d.startswith(month)]
        tiles.append(f'<a class="month-tile {"pnl-positive" if pnl > 0 else "pnl-negative" if pnl < 0 else "pnl-flat"}" '
                     f'href="{escape(period_url(first, last), quote=True)}"><span>{escape(month)}</span>'
                     f'<strong>{number(pnl, " " + currency)}</strong>'
                     f'<small>{sum(exits[d] for d in days)} exits · {len(days)} active days</small></a>')
    body = ('<section class="account-block"><h2>Monthly PnL</h2>'
            '<p class="muted">Click a month to inspect its results. Up to 24 months with recorded activity; '
            'amounts include entry and exit costs and follow the current filters.</p>'
            '<div class="month-grid">' + "".join(tiles) + '</div></section>')
    month = max(daily)[:7]
    year, month_num = map(int, month.split("-"))
    cells = []
    for week in Calendar().monthdayscalendar(year, month_num):
        for day_num in week:
            day = f"{month}-{day_num:02}"
            if not day_num or not start <= day <= end:
                cells.append('<span class="calendar-empty" aria-hidden="true"></span>')
                continue
            pnl = daily.get(day, ZERO)
            label = "No recorded deals" if day not in daily else f"{number(pnl, ' ' + currency)} · {exits[day]} exits"
            cells.append(f'<a class="calendar-day {"pnl-positive" if pnl > 0 else "pnl-negative" if pnl < 0 else "pnl-flat"}" '
                         f'href="{escape(period_url(day, day), quote=True)}" title="{escape(day)} · {label}">'
                         f'<span>{day_num}</span><strong>{number(pnl) if day in daily else "—"}</strong></a>')
    best, worst = max(daily, key=daily.get), min(daily, key=daily.get)
    body += ('<div class="analysis-columns"><section class="panel"><h2>Daily PnL · ' + month + '</h2>'
             '<p class="muted">Latest active month in the selection. Click a day to see its deals. '
             'An empty day means no recorded activity.</p><div class="calendar-grid">'
             + "".join(f'<span class="calendar-heading">{d}</span>' for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
             + "".join(cells) + '</div></section><section class="panel"><h2>Day-of-week results</h2>'
             '<p class="muted">Realized PnL by booking day, including entry costs. Average is per active day.</p>'
             '<div class="table-wrap"><table><thead><tr><th>Day</th><th>Active days</th><th>Net PnL</th><th>Avg. day</th></tr></thead><tbody>')
    for weekday, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
        values = [value for day, value in daily.items() if date.fromisoformat(day).weekday() == weekday]
        total = sum(values, ZERO)
        body += f'<tr><td>{name}</td><td>{len(values)}</td><td>{number(total)}</td><td>{number(total / len(values) if values else None)}</td></tr>'
    body += '</tbody></table></div><div class="account-summary">'
    for label, day in (("Best active day", best), ("Worst active day", worst)):
        body += (f'<div><span class="muted">{label}</span><strong>{number(daily[day], " " + currency)}</strong>'
                 f'<a href="{escape(period_url(day, day), quote=True)}">{escape(day)} → deals</a></div>')
    return body + '</div></section></div>'
