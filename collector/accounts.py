"""Configuration and read-only snapshot delivery for known MT5 accounts."""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from shared.cadence import UPLOAD_INTERVAL_SECONDS
from shared.network import server_origin
from shared.paths import windows_path
from shared.timezones import day_zone

from .core import terminal_id
from .process import ProcessSnapshot, classify_terminal, install_path, list_terminal_processes


@dataclass(frozen=True)
class AccountTarget:
    data_path: Path
    login: int
    server: str
    day_timezone: str
    history_days: int
    strategies: dict[int, str]


def load_targets(path: Path) -> tuple[AccountTarget, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read account config {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"terminals"} or not isinstance(raw["terminals"], list):
        raise ValueError("account config must contain a terminals list")
    targets = []
    seen = set()
    for item in raw["terminals"]:
        if not isinstance(item, dict) or set(item) - {"data_path", "login", "server", "day_timezone", "history_days", "strategies"}:
            raise ValueError("invalid account target")
        data_path = item.get("data_path")
        login = item.get("login")
        server = item.get("server")
        day_timezone = item.get("day_timezone", "UTC")
        history_days = item.get("history_days", 7)
        strategies = item.get("strategies", {})
        if not isinstance(data_path, str) or not (Path(data_path).is_absolute() or ntpath.isabs(data_path)):
            raise ValueError("account data_path must be absolute")
        if type(login) is not int or login <= 0 or not isinstance(server, str) or not server.strip():
            raise ValueError("account login and server are required")
        if type(history_days) is not int or not 1 <= history_days <= 90:
            raise ValueError("history_days must be between 1 and 90")
        day_zone(day_timezone)
        if not isinstance(strategies, dict) or any(
            not str(key).isdigit() or not isinstance(value, str) or not value.strip()
            for key, value in strategies.items()
        ):
            raise ValueError("strategies must map magic numbers to names")
        normalized = windows_path(data_path)
        if normalized in seen:
            raise ValueError("duplicate account data_path")
        seen.add(normalized)
        targets.append(AccountTarget(
            Path(data_path), login, server.strip(), day_timezone, history_days,
            {int(key): value.strip() for key, value in strategies.items()},
        ))
    if not targets:
        raise ValueError("account config must contain at least one terminal")
    return tuple(targets)


def validate_account_coverage(expected: tuple[Path, ...], targets: tuple[AccountTarget, ...]) -> None:
    known = {windows_path(str(path)) for path in expected}
    missing = [str(target.data_path) for target in targets
               if windows_path(str(target.data_path)) not in known]
    if missing:
        raise ValueError("account target(s) missing from inventory expected: " + ", ".join(missing))


def probe_target(target: AccountTarget, host_id: str, processes: ProcessSnapshot,
                 timeout: float = 30) -> dict:
    """Run the MT5 API in a fresh process only for a confirmed running executable."""
    process = classify_terminal(target.data_path, processes)
    status: dict = {"state": process["state"]}
    if process["state"] != "running":
        if process.get("reason"):
            status["reason"] = process["reason"]
    else:
        install = install_path(target.data_path)
        executable = next(
            (path for pid, path in processes.processes if pid == process["pid"] and path), None
        )
        if not install or not executable:
            status = {"state": "unknown", "reason": "MT5 executable path unavailable"}
        else:
            command = [
                sys.executable, "-m", "collector.mt5_snapshot", "--data-path", str(target.data_path),
                "--exe", executable, "--pid", str(process["pid"]),
                "--login", str(target.login), "--server", target.server,
                "--day-timezone", target.day_timezone, "--history-days", str(target.history_days),
            ]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
                if completed.returncode:
                    status = {"state": "unknown", "reason": "MT5 account probe failed"}
                else:
                    result = json.loads(completed.stdout)
                    if not isinstance(result, dict) or not isinstance(result.get("status"), dict):
                        raise ValueError("invalid MT5 probe output")
                    status = result["status"]
                    if result.get("account") and result.get("data_complete") is True:
                        status["data_complete"] = True
                        account = result["account"]
                        positions = result["positions"]
                        deals = result["deals"]
                        history_start_day = result["history_start_day"]
                    else:
                        account = positions = deals = None
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
                status = {"state": "unknown", "reason": "MT5 account probe unavailable"}
    payload = {
        "host_id": host_id, "terminal_id": terminal_id(host_id, target.data_path),
        "data_path": str(target.data_path), "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_login": target.login, "expected_server": target.server,
        "day_timezone": target.day_timezone, "status": status,
    }
    if status.get("data_complete"):
        payload.update({"account": account, "positions": positions, "deals": deals,
                        "history_start_day": history_start_day,
                        "strategies": {str(key): value for key, value in target.strategies.items()}})
    return payload


def upload_snapshot(server_url: str, host_id: str, token: str, payload: dict) -> None:
    if not token:
        raise ValueError("collector token is required")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > 4 * 1024 * 1024:
        raise ValueError("account snapshot exceeds 4 MiB; reduce history_days")
    request = Request(
        server_origin(server_url) + "/v1/snapshot", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            acknowledgement = json.load(response)
    except HTTPError as exc:
        exc.close()
        raise RuntimeError(f"snapshot upload failed: {exc}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"snapshot upload failed: {exc}") from exc
    if not isinstance(acknowledgement, dict) or acknowledgement.get("terminal_id") != payload["terminal_id"]:
        raise RuntimeError("snapshot acknowledgement does not match terminal")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only MT5 connection and account snapshot worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host-id", default=socket.gethostname())
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--interval", type=float, default=UPLOAD_INTERVAL_SECONDS)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("interval must be positive")
    try:
        targets = load_targets(args.config)
        server_origin(args.server_url)
        token = os.environ["DASHBOARD_COLLECTOR_TOKEN"]
        if not token:
            raise ValueError("DASHBOARD_COLLECTOR_TOKEN is required")
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    try:
        while True:
            processes = list_terminal_processes()
            for target in targets:
                payload = probe_target(target, args.host_id, processes)
                try:
                    upload_snapshot(args.server_url, args.host_id, token, payload)
                    print(f"account_probe={target.data_path} state={payload['status']['state']} uploaded=1", flush=True)
                except (RuntimeError, ValueError) as exc:
                    print(f"account_probe={target.data_path} upload_error={exc}", flush=True)
            if not args.follow:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
