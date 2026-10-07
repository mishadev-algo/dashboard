from __future__ import annotations

import html
import json
import sqlite3
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from .links import strategy_url
from .pagination import deal_limit, deal_pagination
from shared.cadence import STALE_AFTER_SECONDS as PERIODIC_STALE_AFTER_SECONDS
from shared.timezones import day_zone


STALE_AFTER_SECONDS = PERIODIC_STALE_AFTER_SECONDS
EA_STALE_AFTER_SECONDS = PERIODIC_STALE_AFTER_SECONDS


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _parsed_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _display_time(value: str | None) -> str:
    parsed = _parsed_time(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if parsed else "—"


def _account_labels(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """Use verified snapshots first, then a valid EA report for untracked accounts."""
    labels = {
        (host_id, terminal_id): f"{login} @ {server}"
        for host_id, terminal_id, server, login in connection.execute(
            "SELECT host_id,terminal_id,server,login FROM accounts"
        )
    }
    for host_id, payload_json in connection.execute(
        "SELECT h.host_id,c.payload_json FROM hosts h "
        "LEFT JOIN collector_heartbeats c ON c.heartbeat_id=("
        "SELECT MAX(heartbeat_id) FROM collector_heartbeats WHERE host_id=h.host_id)"
    ):
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            continue
        terminals = payload.get("terminals", []) if isinstance(payload, dict) else []
        for terminal in terminals if isinstance(terminals, list) else []:
            if not isinstance(terminal, dict):
                continue
            probe = terminal.get("ea_probe")
            if isinstance(probe, dict) and probe.get("state") == "ok":
                login, server = probe.get("login"), probe.get("server")
                if login and server:
                    labels.setdefault((host_id, terminal.get("terminal_id")), f"{login} @ {server}")
    return labels


def _alert_detail(alert_type: str, detail: str, host_id: str, target: str,
                  path_accounts: dict[tuple[str, str], str]) -> str:
    message = {
        "folder_missing": "Expected terminal missing",
        "terminal_stopped": "MT5 process stopped",
        "broker_disconnected": "Broker disconnected",
    }.get(alert_type)
    return f"{message}: {path_accounts.get((host_id, target), 'Account unavailable')}" if message else detail


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)} · MT5 dashboard</title>
<style>
:root {{ color-scheme: light; font: 16px/1.5 "Segoe UI Variable", "Segoe UI", Arial, sans-serif; background: #f3f6fa; color: #17283c; }}
* {{ box-sizing: border-box; }} body {{ margin: 0; }}
header {{ background: #132b49; color: #fff; box-shadow: 0 2px 12px #132b491c; }}
.header-inner {{ max-width: 1600px; margin: auto; padding: .9rem 1.5rem; display: flex; align-items: center; gap: 2rem; flex-wrap: wrap; }}
header strong {{ font-size: 1.15rem; letter-spacing: .01em; white-space: nowrap; }}
nav {{ display: flex; flex-wrap: wrap; gap: .25rem; }} nav a {{ color: #dce8f6; text-decoration: none; padding: .45rem .8rem; border-radius: 7px; font-weight: 600; }}
nav a:hover, nav a:focus-visible {{ background: #ffffff1e; color: #fff; }}
main {{ max-width: 1600px; margin: 2rem auto; padding: 0 1.5rem 4rem; }}
h1 {{ margin: 0 0 .3rem; font-size: clamp(1.8rem, 2.2vw, 2.35rem); line-height: 1.2; letter-spacing: -.025em; }}
h2 {{ font-size: 1.3rem; line-height: 1.3; margin: 2rem 0 .85rem; letter-spacing: -.015em; }}
h3 {{ font-size: 1.05rem; margin: 1.7rem 0 .65rem; }} h2 small {{ font-size: .85rem; font-weight: 500; letter-spacing: 0; }}
p {{ margin: .5rem 0 1rem; }} .muted {{ color: #62748a; }}
.note {{ background: #e9f2ff; border: 1px solid #d2e4fa; border-left: 4px solid #487dbd; border-radius: 8px; padding: .9rem 1.1rem; margin: 1.3rem 0; color: #294c72; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 1rem; margin: 1.5rem 0 2rem; }}
.portfolio-cards {{ grid-template-columns: repeat(3,minmax(0,1fr)); }}
.portfolio-filter {{ display: grid; grid-template-columns: minmax(0,1fr); gap: 1rem; }}
.filter-row {{ display: flex; flex-wrap: wrap; gap: .8rem; align-items: end; }}
.portfolio-filter fieldset {{ min-width: 0; margin: 0; padding: 1rem; border: 1px solid #dce5ef; border-radius: 10px; background: #fff; }}
.portfolio-filter legend {{ font-weight: 650; padding: 0 .4rem; }}
.selection-grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(230px,1fr)); gap: .8rem; margin-top: 1rem; max-height: 380px; overflow-y: auto; }}
.selection-group {{ border: 1px solid #e6edf5; padding: .8rem; border-radius: 8px; }}
.selection-group > small {{ display: block; }}
.selection-choice {{ display: flex; align-items: start; gap: .6rem; cursor: pointer; padding: .35rem 0; }}
.selection-choice input {{ min-height: 0; width: 18px; height: 18px; margin-top: .2rem; flex: 0 0 auto; }}
.selection-choice small {{ display: block; font-weight: 400; }}
.selection-button {{ color: #225ca2; background: #eef4fc; border-color: #c8def5; }}
.selection-button:hover {{ color: #fff; }}
.card, .panel {{ background: #fff; border: 1px solid #dce5ef; border-radius: 12px; padding: 1.2rem 1.35rem; box-shadow: 0 3px 14px #1d355309; }}
.card b {{ display: block; font-size: 2rem; line-height: 1.2; margin-top: .4rem; letter-spacing: -.03em; }}
.host-list {{ display: grid; gap: 1rem; margin: 1rem 0 1.8rem; }}
.host-line {{ display: flex; flex-wrap: wrap; gap: .5rem 1.5rem; align-items: center; }}
.host-line strong {{ font-size: 1.08rem; }}
.badge {{ display: inline-block; padding: .22rem .65rem; border-radius: 99px; font-size: .81rem; font-weight: 650; white-space: nowrap; }}
.good {{ background: #ddf3e7; color: #12643c; }} .bad {{ background: #fce4e4; color: #a32727; }}
.neutral {{ background: #eaf0f6; color: #485f78; }} .warn {{ background: #fff1ce; color: #805800; }}
.table-wrap {{ overflow-x: auto; background: #fff; border: 1px solid #dce5ef; border-radius: 12px; box-shadow: 0 3px 14px #1d355309; }}
table {{ width: 100%; border-collapse: collapse; }} th,td {{ text-align: left; vertical-align: top; padding: .9rem 1rem; border-bottom: 1px solid #edf1f5; }}
tbody tr:last-child td {{ border-bottom: 0; }} tbody tr:hover {{ background: #f8fafd; }}
th {{ background: #f8fafd; font-size: .76rem; text-transform: uppercase; letter-spacing: .06em; color: #5e7086; font-weight: 700; white-space: nowrap; }}
td small {{ display: block; color: #6b7d91; margin-top: .2rem; }} td:first-child {{ font-weight: 600; }}
a {{ color: #225ca2; }} a:hover {{ color: #153e72; }} a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {{ outline: 3px solid #80b7f1; outline-offset: 2px; }}
form {{ display: flex; flex-wrap: wrap; gap: .8rem; align-items: end; margin: 1.2rem 0 1.5rem; }}
label {{ display: grid; gap: .35rem; font-size: .88rem; font-weight: 600; color: #40546e; }}
input,select,button {{ font: inherit; padding: .62rem .75rem; border: 1px solid #bdcad9; border-radius: 7px; min-height: 42px; }}
input,select {{ background: #fff; color: #17283c; }} button {{ background: #245fa5; color: #fff; border-color: #245fa5; cursor: pointer; font-weight: 600; }} button:hover {{ background: #1a4e8c; }}
pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: .86rem/1.5 "Cascadia Code", Consolas, monospace; font-weight: 400; }}
.account-layout {{ display: grid; grid-template-columns: minmax(220px, 270px) minmax(0, 1fr); align-items: start; gap: 1.25rem; margin-top: 1.5rem; }}
.account-sidebar {{ position: sticky; top: 1.25rem; }}
.account-sidebar h2 {{ margin: 0 0 .8rem; font-size: 1rem; }}
.account-tabs {{ display: grid; gap: .4rem; }}
.account-tab {{ display: block; padding: .75rem .85rem; border-radius: 8px; color: #283e57; text-decoration: none; border: 1px solid transparent; }}
.account-tab:hover, .account-tab:focus-visible {{ background: #f0f5fb; color: #173e6b; }}
.account-tab[aria-current="page"] {{ background: #e8f1fc; border-color: #c8def5; color: #154d88; box-shadow: inset 3px 0 #3476bd; }}
.account-tab-login {{ display: block; font-size: 1.02rem; font-weight: 700; }}
.account-tab small {{ display: block; margin-top: .1rem; color: #63768d; overflow-wrap: anywhere; }}
.account-content {{ min-width: 0; }} .account-content form {{ margin-top: 0; }}
.account-detail h2 {{ margin-top: 0; display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; }}
.account-summary {{ display: flex; flex-wrap: wrap; gap: .75rem 2rem; margin: 1.2rem 0; }}
.account-summary strong {{ display: block; font-size: 1.3rem; color: #203c5e; }}
.account-block {{ margin-top: 1.6rem; }} .account-block h3 {{ margin: 0 0 .65rem; font-size: 1.13rem; }}
.account-block .table-wrap {{ box-shadow: none; }}
.chart-wrap {{ border: 1px solid #dce5ef; border-radius: 10px; padding: 1rem; overflow: hidden; }}
.balance-chart {{ display: block; width: 100%; height: auto; min-height: 210px; }}
.performance-chart {{ display: block; width: 100%; height: auto; min-height: 180px; }}
.symbol-table th small {{ display: block; font-weight: 400; color: #62748a; }}
details summary {{ cursor: pointer; color: #225ca2; margin: .8rem 0; }}
.chart-axis {{ display: flex; justify-content: space-between; gap: 1rem; font-size: .82rem; color: #62748a; }}
.polished-chart {{ background: linear-gradient(180deg,#fff,#f9fbfe); padding: 1rem 1rem .7rem; }}
.chart-heading {{ display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: .3rem .4rem .8rem; color: #687f96; font-size: .88rem; }}
.chart-heading strong {{ font-size: 1.35rem; white-space: nowrap; }}
.chart-canvas {{ overflow-x: auto; }}
.chart-point {{ outline: none; cursor: crosshair; }}
.chart-tooltip {{ visibility: hidden; }}
.chart-point:hover .chart-tooltip,.chart-point:focus .chart-tooltip {{ visibility: visible; }}
.breadcrumbs,.quick-periods {{ display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: 1rem; font-size: .9rem; }}
.breadcrumbs a {{ color: #225ca2; padding: 0; font-weight: 500; }}
.breadcrumbs a:hover,.breadcrumbs a:focus-visible {{ color: #153e72; background: #e9f1fb; }}
.quick-periods a {{ padding: .35rem .85rem; background: #e9f1fb; border: 1px solid #d6e4f4; border-radius: 20px; text-decoration: none; }}
.month-grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: .75rem; }}
.month-tile {{ display: grid; gap: .3rem; padding: 1rem; border: 1px solid #dce5ef; border-radius: 10px; text-decoration: none; }}
.month-tile strong {{ font-size: 1.2rem; }} .month-tile small {{ font-size: .78rem; }}
.analysis-columns {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 1rem; margin-top: 1.5rem; }}
.analysis-columns h2 {{ margin-top: .2rem; }}
.calendar-grid {{ display: grid; grid-template-columns: repeat(7,minmax(0,1fr)); gap: .35rem; }}
.calendar-heading {{ text-align: center; font-size: .75rem; color: #6b7d91; padding: .3rem 0; }}
.calendar-day {{ display: grid; gap: .6rem; padding: .55rem .35rem; border-radius: 7px; text-decoration: none; font-size: .75rem; border: 1px solid #dce5ef; min-width: 0; }}
.calendar-day strong {{ overflow-wrap: anywhere; font-size: .72rem; }}
.pnl-positive {{ background: #e7f5ef; color: #15724f; }} .pnl-negative {{ background: #fceef0; color: #ac3e55; }} .pnl-flat {{ background: #f5f8fc; color: #6b7d91; }}
.related-links {{ display: flex; flex-wrap: wrap; gap: .8rem; }} .related-links small {{ display: block; }}
.deal-pagination {{ display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .8rem; margin-top: 1rem; }}
.show-more {{ display: inline-block; padding: .6rem 1rem; border-radius: 7px; background: #245fa5; color: #fff; text-decoration: none; font-weight: 600; }}
.show-more:hover,.show-more:focus-visible {{ background: #1a4e8c; color: #fff; }}
.show-more span {{ font-weight: 400; opacity: .8; }}
@media (max-width: 1000px) {{ .analysis-columns {{ grid-template-columns: minmax(0,1fr); }} }}
@media (max-width: 500px) {{ .chart-heading,.chart-axis {{ flex-wrap: wrap; }} .chart-heading strong {{ font-size: 1.05rem; }} .polished-chart {{ padding: .6rem; }} }}
@media (max-width: 650px) {{ .chart-canvas svg {{ min-width: 580px; }} }}
.symbol-table th[scope="row"] {{ background: transparent; font-size: .92rem; letter-spacing: 0; text-transform: none; color: #253b55; }}
.metric-track {{ display: inline-block; vertical-align: middle; width: min(140px, 45%); height: 9px; margin-right: .6rem; background: #ecf1f6; border-radius: 99px; overflow: hidden; }}
.metric-fill {{ display: block; height: 100%; background: #3478ba; border-radius: inherit; }}
.metric-fill.positive {{ background: #2d9b73; }} .metric-fill.negative {{ background: #d56a6a; }}
.account-extra {{ margin-top: 1.8rem; }} .account-extra summary {{ cursor: pointer; color: #225ca2; font-weight: 650; }}
.filter-reset {{ align-self: center; font-size: .9rem; font-weight: 600; }}
@media (max-width: 850px) {{ .account-layout {{ grid-template-columns: minmax(0, 1fr); }} .account-sidebar {{ position: static; }} .account-tabs {{ display: flex; overflow-x: auto; padding-bottom: .2rem; }} .account-tab {{ flex: 0 0 auto; min-width: 145px; }} }}
@media (max-width: 850px) {{ .portfolio-cards {{ grid-template-columns: repeat(2,minmax(0,1fr)); }} }}
@media (max-width: 500px) {{ .portfolio-cards {{ grid-template-columns: minmax(0,1fr); }} }}
@media (max-width: 650px) {{ .header-inner {{ padding: .8rem 1rem; gap: .6rem; }} nav {{ width: 100%; }} nav a {{ padding: .35rem .55rem; }} main {{ margin: 1.4rem auto; padding: 0 1rem 2.5rem; }} .card,.panel {{ padding: 1rem; }} th,td {{ padding: .75rem; }} }}
</style></head><body><header><div class="header-inner"><strong>MT5 dashboard</strong><nav><a href="/portfolio">Portfolio</a><a href="/backtests">Backtest Lab</a><a href="/accounts">Accounts</a><a href="/">Status</a><a href="/logs">Logs</a><a href="/eas">EAs</a></nav></div></header>
<main>{body}</main></body></html>"""


def render_dashboard(connection: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    account_labels = _account_labels(connection)
    latest_heartbeats = connection.execute(
        "SELECT h.host_id, h.last_heartbeat_utc, c.payload_json FROM hosts h "
        "LEFT JOIN collector_heartbeats c ON c.heartbeat_id=("
        "SELECT MAX(heartbeat_id) FROM collector_heartbeats WHERE host_id=h.host_id) "
        "ORDER BY h.host_id"
    ).fetchall()
    hosts: dict[str, dict] = {}
    for host_id, last_seen, payload_json in latest_heartbeats:
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        seen = _parsed_time(last_seen)
        online = seen is not None and 0 <= (now - seen).total_seconds() <= STALE_AFTER_SECONDS
        hosts[host_id] = {"last_seen": last_seen, "online": online, "payload": payload}

    cutoff = (now - timedelta(hours=24)).isoformat()
    contains = "strpos" if getattr(connection, "is_postgres", False) else "instr"
    terminal_rows = connection.execute(
        "SELECT t.host_id, t.terminal_id, t.data_path, "
        "MAX(CASE WHEN e.stream='journal' THEN e.received_utc END), "
        "MAX(CASE WHEN e.stream='experts' THEN e.received_utc END), "
        "SUM(CASE WHEN e.received_utc>=? AND "
        f"({contains}(lower(e.raw_line),'error')>0 OR {contains}(lower(e.raw_line),'failed')>0) "
        "THEN 1 ELSE 0 END) "
        "FROM terminals t LEFT JOIN log_events e ON e.host_id=t.host_id AND e.terminal_id=t.terminal_id "
        "GROUP BY t.host_id,t.terminal_id,t.data_path ORDER BY t.host_id,t.data_path", (cutoff,)
    ).fetchall()
    snapshots = {
        (host_id, tid): (received, json.loads(status_json))
        for host_id, tid, received, status_json in connection.execute(
            "SELECT host_id,terminal_id,received_utc,status_json FROM terminal_status"
        )
    }
    observed = sum(len(host["payload"].get("terminals", [])) for host in hosts.values() if host["online"])
    offline = sum(not host["online"] for host in hosts.values())
    text_errors = sum(row[5] or 0 for row in terminal_rows)
    cards = "".join(
        f'<div class="card"><span class="muted">{label}</span><b>{value}</b></div>'
        for label, value in (
            ("Hosts", len(hosts)), ("Terminals in current heartbeats", observed),
            ("Collectors offline", offline), ("Error/failed text, 24h", text_errors),
        )
    )
    host_html = []
    for host_id, host in hosts.items():
        payload = host["payload"]
        status = '<span class="badge good">Collector online</span>' if host["online"] else '<span class="badge bad">Collector offline</span>'
        coverage = (
            f'Expected: {len(payload.get("expected", []))}; missing: {len(payload.get("missing", []))}; '
            f'archived: {len(payload.get("archived", []))}; unknown: {len(payload.get("unknown", []))}'
            if payload.get("coverage_configured") else "Expected terminals not configured"
        )
        host_html.append(
            '<div class="panel"><div class="host-line">'
            f'<strong>{_escape(host_id)}</strong>{status}'
            f'<span>Last heartbeat: {_escape(_display_time(host["last_seen"]))}</span>'
            f'<span>{_escape(coverage)}</span>'
            f'<span>Queued at scan: {_escape(payload.get("pending_count", "—"))}</span>'
            '</div></div>'
        )
    rows = []
    missing_count = 0
    for host_id, tid, path, journal_time, experts_time, errors in terminal_rows:
        host = hosts.get(host_id)
        process_html = '<span class="badge neutral">Unknown</span>'
        broker_html = '<span class="badge neutral">Unknown</span>'
        autotrading_html = '<span class="badge neutral">Unknown</span>'
        if not host or not host["online"]:
            folder_status = '<span class="badge neutral">Unknown: collector offline</span>'
        else:
            archived = path.casefold() in host["payload"].get("archived", [])
            current = next(
                (item for item in host["payload"].get("terminals", [])
                 if isinstance(item, dict) and item.get("terminal_id") == tid), None
            )
            if archived:
                folder_status = '<span class="badge neutral">Archived</span>'
            elif current:
                folder_status = '<span class="badge good">Found</span>'
            else:
                folder_status = '<span class="badge bad">Missing</span>'
                missing_count += 1
            if current and not archived:
                process = current.get("process", {})
                state = process.get("state") if isinstance(process, dict) else None
                if state == "running":
                    process_html = '<span class="badge good">Running</span>'
                elif state == "stopped":
                    process_html = '<span class="badge bad">Stopped</span>'
                elif isinstance(process, dict) and process.get("reason"):
                    process_html += f'<small>{_escape(process["reason"])}</small>'
                snapshot = snapshots.get((host_id, tid))
                if state == "running" and snapshot:
                    received, status = snapshot
                    age = now - (_parsed_time(received) or datetime.min.replace(tzinfo=timezone.utc))
                    if timedelta(0) <= age <= timedelta(seconds=STALE_AFTER_SECONDS):
                        if status.get("state") == "account_mismatch":
                            broker_html = '<span class="badge bad">Account mismatch</span>'
                        elif status.get("state") == "ok":
                            if status.get("connected") is True:
                                broker_html = '<span class="badge good">Connected</span>'
                            elif status.get("connected") is False:
                                broker_html = '<span class="badge bad">Disconnected</span>'
                            if status.get("autotrading") is True:
                                autotrading_html = '<span class="badge good">Enabled</span>'
                            elif status.get("autotrading") is False:
                                autotrading_html = '<span class="badge bad">Disabled</span>'
                        if status.get("reason"):
                            broker_html += f'<small>{_escape(status["reason"])}</small>'
                    else:
                        broker_html = '<span class="badge neutral">Stale probe</span>'
        log_link = "/logs?" + urlencode({"host": host_id, "terminal": tid})
        account = account_labels.get((host_id, tid), "Account unavailable")
        rows.append(
            "<tr>"
            f'<td><a href="{_escape(log_link)}">{_escape(account)}</a></td>'
            f'<td>{_escape(host_id)}</td><td>{folder_status}</td><td>{process_html}</td>'
            f'<td>{broker_html}</td><td>{autotrading_html}</td>'
            f'<td>{_escape(_display_time(journal_time))}</td><td>{_escape(_display_time(experts_time))}</td>'
            f'<td>{errors or 0}</td></tr>'
        )
    path_accounts = {
        (host_id, path.casefold()): account_labels.get((host_id, tid), "Account unavailable")
        for host_id, tid, path, *_ in terminal_rows
    }
    alert_rows = connection.execute(
        "SELECT host_id,alert_type,target,state,detail,changed_utc,last_sent_utc,last_error "
        "FROM alerts ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'recovering' THEN 1 ELSE 2 END, "
        "changed_utc DESC LIMIT 30"
    ).fetchall()
    alerts_html = "".join(
        '<tr>'
        f'<td>{_escape(host_id)}</td><td>{_escape(alert_type.replace("_", " "))}</td>'
        f'<td><span class="badge {"bad" if state == "active" else "warn" if state == "recovering" else "good"}">{_escape(state)}</span></td>'
        f'<td>{_escape(_alert_detail(alert_type, detail, host_id, target, path_accounts))}</td>'
        f'<td>{_escape(_display_time(changed))}</td>'
        f'<td>{_escape(_display_time(sent))}'
        + (f'<small>Delivery error: {_escape(error)}</small>' if error else '')
        + '</td></tr>'
        for host_id, alert_type, target, state, detail, changed, sent, error in alert_rows
    )
    body = (
        '<h1>Terminal status</h1>'
        '<p class="muted">Receive times are UTC. Quiet logs do not imply that MT5 is stopped.</p>'
        '<div class="note">Process state comes from the Windows process check. Broker and AutoTrading state '
        f'come from a separate account probe and become stale after {STALE_AFTER_SECONDS} seconds. Unknown means no verified reading.</div>'
        f'<div class="cards">{cards}</div>'
        '<h2>Collectors</h2>' + ('<div class="host-list">' + "".join(host_html) + '</div>' if host_html else '<p>No heartbeats yet.</p>')
        + f'<h2>Terminals <small class="muted">({len(rows)} known; {missing_count} missing from online hosts)</small></h2>'
        + '<div class="table-wrap"><table><thead><tr><th>Account</th><th>Host</th><th>Inventory</th><th>MT5 process</th>'
        '<th>Broker</th><th>AutoTrading</th><th>Last Journal</th><th>Last Experts</th><th>Error text, 24h</th></tr></thead><tbody>'
        + ("".join(rows) if rows else '<tr><td colspan="9">No terminals received yet.</td></tr>')
        + '</tbody></table></div>'
        + '<h2>Alerts</h2><div class="table-wrap"><table><thead><tr><th>Host</th><th>Type</th><th>State</th>'
        '<th>Detail</th><th>Changed</th><th>Last sent</th></tr></thead><tbody>'
        + (alerts_html if alerts_html else '<tr><td colspan="6">No alerts yet.</td></tr>')
        + '</tbody></table></div>'
    )
    return _page("Status", body)


def _date_param(params: dict[str, list[str]], name: str) -> str:
    value = (params.get(name, [""])[0] or "").strip()
    try:
        return date.fromisoformat(value).isoformat() if value else ""
    except ValueError:
        return ""


def _symbol_chart(metrics: dict[str, tuple[int, Decimal]], currency: str) -> str:
    if not metrics:
        return '<p class="muted">No closed trades with symbols in this period.</p>'
    max_count = max(count for count, _ in metrics.values()) or 1
    max_pnl = max(abs(pnl) for _, pnl in metrics.values()) or Decimal(1)
    rows = []
    for symbol, (count, pnl) in sorted(metrics.items(), key=lambda item: (-item[1][0], item[0])):
        count_width = 100 * count / max_count
        pnl_width = 100 * abs(pnl) / max_pnl
        pnl_class = "positive" if pnl >= 0 else "negative"
        rows.append(
            '<tr>'
            f'<th scope="row">{_escape(symbol)}</th>'
            f'<td><span class="metric-track"><span class="metric-fill" style="width:{count_width:.1f}%"></span></span>{count}</td>'
            f'<td><span class="metric-track"><span class="metric-fill {pnl_class}" style="width:{pnl_width:.1f}%"></span></span>'
            f'{pnl:.2f} {_escape(currency)}</td></tr>'
        )
    return ('<div class="table-wrap"><table class="symbol-table"><thead><tr>'
            '<th>Symbol</th><th>Closed deals</th><th>Trading PnL</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table></div>')


def _balance_chart(connection: sqlite3.Connection, server: str, login: int, currency: str,
                   earliest_day: str, latest_day: str, balance: str | None) -> str:
    from .analytics_web import line_chart
    if balance is None:
        return '<div class="chart-wrap"><p class="muted">Waiting for the next verified MT5 balance snapshot.</p></div>'
    daily: dict[str, Decimal] = {}
    for day, deal_type, profit, commission, swap, fee in connection.execute(
        "SELECT day_local,type,profit,commission,swap,fee FROM deals "
        "WHERE server=? AND login=? ORDER BY time_utc,ticket", (server, login),
    ):
        if deal_type == 3:  # MT5 credit is separate from account balance.
            continue
        delta = sum((Decimal(value) for value in (profit, commission, swap, fee)), Decimal(0))
        daily[day] = daily.get(day, Decimal(0)) + delta
    current = Decimal(balance)
    running = current - sum(daily.values(), Decimal(0))
    points = []
    for day, delta in sorted(daily.items()):
        running += delta
        points.append((day, running))
    if not points:
        points.append((earliest_day, current))
    points.append((max(latest_day, points[-1][0]), current))
    chart = line_chart(points, "Account balance · available history", currency,
                       chart_class="balance-chart", include_zero=False)
    return (chart + f'<p class="muted">Latest balance: {current:.2f} {_escape(currency)} · {_escape(latest_day)}</p>'
            '<p class="muted">Reconstructed from the latest verified balance and stored account deals; '
            'MT5 credit is excluded. Earlier history may not be available.</p>')


def render_accounts(connection: sqlite3.Connection, params: dict[str, list[str]], now: datetime | None = None) -> str:
    from .analytics_web import performance_panel, pnl_breakdown, strategy_table
    from .activity_web import activity_panels
    from .performance import account_performance, load_deals, strategy_stats, StrategyStats, RESULT_KINDS

    now = now or datetime.now(timezone.utc)
    from_day = _date_param(params, "from")
    to_day = _date_param(params, "to")
    invalid_period = bool(from_day and to_day and from_day > to_day)
    accounts = connection.execute(
        "SELECT server,login,currency,day_timezone,host_id,terminal_id,latest_snapshot_utc,history_start_day,balance "
        "FROM accounts ORDER BY server,login"
    ).fetchall()
    requested_server = (params.get("server", [""])[0] or "").strip()
    requested_login = (params.get("login", [""])[0] or "").strip()
    selected_account = next(
        (account for account in accounts
         if account[0] == requested_server and str(account[1]) == requested_login),
        accounts[0] if accounts else None,
    )
    reset_link = (
        "/accounts?" + urlencode({"server": selected_account[0], "login": selected_account[1]})
        if selected_account is not None else "/accounts"
    )
    tabs = []
    for account in accounts:
        server, login = account[:2]
        query = {"server": server, "login": str(login)}
        if from_day:
            query["from"] = from_day
        if to_day:
            query["to"] = to_day
        active = account == selected_account
        tabs.append(
            f'<a class="account-tab" href="/accounts?{_escape(urlencode(query))}"'
            + (' aria-current="page"' if active else '')
            + f'><span class="account-tab-login">{_escape(login)}</span>'
            f'<small>{_escape(server)}</small></a>'
        )
    sections = []
    for server, login, currency, day_timezone, host_id, tid, received, history_start, balance in (
        [selected_account] if selected_account is not None else []
    ):
        zone = day_zone(day_timezone)
        latest_day = (_parsed_time(received) or now).astimezone(zone).date().isoformat()
        earliest_row = connection.execute(
            "SELECT MIN(day_local) FROM deals WHERE server=? AND login=?", (server, login),
        ).fetchone()
        earliest_day = earliest_row[0] or history_start
        clauses = ["server=?", "login=?"]
        values: list[object] = [server, login]
        if from_day:
            clauses.append("day_local>=?")
            values.append(from_day)
        if to_day:
            clauses.append("day_local<=?")
            values.append(to_day)
        where = " WHERE " + " AND ".join(clauses)
        closed_count = 0
        symbol_metrics: dict[str, tuple[int, Decimal]] = {}
        if not invalid_period:
            for kind, entry, symbol, profit, commission, swap, fee in connection.execute(
                "SELECT kind,entry,symbol,profit,commission,swap,fee FROM deals" + where, values,
            ):
                amount = sum((Decimal(value) for value in (profit, commission, swap, fee)), Decimal(0))
                if kind == "trade":
                    closed_count += int(entry in (1, 2, 3))
                if kind == "trade" and symbol:
                    count, previous = symbol_metrics.get(symbol, (0, Decimal(0)))
                    is_close = entry in (1, 2, 3)
                    symbol_metrics[symbol] = (count + int(is_close), previous + amount)
        limit = deal_limit(params, 15, closed_count)
        closed_rows = connection.execute(
            "SELECT ticket,time_utc,position_id,symbol,strategy,volume,profit,commission,swap,fee "
            "FROM deals" + where + " AND kind='trade' AND entry IN (1,2,3) "
            "ORDER BY time_utc DESC,ticket DESC LIMIT ?", [*values, limit],
        ).fetchall() if not invalid_period else []
        closes_html = "".join(
            '<tr>'
            f'<td>{_escape(closed_at)}</td><td>{_escape(ticket)}</td>'
            f'<td>{_escape(position_id)}</td><td>{_escape(symbol)}</td>'
            f'<td><a href="{_escape(strategy_url(server, login, strategy, from_day, to_day))}">{_escape(strategy)}</a></td><td>{volume:g}</td>'
            f'<td>{sum((Decimal(value) for value in (profit, commission, swap, fee)), Decimal(0)):.2f} {_escape(currency)}</td>'
            '</tr>'
            for ticket, closed_at, position_id, symbol, strategy, volume, profit, commission, swap, fee in closed_rows
        )
        position_rows = connection.execute(
            "SELECT ticket,symbol,type,strategy,volume,price_open,price_current,profit,swap "
            "FROM positions WHERE server=? AND login=? ORDER BY symbol,ticket", (server, login),
        ).fetchall()
        status_row = connection.execute(
            "SELECT received_utc,status_json FROM terminal_status WHERE host_id=? AND terminal_id=?",
            (host_id, tid),
        ).fetchone()
        age = now - (_parsed_time(received) or datetime.min.replace(tzinfo=timezone.utc))
        complete_latest = bool(status_row and status_row[0] == received and
                               json.loads(status_row[1]).get("data_complete") is True)
        freshness = ('<span class="badge good">Current snapshot</span>' if complete_latest and timedelta(0) <= age <= timedelta(seconds=STALE_AFTER_SECONDS)
                     else '<span class="badge neutral">Stale snapshot</span>')
        period_label = f'{from_day or earliest_day} – {to_day or latest_day}'
        coverage = (f'Available recorded history starts at {_escape(earliest_day)}. '
                    'Earlier account activity may not be included.')
        positions_html = "".join(
            '<tr>'
            f'<td>{_escape(ticket)}</td><td>{_escape(symbol)}</td>'
            f'<td>{"Buy" if side == 0 else "Sell" if side == 1 else _escape(side)}</td>'
            f'<td><a href="{_escape(strategy_url(server, login, strategy, from_day, to_day))}">{_escape(strategy)}</a></td><td>{volume:g}</td>'
            f'<td>{price_open:g}</td><td>{price_current:g}</td>'
            f'<td>{_escape(profit)}</td><td>{_escape(swap)}</td></tr>'
            for ticket, symbol, side, strategy, volume, price_open, price_current, profit, swap in position_rows
        )
        recorded_deals = load_deals(connection, server, login)
        period_start, period_end = from_day or earliest_day, to_day or latest_day
        performance = account_performance(recorded_deals, balance, period_start, period_end)
        pnl_text = f'{performance.pnl:.2f} {_escape(currency)}'
        cash_text = f'{performance.cash:.2f} {_escape(currency)}'
        strategies = strategy_stats(recorded_deals, period_start, period_end)
        for _, symbol, _, strategy, _, _, _, profit, swap in position_rows:
            stats = strategies.setdefault(strategy, StrategyStats())
            stats.positions += 1
            stats.floating += Decimal(profit) + Decimal(swap)
            stats.symbols.add(symbol)
        sections.append(
            f'<section class="panel account-detail"><h2>{_escape(login)} @ {_escape(server)} {freshness}</h2>'
            f'<p class="muted">Host {_escape(host_id)} · '
            f'currency {_escape(currency)} · day zone {_escape(day_timezone)} · '
            f'last complete snapshot {_escape(_display_time(received))}</p>'
            + ('<p class="note">Start date must not be after end date.</p>' if invalid_period else '')
            + f'<div class="account-summary"><div><span class="muted">Trading PnL · {_escape(period_label)}</span><strong>{pnl_text}</strong></div>'
            f'<div><span class="muted">Cash transfers</span><strong>{cash_text}</strong></div>'
            f'<div><span class="muted">Closed deals</span><strong>{closed_count}</strong></div></div>'
            f'<p class="muted">{coverage}</p>'
            + performance_panel(performance, currency)
            + pnl_breakdown(recorded_deals, currency, period_start, period_end)
            + '<section class="account-block"><h3>Strategy PnL and quality</h3>'
            + strategy_table(strategies, currency, {name: strategy_url(server, login, name, from_day, to_day)
                             for name in strategies if name not in ("unallocated commission", "unallocated income / charges")}) + '</section>'
            + activity_panels([d for d in recorded_deals if d.kind in RESULT_KINDS and d.type != 3],
                              period_start, period_end, currency,
                              lambda lower, upper: "/accounts?" + urlencode({"server": server, "login": login, "from": lower, "to": upper}))
            + '<p><a href="/portfolio?'
            + _escape(urlencode({"currency": currency, "from": from_day, "to": to_day}))
            + '">Compare strategies across accounts →</a></p>'
            + '<section class="account-block"><h3>Account balance · available history</h3>'
            + _balance_chart(connection, server, login, currency, earliest_day, latest_day, balance)
            + '</section>'
            '<section class="account-block"><h3>Trades and PnL by symbol</h3>'
            '<p class="muted">Closed deal count; PnL includes entry and exit trading costs on each symbol.</p>'
            + _symbol_chart(symbol_metrics, currency) + '</section>'
            f'<section id="deals" class="account-block"><h3>Last {limit} closed deals</h3>'
            '<p class="muted">Latest exits in the selected period. Deal PnL includes that deal’s profit, commission, swap, and fee.</p>'
            '<div class="table-wrap"><table><thead><tr><th>Closed UTC</th><th>Deal</th><th>Position</th>'
            '<th>Symbol</th><th>Strategy</th><th>Volume</th><th>Deal PnL</th></tr></thead><tbody>'
            + (closes_html if closes_html else '<tr><td colspan="7">No closed deals in this period.</td></tr>')
            + '</tbody></table></div>'
            + deal_pagination("/accounts", {"server": server, "login": login, "from": from_day, "to": to_day},
                              len(closed_rows), closed_count, 15)
            + '</section>'
            '<details class="account-extra"><summary>Positions and strategy details</summary>'
            '<h3>Open positions</h3><div class="table-wrap"><table><thead><tr>'
            '<th>Ticket</th><th>Symbol</th><th>Side</th><th>Strategy</th><th>Volume</th>'
            '<th>Open</th><th>Current</th><th>Profit</th><th>Swap</th></tr></thead><tbody>'
            + (positions_html if positions_html else '<tr><td colspan="9">No open positions in the last complete snapshot.</td></tr>')
            + '</tbody></table></div></details></section>'
        )
    body = (
        '<h1>Accounts and PnL</h1>'
        '<p class="muted">Values come from verified read-only MT5 snapshots. Date ranges use each account’s configured time zone.</p>'
        + (
            '<div class="account-layout"><aside class="panel account-sidebar">'
            f'<h2>Accounts <small class="muted">({len(accounts)})</small></h2>'
            f'<nav class="account-tabs" aria-label="Accounts">{"".join(tabs)}</nav></aside>'
            '<div class="account-content"><form method="get" action="/accounts">'
            f'<input type="hidden" name="server" value="{_escape(selected_account[0])}">'
            f'<input type="hidden" name="login" value="{_escape(selected_account[1])}">'
            f'<label>From<input type="date" name="from" value="{_escape(from_day)}"></label>'
            f'<label>To<input type="date" name="to" value="{_escape(to_day)}"></label>'
            '<button type="submit">Apply period</button>'
            f'<a class="filter-reset" href="{_escape(reset_link)}">All history</a></form>'
            + "".join(sections) + '</div></div>'
            if selected_account is not None else '<p>No verified account snapshots yet.</p>'
        )
    )
    return _page("Accounts", body)


def render_eas(connection: sqlite3.Connection, now: datetime | None = None) -> str:
    """Show attached EAs from each terminal's most recent MQL5 service report."""
    now = now or datetime.now(timezone.utc)
    account_labels = _account_labels(connection)
    heartbeats = {}
    for host_id, received, payload_json in connection.execute(
        "SELECT h.host_id,h.last_heartbeat_utc,c.payload_json FROM hosts h "
        "LEFT JOIN collector_heartbeats c ON c.heartbeat_id=("
        "SELECT MAX(heartbeat_id) FROM collector_heartbeats WHERE host_id=h.host_id)"
    ):
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        terminals = payload.get("terminals", []) if isinstance(payload, dict) else []
        reports = {item.get("terminal_id"): item
                   for item in terminals if isinstance(item, dict)} if isinstance(terminals, list) else {}
        seen = _parsed_time(received)
        online = seen is not None and 0 <= (now - seen).total_seconds() <= STALE_AFTER_SECONDS
        heartbeats[host_id] = (online, reports)

    rows = []
    observed_count = 0
    terminals = connection.execute(
        "SELECT host_id,terminal_id FROM terminals ORDER BY host_id,terminal_id"
    ).fetchall()
    for host_id, terminal_id in terminals:
        online, reports = heartbeats.get(host_id, (False, {}))
        report = reports.get(terminal_id)
        probe = report.get("ea_probe") if isinstance(report, dict) else None
        account = account_labels.get((host_id, terminal_id), "Account unavailable")
        base = f"<td>{_escape(account)}</td><td>{_escape(host_id)}</td>"
        if not isinstance(probe, dict) or probe.get("state") != "ok":
            if not online:
                state = "Collector offline"
            elif report is None:
                state = "Terminal not seen"
            elif isinstance(probe, dict) and probe.get("state") == "invalid":
                state = "Invalid probe data"
            else:
                state = "No probe report"
            rows.append(f"<tr>{base}<td>—</td><td>—</td><td>—</td><td><span class='badge neutral'>{state}</span></td></tr>")
            continue
        observed = _parsed_time(probe.get("observed_at_utc"))
        process = report.get("process", {}) if isinstance(report, dict) else {}
        stopped = isinstance(process, dict) and process.get("state") == "stopped"
        fresh = bool(online and not stopped and observed and
                     0 <= (now - observed).total_seconds() <= EA_STALE_AFTER_SECONDS)
        stamp = _display_time(probe.get("observed_at_utc"))
        experts = probe.get("experts", [])
        if not isinstance(experts, list):
            experts = []
        if not experts:
            state = "No EA attached" if fresh else "Stale report"
            rows.append(f"<tr>{base}<td>—</td><td>—</td>"
                        f"<td>{_escape(stamp)}</td><td><span class='badge neutral'>{state}</span></td></tr>")
            continue
        for expert in experts:
            if not isinstance(expert, dict):
                continue
            if fresh:
                observed_count += 1
            state = "Attached" if fresh else "Stale report"
            badge = "good" if fresh else "neutral"
            chart = f"{expert.get('symbol', '—')} {expert.get('period', '')}"
            rows.append(f"<tr>{base}<td>{_escape(expert.get('name', '—'))}</td>"
                        f"<td>{_escape(chart)}</td><td>{_escape(stamp)}</td>"
                        f"<td><span class='badge {badge}'>{state}</span></td></tr>")
    body = (
        f"<h1>Expert Advisors</h1><p class='muted'>{observed_count} attached EAs observed recently "
        f"across {len(terminals)} registered terminals. An EA on a chart can still have trading disabled.</p>"
        "<p class='note'>Each terminal needs the DashboardEaProbe MQL5 service. Without a fresh report, "
        "this page does not claim that an EA is running.</p>"
        "<div class='table-wrap'><table><thead><tr><th>Account</th><th>Host</th><th>EA</th>"
        "<th>Chart</th><th>Last scan</th><th>State</th></tr></thead><tbody>"
        + ("".join(rows) if rows else "<tr><td colspan='6'>No registered terminals yet.</td></tr>")
        + "</tbody></table></div>"
    )
    return _page("Expert Advisors", body)


def render_logs(connection: sqlite3.Connection, params: dict[str, list[str]]) -> str:
    account_labels = _account_labels(connection)
    filters = {name: (params.get(name, [""])[0] or "").strip()[:200] for name in ("host", "terminal", "stream", "q")}
    clauses = []
    values: list[str] = []
    for name, column in (("host", "e.host_id"), ("terminal", "e.terminal_id"), ("stream", "e.stream")):
        if filters[name]:
            clauses.append(f"{column}=?")
            values.append(filters[name])
    if filters["q"]:
        contains = "strpos" if getattr(connection, "is_postgres", False) else "instr"
        clauses.append(f"{contains}(lower(e.raw_line), lower(?))>0")
        values.append(filters["q"])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = connection.execute(
        "SELECT e.received_utc,e.host_id,e.terminal_id,e.stream,e.raw_line "
        "FROM log_events e"
        + where + " ORDER BY e.received_utc DESC,e.event_id DESC LIMIT 200", values
    ).fetchall()
    options = ['<option value="">Both streams</option>']
    for value, label in (("journal", "Journal"), ("experts", "Experts")):
        selected = " selected" if filters["stream"] == value else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    account_options = ['<option value="">All accounts</option>']
    account_choices = sorted(
        ((host_id, tid, label) for (host_id, tid), label in account_labels.items()
         if not filters["host"] or host_id == filters["host"]),
        key=lambda item: (item[2].casefold(), item[0]),
    )
    for host_id, tid, label in account_choices:
        selected = " selected" if filters["terminal"] == tid else ""
        account_options.append(f'<option value="{_escape(tid)}"{selected}>{_escape(label)}</option>')
    if filters["terminal"] and not any(tid == filters["terminal"] for _, tid, _ in account_choices):
        account_options.append(
            f'<option value="{_escape(filters["terminal"])}" selected>Account unavailable</option>'
        )
    form = (
        '<form method="get" action="/logs">'
        f'<label>Host<input name="host" value="{_escape(filters["host"])}"></label>'
        f'<label>Account<select name="terminal">{"".join(account_options)}</select></label>'
        f'<label>Stream<select name="stream">{"".join(options)}</select></label>'
        f'<label>Text contains<input name="q" value="{_escape(filters["q"])}"></label>'
        '<button type="submit">Filter</button></form>'
    )
    log_rows = []
    for received, host_id, tid, stream, raw_line in rows:
        account = account_labels.get((host_id, tid), "Account unavailable")
        log_rows.append(
            '<tr>'
            f'<td>{_escape(_display_time(received))}</td>'
            f'<td>{_escape(account)}<small>{_escape(host_id)}</small></td>'
            f'<td>{_escape(stream)}</td>'
            f'<td><pre>{_escape(raw_line)}</pre></td></tr>'
        )
    body = (
        '<h1>Raw logs</h1><p class="muted">Newest 200 matches by server receive time (UTC). '
        'Lines may reach disk later than they appear in MT5.</p>'
        + form
        + '<div class="table-wrap"><table><thead><tr><th>Received</th><th>Account</th><th>Stream</th><th>Raw line</th></tr></thead><tbody>'
        + ("".join(log_rows) if log_rows else '<tr><td colspan="4">No matching lines.</td></tr>')
        + '</tbody></table></div>'
    )
    return _page("Logs", body)
