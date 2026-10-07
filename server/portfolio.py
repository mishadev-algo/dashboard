"""Portfolio analysis across verified accounts, with currencies kept separate."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import combinations
from urllib.parse import urlencode

from .analytics_web import line_chart, number, strategy_table
from .performance import (ZERO, UNALLOCATED, StrategyStats, PerformanceSource,
                          combined_performance, load_deals, strategy_stats)
from .portfolio_selection import account_key, strategy_options, shared_period
from .links import strategy_url
from .activity_web import activity_panels
from .web import _date_param, _display_time, _escape, _page, _parsed_time, STALE_AFTER_SECONDS


def _anchor(name: str) -> str:
    return "strategy-" + sha256(name.encode()).hexdigest()[:16]


def _selection_form(accounts, currencies, currency, options, account_ids, strategy_ids, params,
                    account_mode, strategy_mode, basis):
    form = '<form class="portfolio-filter" method="get" action="/portfolio">'
    form += ('<div class="filter-row"><label>Currency<select name="currency" '
             'onchange="this.form.elements.account_mode.value=\'all\';'
             'this.form.elements.strategy_mode.value=\'all\';this.form.submit()">')
    form += "".join(f'<option value="{_escape(c)}"' + (' selected' if c == currency else '')
                    + f'>{_escape(c)}</option>' for c in currencies)
    form += (f'</select></label><label>From (UTC, optional)<input type="date" name="from" value="{_escape(_date_param(params, "from"))}"></label>'
             f'<label>To (UTC, optional)<input type="date" name="to" value="{_escape(_date_param(params, "to"))}"></label>'
             '<label>Return basis<select name="basis">')
    for key, label in (("accounts", "Participating account capital"), ("fixed", "Fixed starting capital")):
        form += f'<option value="{key}"' + (' selected' if key == basis else '') + f'>{label}</option>'
    form += (f'</select></label><label>Fixed capital ({_escape(currency)})'
             f'<input type="number" name="capital" min="0.01" step="any" value="{_escape(params.get("capital", [""])[0])}" '
             'placeholder="For fixed basis only"></label></div>')
    for kind, mode in (("account", account_mode), ("strategy", strategy_mode)):
        label = "Accounts" if kind == "account" else "Strategies by account"
        form += f'<fieldset><legend>{label}</legend><div class="filter-row"><label>Include<select name="{kind}_mode">'
        for key, text in (("all", "All accounts" if kind == "account" else "All strategies on selected accounts"),
                          ("selected", "Checked accounts" if kind == "account" else "Checked strategy + account pairs")):
            form += f'<option value="{key}"' + (' selected' if key == mode else '') + f'>{text}</option>'
        form += '</select></label>'
        for text, checked in (("Select all", "true"), ("Clear", "false")):
            target_mode = "all" if checked == "true" else "selected"
            form += (f'<button class="selection-button" type="button" onclick="'
                     f'this.form.elements.{kind}_mode.value=\'{target_mode}\';'
                     f'this.closest(\'fieldset\').querySelectorAll(\'input[type=checkbox]\').forEach(x=>x.checked={checked})">'
                     f'{text}</button>')
        form += '</div><div class="selection-grid">'
        for account in accounts:
            server, login = account[:2]
            aid = account_key(server, login)
            if kind == "account":
                form += (f'<label class="selection-choice"><input type="checkbox" name="account" value="{aid}"'
                         + (' checked' if aid in account_ids else '')
                         + ' onchange="this.form.elements.account_mode.value=\'selected\'">'
                         + f'<span>{_escape(login)} @ {_escape(server)}</span></label>')
            else:
                group = [o for o in options if (o.server, o.login) == (server, login)]
                if not group:
                    continue
                form += f'<div class="selection-group"><strong>{_escape(login)} @ {_escape(server)}</strong>'
                if aid not in account_ids:
                    form += '<small class="muted">Excluded by account selection</small>'
                for option in group:
                    form += (f'<label class="selection-choice"><input type="checkbox" name="strategy" value="{option.key}"'
                             + (' checked' if option.key in strategy_ids else '')
                             + ' onchange="this.form.elements.strategy_mode.value=\'selected\'">'
                             + f'<span>{_escape(option.name)}<small class="muted">'
                             + (f'First deal: {_escape(option.first_day)}' if option.first_day else 'No recorded deals')
                             + '</small></span></label>')
                form += '</div>'
        form += '</div></fieldset>'
    reset_dates = {key: values for key, values in params.items() if key not in ("from", "to")}
    reset_dates["currency"] = [currency]
    form += ('<div class="filter-row"><button type="submit">Build portfolio</button>'
             f'<a href="/portfolio?{_escape(urlencode(reset_dates, doseq=True))}">Full shared history</a>'
             f'<a href="/portfolio?{_escape(urlencode({"currency": currency}))}">Reset selection</a></div></form>')
    return form


def _correlations(stats: dict[str, StrategyStats]) -> str:
    # Bound the matrix for readability. Use UTC day PnL, never call it return correlation.
    names = [name for name, s in sorted(stats.items(), key=lambda item: -abs(item[1].pnl))
             if len(s.daily) >= 20 and name not in UNALLOCATED and name != "unmapped"][:12]
    pairs = []
    for left, right in combinations(names, 2):
        a, b = stats[left].daily, stats[right].daily
        start, end = max(min(a), min(b)), min(max(a), max(b))
        days = sorted(day for day in a.keys() | b.keys() if start <= day <= end)
        if len(days) < 20:
            continue
        x, y = [a.get(day, ZERO) for day in days], [b.get(day, ZERO) for day in days]
        avg_x, avg_y = sum(x) / len(x), sum(y) / len(y)
        cov = sum((u - avg_x) * (v - avg_y) for u, v in zip(x, y))
        variance = sum((u - avg_x) ** 2 for u in x) * sum((v - avg_y) ** 2 for v in y)
        if variance:
            pairs.append((cov / variance.sqrt(), left, right, len(days)))
    intro = ('<section class="account-block"><h3>Strategy relationships · daily PnL</h3>'
             '<p class="muted">Pearson correlation of realized PnL in UTC, not returns. '
             'Up to 12 mapped strategies with the largest absolute net PnL; at least 20 active days per strategy '
             'and 20 paired days in their overlapping recorded span. Days where either strategy has a deal are included; '
             'the other strategy has zero if it has no deal. Only pairs with nonzero variance appear. '
             'Position sizes and incomplete history can affect this measure.</p>')
    if not pairs:
        return intro + '<p>No strategy pairs have enough overlapping activity yet.</p></section>'
    rows = "".join(
        f'<tr><td><a href="#{_anchor(left)}">{_escape(left)}</a></td>'
        f'<td><a href="#{_anchor(right)}">{_escape(right)}</a></td><td>{corr:+.2f}</td><td>{count}</td></tr>'
        for corr, left, right, count in sorted(pairs, key=lambda pair: -abs(pair[0]))
    )
    return (intro + '<div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Strategy</th>'
            '<th>PnL correlation</th><th>Paired days</th></tr></thead><tbody>' + rows + '</tbody></table></div></section>')


def render_portfolio(connection, params: dict[str, list[str]], now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    accounts = connection.execute(
        "SELECT server,login,currency,latest_snapshot_utc,history_start_day,balance,host_id,terminal_id "
        "FROM accounts ORDER BY currency,server,login"
    ).fetchall()
    intro = ('<h1>Strategy portfolio</h1><p class="muted">Analyze strategy contribution, realized returns, '
             'drawdowns and shared positions across accounts. Portfolio dates use UTC; account detail dates '
             'use the account’s configured time zone. Currencies are never added together.</p>')
    if not accounts:
        return _page("Portfolio", intro + '<p>No verified account snapshots yet.</p>')
    currencies = sorted({a[2] for a in accounts})
    currency = params.get("currency", [currencies[0]])[0]
    if currency not in currencies:
        currency = currencies[0]
    accounts = [a for a in accounts if a[2] == currency]
    data = {}
    options = []
    for server, login, _, received, history_start, balance, host_id, terminal_id in accounts:
        deals = [replace(d, day=d.time.date().isoformat()) for d in load_deals(connection, server, login)]
        positions = connection.execute(
            "SELECT strategy,symbol,type,volume,profit,swap FROM positions WHERE server=? AND login=?",
            (server, login),
        ).fetchall()
        status = connection.execute(
            "SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?",
            (host_id, terminal_id),
        ).fetchone()
        data[(server, login)] = (deals, positions, status)
        options.extend(strategy_options(server, login, deals, positions,
                                        (_parsed_time(received) or now).date().isoformat()))
    account_mode = params.get("account_mode", ["selected" if "account" in params else "all"])[0]
    strategy_mode = params.get("strategy_mode", ["selected" if "strategy" in params else "all"])[0]
    account_ids = ({account_key(a[0], a[1]) for a in accounts} if account_mode == "all"
                   else set(params.get("account", [])))
    strategy_ids = ({o.key for o in options} if strategy_mode == "all" else set(params.get("strategy", [])))
    chosen_options = [o for o in options if o.key in strategy_ids and account_key(o.server, o.login) in account_ids]
    basis = "fixed" if params.get("basis", ["accounts"])[0] == "fixed" else "accounts"
    form = _selection_form(accounts, currencies, currency, options, account_ids, strategy_ids, params,
                           account_mode, strategy_mode, basis)
    accounts = [a for a in accounts if account_key(a[0], a[1]) in account_ids
                and (strategy_mode == "all" or any((o.server, o.login) == a[:2] for o in chosen_options))]
    if not accounts:
        return _page("Portfolio", intro + form + '<p class="note">Select at least one account and strategy. '
                     'Unknown or excluded selections are not replaced with all accounts.</p>')
    if strategy_mode == "all":
        spans = [(min([a[4]] + [d.day for d in data[a[:2]][0]]),
                  (_parsed_time(a[3]) or now).date().isoformat()) for a in accounts]
    else:
        spans = [(o.first_day, o.last_day) for o in chosen_options]
    period = shared_period(spans, _date_param(params, "from"), _date_param(params, "to"))
    if period.reason:
        return _page("Portfolio", intro + form + f'<p class="note">{_escape(period.reason)}</p>')
    start, end = period.start, period.end
    sources = {a[:2]: PerformanceSource(data[a[:2]][0], a[5],
               None if strategy_mode == "all" else frozenset(o.name for o in chosen_options if (o.server, o.login) == a[:2]))
               for a in accounts}
    selected_deals = {key: [d for d in source.deals if source.includes(d)] for key, source in sources.items()}
    combined_deals = [d for deals in selected_deals.values() for d in deals]
    capital = None
    capital_error = ""
    if basis == "fixed":
        try:
            capital = Decimal(params.get("capital", [""])[0])
            if not capital.is_finite() or capital < Decimal("0.01") or capital > Decimal("1e30"):
                raise InvalidOperation
        except InvalidOperation:
            capital_error = "Enter a fixed starting capital between 0.01 and 1e30 to calculate percentage returns."
            capital = None
    combined = combined_performance(sources, start, end, capital)
    if capital_error:
        combined.reason = capital_error
        combined.total_return = combined.max_drawdown = None
        combined.points = []
    stats = strategy_stats(combined_deals, start, end)
    account_rows, breakdown = [], {}
    exposure: dict[str, dict] = {}
    cash = floating = balance_total = ZERO
    missing_balances = stale = 0
    for server, login, _, received, history_start, balance, _, _ in accounts:
        deals, positions, status = data[(server, login)]
        source = sources[(server, login)]
        result = combined_performance({(server, login): source}, start, end)
        per_account = strategy_stats(selected_deals[(server, login)], start, end)
        cash += result.cash
        if balance is None:
            missing_balances += 1
        else:
            balance_total += Decimal(balance)
        age = now - (_parsed_time(received) or datetime.min.replace(tzinfo=timezone.utc))
        fresh = bool(status and status[0] == received and json.loads(status[1]).get("data_complete") is True
                     and timedelta(0) <= age <= timedelta(seconds=STALE_AFTER_SECONDS))
        stale += int(not fresh)
        url = "/accounts?" + urlencode({"server": server, "login": login, "from": start, "to": end})
        account_link = f'<a href="{_escape(url)}">{_escape(login)} @ {_escape(server)}</a>'
        account_float = ZERO
        for name, symbol, side, volume, profit, swap in positions:
            if source.strategies is not None and name not in source.strategies:
                continue
            value = Decimal(profit) + Decimal(swap)
            account_float += value
            for table in (stats, per_account):
                s = table.setdefault(name, StrategyStats())
                s.positions += 1
                s.floating += value
                s.symbols.add(symbol)
            item = exposure.setdefault(symbol, {"buy": ZERO, "sell": ZERO, "unknown": ZERO,
                                                "floating": ZERO, "strategies": set(), "accounts": set()})
            item["buy" if side == 0 else "sell" if side == 1 else "unknown"] += Decimal(str(volume))
            item["floating"] += value
            item["strategies"].add(name)
            item["accounts"].add((server, login))
        floating += account_float
        recorded = min((d.day for d in deals), default=history_start)
        account_rows.append(
            f'<tr><td>{account_link}<small>{"Current" if fresh else "Stale snapshot"} · '
            f'{_escape(_display_time(received))}</small><small>Recorded deals from {_escape(recorded)}</small></td>'
            f'<td>{number(result.pnl)}</td><td>{number(result.total_return, "%")}'
            + (f'<small>{_escape(result.reason)}</small>' if result.reason else '')
            + f'</td><td>{number(result.max_drawdown, "%")}</td><td>{number(account_float)}</td>'
            f'<td>{number(Decimal(balance) if balance is not None else None)}</td></tr>'
        )
        for name, s in per_account.items():
            detail_link = (f'<small><a href="{_escape(strategy_url(server, login, name, start, end, zone="UTC", basis=basis, capital=params.get("capital", [""])[0]))}">Strategy details →</a></small>'
                           if name not in UNALLOCATED else '')
            breakdown.setdefault(name, []).append(
                f'<tr><td>{account_link}{detail_link}</td><td>{number(s.pnl)}</td><td>{s.closes}</td>'
                f'<td>{number(s.win_rate, "%")}</td><td>{number(s.average_close)}</td>'
                f'<td>{s.positions}</td><td>{number(s.floating)}</td></tr>'
            )
    pnl = sum((s.pnl for s in stats.values()), ZERO)
    gross = sum((abs(s.pnl) for s in stats.values()), ZERO)
    unmapped = stats.get("unmapped", StrategyStats())
    cards = '<div class="cards portfolio-cards">'
    for label, value in (("Net trading PnL", number(pnl, " " + currency)),
                         ("Current floating PnL", number(floating, " " + currency)),
                         ("Cash transfers · excluded from PnL", number(cash, " " + currency)),
                         ("Latest balances", number(balance_total, " " + currency) if not missing_balances else "Incomplete"),
                         ("Strategies with trading activity", str(sum(bool(s.daily) for n, s in stats.items()
                                                                               if n not in UNALLOCATED))),
                         ("Accounts / stale snapshots", f"{len(accounts)} / {stale}")):
        cards += f'<div class="card"><span class="muted">{label}</span><b>{value}</b></div>'
    cards += '</div>'
    notices = ('<details class="note"><summary>Coverage and calculation</summary><p>Results cover stored deals only; missing historical deals cannot be inferred. '
               'Latest balances and open positions use each account’s last complete snapshot, not the period end. '
               'Strategy selection uses the exact account and strategy pair; tables group selected pairs by strategy name. '
               'Account balances and transfers are whole-account context, counted once per participating account. '
               'When individual strategies are selected, unallocated commissions and income are excluded because '
               'their strategy ownership is unknown.</p></details>')
    if stale:
        notices += f'<p class="note">{stale} account(s) have stale snapshots. Current positions and balances may be outdated.</p>'
    if unmapped.daily or unmapped.positions:
        notices += (f'<p class="note">Unmapped activity: {number(unmapped.pnl, " " + currency)} net PnL, '
                    f'{unmapped.closes} exit deals and {unmapped.positions} open positions. '
                    'Configure magic-to-strategy mappings to attribute this activity.</p>')
    top = max(stats.items(), key=lambda item: abs(item[1].pnl), default=None)
    if top and gross:
        notices += (f'<p>Largest absolute PnL contribution: <a href="#{_anchor(top[0])}">{_escape(top[0])}</a> '
                    f'· {number(abs(top[1].pnl) / gross * 100, "%")} of absolute strategy PnL.</p>')
    daily: dict[str, Decimal] = {}
    for s in stats.values():
        for day, amount in s.daily.items():
            daily[day] = daily.get(day, ZERO) + amount
    running = ZERO
    curve = [(start, ZERO)]
    for day, amount in sorted(daily.items()):
        running += amount
        curve.append((day, running))
    curve.append((end, running))
    selection_summary = (f'<p class="note"><strong>Shared analysis period (UTC): {_escape(start)} – {_escape(end)}</strong><br>'
                         f'{len(accounts)} participating account(s) · {len(chosen_options)} strategy/account pair(s). '
                         + ('Individual strategy history starts at its first recorded deal; this is not a deployment date. '
                            if strategy_mode != "all" else 'Using the shared recorded account history. ')
                         + 'The end is limited by the earliest last complete account snapshot. Quiet days remain in the period.</p>')
    body = intro + form + selection_summary + cards + notices
    body += '<section class="panel"><h2>Portfolio trading PnL · transfers excluded</h2>'
    body += line_chart(curve, "Portfolio cumulative trading PnL excluding transfers", currency) + '</section>'
    body += '<section class="panel"><details><summary>Selected portfolio · realized return (%)</summary>'
    body += '<div class="account-summary">'
    body += f'<div>Period return<strong>{number(combined.total_return, "%")}</strong></div>'
    body += f'<div>Max drawdown<strong>{number(combined.max_drawdown, "%")}</strong></div></div>'
    if basis == "fixed":
        body += (f'<p class="muted">Fixed starting capital: {number(capital, " " + currency)}. '
                 'Model return = selected cumulative net PnL / this capital. Account deposits and withdrawals '
                 'are ignored. This is a model of the selected recorded trades, not historical allocated strategy ROI.</p>')
    else:
        body += ('<p class="muted">Account-capital basis: selected realized PnL divided by the combined actual '
                 'balance of participating accounts at each event, geometrically linked. Each account is counted once. '
                 'Other strategies affect the capital base but do not enter selected PnL; transfers do not create gains. '
                 'This measures selected results relative to account capital, not standalone strategy ROI.</p>')
    body += '<p class="muted">The common period starts at 0%. Floating PnL is excluded.</p>'
    if combined.reason:
        body += f'<p class="note">{_escape(combined.reason)} The selected monetary PnL is shown above.</p>'
    else:
        body += line_chart(combined.points, "Selected portfolio return over shared history", "%")
    body += '</details></section>'
    def period_link(lower, upper):
        query = {**params, "currency": [currency], "from": [lower], "to": [upper]}
        return "/portfolio?" + urlencode(query, doseq=True)
    body += activity_panels(combined_deals, start, end, currency, period_link)
    body += '<h2>Strategy contribution and quality</h2>'
    body += strategy_table(stats, currency, {name: "#" + _anchor(name) for name in stats})
    body += ('<h2>Selected results by account</h2><p class="muted">These rows use selected strategies only. '
             'Returns use the entire capital of each participating account, independent of the combined chart’s basis. '
             'Account percentages are not summed. Links open the full account view.</p>'
             '<div class="table-wrap"><table><thead><tr><th>Account / coverage</th><th>Net PnL</th>'
             '<th>Return</th><th>Max return drawdown</th><th>Current floating</th><th>Latest balance</th>'
             '</tr></thead><tbody>' + "".join(account_rows) + '</tbody></table></div>')
    body += '<h2>Shared instruments · current positions</h2>'
    body += ('<p class="muted">Shared symbols reveal overlapping positions and opposing directions. '
             'Lots are shown per symbol only; contracts can differ between brokers. These are not notional exposures or risk weights.</p>')
    exposure_rows = []
    for symbol, item in sorted(exposure.items()):
        names = ' · '.join(f'<a href="#{_anchor(name)}">{_escape(name)}</a>' for name in sorted(item["strategies"]))
        exposure_rows.append(f'<tr><th scope="row">{_escape(symbol)}</th><td>{names}</td>'
                             f'<td>{len(item["accounts"])}</td><td>{number(item["buy"])}</td>'
                             f'<td>{number(item["sell"])}</td><td>{number(item["floating"])}</td></tr>')
    body += ('<div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Strategies</th><th>Accounts</th>'
             '<th>Buy lots</th><th>Sell lots</th><th>Floating PnL</th></tr></thead><tbody>'
             + ("".join(exposure_rows) or '<tr><td colspan="6">No open positions in the latest snapshots.</td></tr>')
             + '</tbody></table></div>')
    body += _correlations(stats)
    body += '<h2>Same strategy across accounts</h2><p class="muted">Compare exit counts and average exit results; '
    body += 'position sizing and different history coverage can explain differences.</p>'
    for name, rows in sorted(breakdown.items()):
        body += (f'<section id="{_anchor(name)}" class="account-block"><h3>{_escape(name)}</h3>'
                 '<div class="table-wrap"><table><thead><tr><th>Account</th><th>Net PnL</th><th>Exit deals</th>'
                 '<th>Exit win rate</th><th>Avg. exit</th><th>Open</th><th>Floating PnL</th></tr></thead>'
                 '<tbody>' + "".join(rows) + '</tbody></table></div></section>')
    return _page("Portfolio", body)
