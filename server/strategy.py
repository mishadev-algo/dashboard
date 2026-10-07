"""Strategy/account drill-down using the same return basis as portfolio analysis."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode

from .activity_web import activity_panels
from .analytics_web import line_chart, number, strategy_table
from .links import strategy_url
from .performance import ZERO, StrategyStats, PerformanceSource, combined_performance, load_deals, strategy_stats
from .portfolio_selection import account_key, strategy_key, shared_period
from .web import _page, _escape, _date_param, _parsed_time, _display_time, STALE_AFTER_SECONDS
from shared.timezones import day_zone
from .pagination import deal_limit, deal_pagination


def render_strategy(connection, params, now=None):
    now = now or datetime.now(timezone.utc)
    server, name = params.get("server", [""])[0], params.get("strategy", [""])[0]
    try:
        login = int(params.get("login", [""])[0])
        if not 0 < login <= 9223372036854775807:
            raise ValueError
    except ValueError:
        return _page("Strategy", '<h1>Strategy details</h1><p>Select a strategy from <a href="/accounts">Accounts</a> or <a href="/portfolio">Portfolio</a>.</p>')
    account = connection.execute(
        "SELECT currency,day_timezone,latest_snapshot_utc,balance,host_id,terminal_id FROM accounts WHERE server=? AND login=?",
        (server, login),
    ).fetchone()
    if not account:
        return _page("Strategy", '<h1>Strategy not found</h1><p>This account is unavailable. <a href="/accounts">Back to accounts</a>.</p>')
    currency, zone, received, balance, host, tid = account
    use_utc = params.get("zone", ["account"])[0] == "UTC"
    zone_label = "UTC" if use_utc else zone
    deals = load_deals(connection, server, login)
    if use_utc:
        deals = [replace(d, day=d.time.date().isoformat()) for d in deals]
    trades = [d for d in deals if d.kind == "trade" and d.strategy == name]
    positions = connection.execute(
        "SELECT ticket,symbol,type,volume,profit,swap FROM positions WHERE server=? AND login=? AND strategy=? ORDER BY symbol,ticket",
        (server, login, name),
    ).fetchall()
    if not trades and not positions:
        return _page("Strategy", '<h1>Strategy not found</h1><p>No recorded strategy with this name on this account. <a href="/accounts">Back to accounts</a>.</p>')
    symbol = params.get("symbol", [""])[0]
    symbols = sorted({d.symbol for d in trades if d.symbol} | {p[1] for p in positions})
    basis = "fixed" if params.get("basis", ["accounts"])[0] == "fixed" else "accounts"
    capital_raw = params.get("capital", [""])[0]
    filters = {"basis": basis, "capital": capital_raw, "zone": "UTC" if use_utc else "account", "symbol": symbol}
    def link(start="", end="", **changes):
        return strategy_url(server, login, name, start, end, **{**filters, **changes})
    last_day = (_parsed_time(received) or now).astimezone(timezone.utc if use_utc else day_zone(zone)).date().isoformat()
    first_day = min((d.day for d in trades), default=None)
    requested_start, requested_end = _date_param(params, "from"), _date_param(params, "to")
    period = shared_period([(first_day, last_day)], requested_start, requested_end)
    start, end = period.start, period.end
    account_url = "/accounts?" + urlencode({"server": server, "login": login, "from": requested_start, "to": requested_end})
    body = (f'<nav class="breadcrumbs" aria-label="Breadcrumb"><a href="/accounts">Accounts</a><span>/</span>'
            f'<a href="{_escape(account_url)}">{login} @ {_escape(server)}</a><span>/</span><span>{_escape(name)}</span></nav>'
            f'<h1>{_escape(name)}</h1><p class="muted">Strategy on {login} @ {_escape(server)} · {_escape(currency)} · '
            f'dates: {_escape(zone_label)} · first recorded deal: {_escape(first_day or "not available")}</p>')
    status = connection.execute("SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?", (host, tid)).fetchone()
    age = now - (_parsed_time(received) or datetime.min.replace(tzinfo=timezone.utc))
    fresh = bool(status and status[0] == received and json.loads(status[1]).get("data_complete") is True
                 and timedelta(0) <= age <= timedelta(seconds=STALE_AFTER_SECONDS))
    body += f'<p><span class="badge {"good" if fresh else "warn"}">{"Current snapshot" if fresh else "Stale snapshot"}</span> '
    body += f'<span class="muted">Last complete snapshot: {_escape(_display_time(received))}</span></p>'
    body += '<form action="/strategy" method="get">'
    for key, value in (("server", server), ("login", login), ("strategy", name), ("zone", filters["zone"])):
        body += f'<input type="hidden" name="{key}" value="{_escape(value)}">'
    body += (f'<label>From<input type="date" name="from" value="{_escape(requested_start)}"></label>'
             f'<label>To<input type="date" name="to" value="{_escape(requested_end)}"></label>'
             '<label>Symbol<select name="symbol"><option value="">All symbols</option>')
    if symbol and symbol not in symbols:
        body += f'<option selected value="{_escape(symbol)}">{_escape(symbol)} (unavailable)</option>'
    body += "".join(f'<option value="{_escape(s)}"' + (' selected' if s == symbol else '') + f'>{_escape(s)}</option>' for s in symbols)
    body += '</select></label><label>Return basis<select name="basis">'
    for key, label in (("accounts", "Account capital"), ("fixed", "Fixed starting capital")):
        body += f'<option value="{key}"' + (' selected' if key == basis else '') + f'>{label}</option>'
    body += (f'</select></label><label>Fixed capital ({_escape(currency)})<input type="number" min="0.01" step="any" name="capital" '
             f'value="{_escape(capital_raw)}" placeholder="For fixed basis only"></label><button>Apply</button></form>')
    if first_day:
        body += '<div class="quick-periods" aria-label="Quick periods">'
        for label, days in (("1M", 30), ("3M", 90), ("6M", 180), ("All history", None)):
            lower = max(first_day, (date.fromisoformat(last_day) - timedelta(days=days - 1)).isoformat()) if days else ""
            body += f'<a href="{_escape(link(lower, last_day if days else ""))}">{label}</a>'
        body += '</div>'
    portfolio_query = {"currency": currency, "account_mode": "selected", "account": account_key(server, login),
                       "strategy_mode": "selected", "strategy": strategy_key(server, login, name),
                       "from": requested_start, "to": requested_end, "basis": basis, "capital": capital_raw}
    portfolio_url = "/portfolio?" + urlencode(portfolio_query)
    body += f'<p><a href="{_escape(portfolio_url)}">Analyze this strategy in Portfolio →</a> '
    body += '<small class="muted">Portfolio uses UTC dates and includes all symbols.</small></p>'
    if period.reason or symbol and symbol not in symbols:
        body += f'<p class="note">{_escape(period.reason if period.reason else "This symbol has no recorded activity for this strategy.")}</p>'
    else:
        source = PerformanceSource(deals, balance, frozenset((name,)), frozenset((symbol,)) if symbol else None)
        selected = [d for d in trades if source.includes(d) and start <= d.day <= end]
        capital, capital_error = None, ""
        if basis == "fixed":
            try:
                capital = Decimal(capital_raw)
                if not capital.is_finite() or not Decimal("0.01") <= capital <= Decimal("1e30"):
                    raise InvalidOperation
            except InvalidOperation:
                capital, capital_error = None, "Enter a fixed starting capital between 0.01 and 1e30."
        result = combined_performance({(server, login): source}, start, end, capital)
        if capital_error:
            result.reason, result.points = capital_error, []
            result.total_return = result.max_drawdown = None
        stats = strategy_stats(selected, start, end).get(name, StrategyStats())
        filtered_positions = [p for p in positions if not symbol or p[1] == symbol]
        stats.positions = len(filtered_positions)
        stats.floating = sum((Decimal(p[4]) + Decimal(p[5]) for p in filtered_positions), ZERO)
        body += f'<p class="muted">Analysis period: {_escape(start)} – {_escape(end)} · {_escape(zone_label)}</p><div class="cards portfolio-cards">'
        factor = number(stats.profit_factor) if stats.gross_losses else ("∞" if stats.gross_wins else "—")
        for label, value in (("Net strategy PnL", number(result.pnl, " " + currency)), ("Period return", number(result.total_return, "%")),
                             ("Max return drawdown", number(result.max_drawdown, "%")), ("Exit win rate", number(stats.win_rate, "%")),
                             ("Exit profit factor", factor), ("Current floating PnL", number(stats.floating, " " + currency))):
            body += f'<div class="card"><span class="muted">{label}</span><b>{value}</b></div>'
        body += '</div><section class="panel"><h2>Strategy profit · transfers excluded</h2>'
        body += line_chart(result.pnl_points, "Strategy cumulative PnL", currency)
        body += '<details><summary>Strategy realized return (%)</summary>'
        body += (f'<p class="muted">Fixed capital: {number(capital, " " + currency)}. Return = cumulative selected net PnL / this capital.</p>' if basis == "fixed" else
                 '<p class="muted">Selected strategy results relative to the entire account capital, geometrically linked. '
                 'Other strategies change the capital base but do not enter this strategy’s PnL.</p>')
        body += '<p class="muted">Transfers create no returns. Floating PnL and unallocated account charges/income are excluded. '
        body += 'This is not a separately allocated strategy ROI.</p>'
        if result.reason:
            body += f'<p class="note">{_escape(result.reason)} Monetary PnL remains available.</p>'
        else:
            body += line_chart(result.points, "Strategy realized return", "%")
            peak = Decimal(1)
            drawdowns = []
            for day, value in result.points:
                index = 1 + value / 100
                peak = max(peak, index)
                drawdowns.append((day, (index / peak - 1) * 100))
            body += '<details><summary>Drawdown curve</summary>' + line_chart(drawdowns, "Strategy drawdown", "%", "#bd5555") + '</details>'
        body += '</details></section>'
        body += activity_panels(selected, start, end, currency,
                                lambda lower, upper: link(lower, upper) + ("#deals" if lower == upper else ""))
        body += '<section class="account-block"><h2>Strategy statistics</h2>' + strategy_table({name: stats}, currency) + '</section>'
        body += '<section class="account-block"><h2>Symbols → strategy detail</h2><p class="muted">All instruments traded by this strategy in the period. Click one to filter the chart and deals.</p><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Net PnL</th><th>Exit deals</th></tr></thead><tbody>'
        for instrument in symbols:
            rows = [d for d in trades if d.symbol == instrument and start <= d.day <= end]
            body += (f'<tr><td><a href="{_escape(link(start, end, symbol=instrument))}">{_escape(instrument)}</a></td>'
                     f'<td>{number(sum((d.net for d in rows), ZERO))}</td><td>{sum(d.entry in (1, 2, 3) for d in rows)}</td></tr>')
        body += '</tbody></table></div></section>'
        body += '<section id="deals" class="account-block"><h2>Strategy deals</h2>'
        limit = deal_limit(params, 50, len(selected))
        body += '<p class="muted">Recorded deals matching the period and symbol, newest first. Entries and partial exits stay separate.</p>'
        body += '<div class="table-wrap"><table><thead><tr><th>Time UTC</th><th>Deal</th><th>Symbol</th><th>Entry / exit</th><th>Net PnL</th><th>Commission / swap / fees</th></tr></thead><tbody>'
        for d in reversed(selected[-limit:]):
            body += (f'<tr id="deal-{d.ticket}"><td>{_escape(_display_time(d.time.isoformat()))}</td><td>{d.ticket}</td>'
                     f'<td><a href="{_escape(link(start, end, symbol=d.symbol))}">{_escape(d.symbol)}</a></td>'
                     f'<td>{ {0:"Entry", 1:"Exit", 2:"Reversal", 3:"Close by"}.get(d.entry, "Unknown") }</td>'
                     f'<td>{number(d.net)}</td><td>{number(d.costs)}</td></tr>')
        body += ('' if selected else '<tr><td colspan="6">No recorded deals in this period.</td></tr>') + '</tbody></table></div>'
        body += deal_pagination("/strategy", {"server": server, "login": login, "strategy": name,
                                  "from": requested_start, "to": requested_end, **filters},
                                  min(limit, len(selected)), len(selected), 50)
        body += '</section>'
    body += '<section class="account-block"><h2>Current strategy positions</h2><p class="muted">Latest account snapshot; independent of the historical date filter.</p>'
    body += '<div class="table-wrap"><table><thead><tr><th>Ticket</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Floating PnL</th></tr></thead><tbody>'
    filtered_positions = [p for p in positions if not symbol or p[1] == symbol]
    for ticket, instrument, side, volume, profit, swap in filtered_positions:
        body += (f'<tr><td>{ticket}</td><td><a href="{_escape(link(requested_start, requested_end, symbol=instrument))}">{_escape(instrument)}</a></td>'
                 f'<td>{"Buy" if side == 0 else "Sell" if side == 1 else "Unknown"}</td><td>{volume:g}</td><td>{number(Decimal(profit) + Decimal(swap), " " + currency)}</td></tr>')
    body += ('' if filtered_positions else '<tr><td colspan="5">No open positions for this selection.</td></tr>') + '</tbody></table></div></section>'
    peers = connection.execute(
        "SELECT a.server,a.login,a.currency FROM accounts a WHERE "
        "EXISTS (SELECT 1 FROM deals d WHERE d.server=a.server AND d.login=a.login AND d.kind='trade' AND d.strategy=?) "
        "OR EXISTS (SELECT 1 FROM positions p WHERE p.server=a.server AND p.login=a.login AND p.strategy=?) ORDER BY a.currency,a.server,a.login",
        (name, name),
    ).fetchall()
    body += '<section class="account-block"><h2>Same strategy on other accounts</h2><div class="related-links">'
    for peer_server, peer_login, peer_currency in peers:
        if (peer_server, peer_login) != (server, login):
            body += (f'<a class="panel" href="{_escape(strategy_url(peer_server, peer_login, name, requested_start, requested_end, zone=filters["zone"]))}">'
                     f'{peer_login} @ {_escape(peer_server)}<small>{_escape(peer_currency)}</small></a>')
    compare = {**portfolio_query, "account_mode": "all", "strategy": [strategy_key(s, l, name) for s, l, c in peers if c == currency]}
    body += '</div>'
    body += f'<p><a href="{_escape("/portfolio?" + urlencode(compare, doseq=True))}">Compare all matching accounts in {_escape(currency)} →</a></p></section>'
    return _page(name + " · Strategy", body)
