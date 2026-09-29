from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode


STALE_AFTER_SECONDS = 30


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


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_escape(title)} · MT5 dashboard</title>
<style>
:root {{ color-scheme: light; font-family: system-ui, sans-serif; background: #f5f7fa; color: #182434; }}
body {{ margin: 0; }} header {{ background: #10253f; color: white; padding: 1rem max(1rem, calc((100vw - 1200px)/2)); }}
header strong {{ font-size: 1.15rem; }} nav {{ display: inline-flex; gap: 1.25rem; margin-left: 2rem; }}
nav a {{ color: #d7e6ff; text-decoration: none; }} main {{ max-width: 1200px; margin: 2rem auto; padding: 0 1rem 3rem; }}
h1 {{ margin: 0 0 .35rem; font-size: 1.8rem; }} h2 {{ font-size: 1.15rem; }}
.muted {{ color: #607084; }} .note {{ background: #e9f0fa; border-left: 4px solid #4979b7; padding: .8rem 1rem; margin: 1rem 0; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: .8rem; margin: 1.4rem 0; }}
.card, .panel {{ background: white; border: 1px solid #dce3ec; border-radius: 8px; padding: 1rem; }}
.card b {{ display: block; font-size: 1.8rem; margin-top: .25rem; }} .host-list {{ display: grid; gap: .8rem; margin: 1rem 0 1.5rem; }}
.host-line {{ display: flex; flex-wrap: wrap; gap: .6rem 1.5rem; align-items: center; }}
.badge {{ display: inline-block; padding: .18rem .5rem; border-radius: 99px; font-size: .82rem; font-weight: 600; }}
.good {{ background: #e3f4e9; color: #126036; }} .bad {{ background: #fde7e7; color: #9b2424; }}
.neutral {{ background: #eaf0f7; color: #40546e; }} .warn {{ background: #fff0ce; color: #775100; }}
.table-wrap {{ overflow-x: auto; background: white; border: 1px solid #dce3ec; border-radius: 8px; }}
table {{ width: 100%; border-collapse: collapse; }} th,td {{ text-align: left; vertical-align: top; padding: .75rem; border-bottom: 1px solid #edf0f4; }}
th {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: #546579; }}
td small {{ display: block; color: #607084; margin-top: .2rem; }} a {{ color: #245aa0; }}
form {{ display: flex; flex-wrap: wrap; gap: .65rem; align-items: end; margin: 1.2rem 0; }}
label {{ display: grid; gap: .25rem; font-size: .85rem; color: #40546e; }} input,select,button {{ font: inherit; padding: .48rem .6rem; border: 1px solid #aebccc; border-radius: 5px; }}
button {{ background: #245aa0; color: white; border-color: #245aa0; cursor: pointer; }}
pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: .84rem/1.45 ui-monospace,monospace; }}
</style></head><body><header><strong>MT5 dashboard</strong><nav><a href="/">Status</a><a href="/logs">Logs</a></nav></header>
<main>{body}</main></body></html>"""


def render_dashboard(connection: sqlite3.Connection, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
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
    terminal_rows = connection.execute(
        "SELECT t.host_id, t.terminal_id, t.data_path, "
        "MAX(CASE WHEN e.stream='journal' THEN e.received_utc END), "
        "MAX(CASE WHEN e.stream='experts' THEN e.received_utc END), "
        "SUM(CASE WHEN e.received_utc>=? AND "
        "(instr(lower(e.raw_line),'error')>0 OR instr(lower(e.raw_line),'failed')>0) "
        "THEN 1 ELSE 0 END) "
        "FROM terminals t LEFT JOIN log_events e ON e.host_id=t.host_id AND e.terminal_id=t.terminal_id "
        "GROUP BY t.host_id,t.terminal_id,t.data_path ORDER BY t.host_id,t.data_path", (cutoff,)
    ).fetchall()
    observed = sum(len(host["payload"].get("terminals", [])) for host in hosts.values() if host["online"])
    offline = sum(not host["online"] for host in hosts.values())
    text_errors = sum(row[5] or 0 for row in terminal_rows)
    cards = "".join(
        f'<div class="card"><span class="muted">{label}</span><b>{value}</b></div>'
        for label, value in (
            ("Hosts", len(hosts)), ("Folders in current heartbeats", observed),
            ("Collectors offline", offline), ("Error/failed text, 24h", text_errors),
        )
    )
    host_html = []
    for host_id, host in hosts.items():
        payload = host["payload"]
        status = '<span class="badge good">Collector online</span>' if host["online"] else '<span class="badge bad">Collector offline</span>'
        coverage = (
            f'Missing: {len(payload.get("missing", []))}; unknown: {len(payload.get("unknown", []))}'
            if payload.get("coverage_configured") else "Expected folders not configured"
        )
        host_html.append(
            '<div class="panel host-line">'
            f'<strong>{_escape(host_id)}</strong>{status}'
            f'<span>Last heartbeat: {_escape(_display_time(host["last_seen"]))}</span>'
            f'<span>{_escape(coverage)}</span>'
            f'<span>Queued at scan: {_escape(payload.get("pending_count", "—"))}</span>'
            '</div>'
        )
    rows = []
    missing_count = 0
    for host_id, tid, path, journal_time, experts_time, errors in terminal_rows:
        host = hosts.get(host_id)
        if not host or not host["online"]:
            folder_status = '<span class="badge neutral">Unknown: collector offline</span>'
        else:
            discovered_ids = {item.get("terminal_id") for item in host["payload"].get("terminals", []) if isinstance(item, dict)}
            if tid in discovered_ids:
                folder_status = '<span class="badge good">Folder found</span>'
            else:
                folder_status = '<span class="badge bad">Folder missing</span>'
                missing_count += 1
        log_link = "/logs?" + urlencode({"host": host_id, "terminal": tid})
        rows.append(
            "<tr>"
            f'<td><a href="{_escape(log_link)}">{_escape(path.rsplit(chr(92), 1)[-1])}</a><small>{_escape(path)}</small></td>'
            f'<td>{_escape(host_id)}</td><td>{folder_status}<small>Process: not checked</small></td>'
            f'<td>{_escape(_display_time(journal_time))}</td><td>{_escape(_display_time(experts_time))}</td>'
            f'<td>{errors or 0}</td></tr>'
        )
    body = (
        '<h1>Collector and terminal folders</h1>'
        '<p class="muted">Receive times are UTC. Quiet logs do not imply that MT5 is stopped.</p>'
        '<div class="note">Process, broker connection, and AutoTrading states need a separate terminal probe. '
        'The table currently reports folder discovery and log receipt.</div>'
        f'<div class="cards">{cards}</div>'
        '<h2>Collectors</h2>' + ('<div class="host-list">' + "".join(host_html) + '</div>' if host_html else '<p>No heartbeats yet.</p>')
        + f'<h2>Terminals <small class="muted">({len(rows)} known; {missing_count} folders missing from online hosts)</small></h2>'
        + '<div class="table-wrap"><table><thead><tr><th>Data folder</th><th>Host</th><th>Folder state</th>'
        '<th>Last Journal</th><th>Last Experts</th><th>Error text, 24h</th></tr></thead><tbody>'
        + ("".join(rows) if rows else '<tr><td colspan="6">No terminals received yet.</td></tr>')
        + '</tbody></table></div>'
    )
    return _page("Status", body)


def render_logs(connection: sqlite3.Connection, params: dict[str, list[str]]) -> str:
    filters = {name: (params.get(name, [""])[0] or "").strip()[:200] for name in ("host", "terminal", "stream", "q")}
    clauses = []
    values: list[str] = []
    for name, column in (("host", "e.host_id"), ("terminal", "e.terminal_id"), ("stream", "e.stream")):
        if filters[name]:
            clauses.append(f"{column}=?")
            values.append(filters[name])
    if filters["q"]:
        clauses.append("instr(lower(e.raw_line), lower(?))>0")
        values.append(filters["q"])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = connection.execute(
        "SELECT e.received_utc,e.host_id,e.terminal_id,t.data_path,e.stream,e.file_name,e.raw_line "
        "FROM log_events e LEFT JOIN terminals t ON t.host_id=e.host_id AND t.terminal_id=e.terminal_id"
        + where + " ORDER BY e.received_utc DESC,e.rowid DESC LIMIT 200", values
    ).fetchall()
    options = ['<option value="">Both streams</option>']
    for value, label in (("journal", "Journal"), ("experts", "Experts")):
        selected = " selected" if filters["stream"] == value else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    form = (
        '<form method="get" action="/logs">'
        f'<label>Host<input name="host" value="{_escape(filters["host"])}"></label>'
        f'<label>Terminal ID<input name="terminal" value="{_escape(filters["terminal"])}"></label>'
        f'<label>Stream<select name="stream">{"".join(options)}</select></label>'
        f'<label>Text contains<input name="q" value="{_escape(filters["q"])}"></label>'
        '<button type="submit">Filter</button></form>'
    )
    log_rows = []
    for received, host_id, tid, path, stream, file_name, raw_line in rows:
        folder = path.rsplit(chr(92), 1)[-1] if path else tid
        log_rows.append(
            '<tr>'
            f'<td>{_escape(_display_time(received))}</td>'
            f'<td>{_escape(host_id)}<small>{_escape(folder)}</small></td>'
            f'<td>{_escape(stream)}<small>{_escape(file_name)}</small></td>'
            f'<td><pre>{_escape(raw_line)}</pre></td></tr>'
        )
    body = (
        '<h1>Raw logs</h1><p class="muted">Newest 200 matches by server receive time (UTC). '
        'Lines may reach disk later than they appear in MT5.</p>'
        + form
        + '<div class="table-wrap"><table><thead><tr><th>Received</th><th>Host / folder</th><th>Stream / file</th><th>Raw line</th></tr></thead><tbody>'
        + ("".join(log_rows) if log_rows else '<tr><td colspan="4">No matching lines.</td></tr>')
        + '</tbody></table></div>'
    )
    return _page("Logs", body)
