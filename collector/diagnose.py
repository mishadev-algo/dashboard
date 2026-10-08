"""Inspect this Windows collector; a fresh acknowledged heartbeat proves delivery."""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from shared.cadence import STALE_AFTER_SECONDS
from shared.network import server_origin
from shared.paths import windows_path
from shared.runtime_state import read_state
from shared.sqlite import open_readonly
from .accounts import load_targets
from .core import resolve_host_id
from .ea_probe import read_probe
from .inventory import load_inventory
from .process import classify_terminal, list_terminal_processes
from .remote import pending_count


def fresh(value: object, now: datetime, seconds: int = STALE_AFTER_SECONDS) -> bool:
    try:
        age = (now - datetime.fromisoformat(str(value).replace("Z", "+00:00"))).total_seconds()
        return -60 <= age <= seconds
    except (TypeError, ValueError):
        return False


def delivery_findings(state: dict, host_id: str, origin: str, now: datetime) -> list[str]:
    issues = []
    if state.get("host_id") != host_id or state.get("server_url") != origin:
        issues.append("Collector receipt missing or belongs to another host/origin; start the updated task")
    else:
        if not fresh(state.get("last_scan_utc"), now, 90):
            issues.append("Collector scan is missing or stale")
        if not fresh(state.get("last_upload_utc"), now):
            issues.append("No fresh acknowledged heartbeat; inspect collector log and token registration")
        if state.get("upload_error"):
            issues.append("Last upload failed; inspect collector log")
    return issues


def task_states() -> dict:
    command = "Get-ScheduledTask -TaskName 'MT5CollectorRemote','MT5DashboardMonitor' -ErrorAction SilentlyContinue | Select-Object TaskName,@{Name='State';Expression={[string]$_.State}} | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            return {}
        records = json.loads(result.stdout) if result.stdout.strip() else []
        if isinstance(records, dict):
            records = [records]
        return {item["TaskName"]: item["State"] for item in records}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def diagnose(project: Path, profile: str, online: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    config = read_state(project / ".dashboard-remote.local.json")
    origin = server_origin(config.get("server_url", ""))
    inventory = load_inventory(project / config["inventory"])
    db = project / config["collector_db"]
    with closing(open_readonly(db)) as connection:
        host_id = resolve_host_id(connection, None, socket.gethostname())
        pending = pending_count(connection)
        latest_event = connection.execute("SELECT MAX(delivered_utc) FROM log_events").fetchone()[0]
    collector_state = read_state(db.parent / ".dashboard-collector-state.json")
    issues = delivery_findings(collector_state, host_id, origin, now)
    if {windows_path(p) for p in collector_state.get("expected", [])} != {windows_path(str(p)) for p in inventory.expected}:
        issues.append("Running collector coverage differs from inventory; restart the updated task after reviewing config")
    if collector_state.get("missing") or collector_state.get("unknown"):
        issues.append("Collector reports missing or unclassified data folders")
    processes = list_terminal_processes()
    tasks = task_states()
    if tasks.get("MT5CollectorRemote") != "Running":
        issues.append("MT5CollectorRemote is not confirmed Running")
    if not inventory.expected:
        issues.append("inventory.expected is empty")
    targets = {}
    if profile != "Logs":
        if not config.get("accounts"):
            issues.append("Account worker is not configured")
        else:
            targets = {windows_path(str(t.data_path)): t for t in load_targets(project / config["accounts"])}
    receipts = read_state((project / config.get("accounts", "accounts.json")).parent / ".dashboard-accounts-state.json")
    receipt_owner_ok = receipts.get("host_id") == host_id and receipts.get("server_url") == origin
    terminals = []
    for path in inventory.expected:
        state = classify_terminal(path, processes)
        item = {"data_path": str(path), "process": state}
        if state["state"] != "running":
            issues.append(f"Terminal not confirmed running: {path}")
        key = windows_path(str(path))
        target = targets.get(key)
        if profile != "Logs":
            receipt = receipts.get("terminals", {}).get(key, {}) if receipt_owner_ok else {}
            status = receipt.get("status", {})
            item["account"] = receipt
            configured = bool(target and receipt.get("expected_login") == target.login
                              and receipt.get("expected_server") == target.server
                              and receipt.get("day_timezone") == target.day_timezone
                              and receipt.get("history_days") == target.history_days)
            if not configured or not fresh(receipt.get("last_upload_utc"), now) or status.get("data_complete") is not True or status.get("state") != "ok" or status.get("connected") is not True:
                issues.append(f"No fresh complete acknowledged account snapshot: {path}")
            elif status.get("autotrading") is False:
                item["notice"] = "AutoTrading disabled in MT5; installer does not change trading settings"
        if profile == "Full":
            probe = read_probe(path)
            item["ea_probe"] = probe
            if probe.get("state") != "ok" or not fresh(probe.get("observed_at_utc"), now):
                issues.append(f"EA service report missing/invalid/stale; start one service instance in MT5: {path}")
            elif target and (probe.get("login") != target.login or probe.get("server") != target.server):
                issues.append(f"EA report account differs from configuration: {path}")
        terminals.append(item)
    if profile == "Full":
        monitor = read_state(project / ".dashboard-monitor-state.json")
        log = project / "run-logs" / "monitor.log"
        recent_log = log.is_file() and -60 <= now.timestamp() - log.stat().st_mtime <= 90
        verified_check = monitor.get("url") == origin + "/health" and fresh(monitor.get("checked_at_utc"), now, 90)
        if tasks.get("MT5DashboardMonitor") != "Running" or monitor.get("healthy") is not True or monitor.get("failures") != 0 or not recent_log or not verified_check:
            issues.append("Availability monitor is not confirmed Running with a recent healthy check")
    health = "not requested"
    if online:
        try:
            with urlopen(origin + "/health", timeout=15) as response:
                health = json.load(response).get("status")
            if health != "ok":
                issues.append("Central /health did not return ok")
        except (OSError, ValueError):
            health = "unavailable"
            issues.append("Central HTTPS health check failed; check network/TLS")
    return {"host_id": host_id, "profile": profile, "server_url": origin,
            "checked_at_utc": now.isoformat(), "tasks": tasks, "pending": pending,
            "latest_event_delivery_utc": latest_event, "health": health,
            "terminals": terminals, "issues": issues, "ready": not issues}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--profile", choices=("Logs", "Accounts", "Full"), default="Full")
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    try:
        report = diagnose(args.project, args.profile, args.online)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
