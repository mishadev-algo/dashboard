"""Prepare editable proposals without changing running tasks or host configuration."""
from __future__ import annotations

import argparse
import json
import ntpath
import socket
import subprocess
import sys
from pathlib import Path

from shared.network import server_origin
from shared.paths import windows_path
from shared.timezones import day_zone
from .accounts import load_targets, validate_account_coverage
from .core import discover
from .inventory import load_inventory
from .process import ProcessSnapshot, classify_terminal, list_terminal_processes


def inventory_proposal(roots: list[Path], terminals: list[Path], processes: ProcessSnapshot) -> tuple[dict, list[str]]:
    portable = [Path(ntpath.dirname(exe)) for _, exe in processes.processes if exe]
    found = discover(roots, [*terminals, *portable])
    expected, archived, matched, issues = [], [], set(), []
    if not processes.complete or processes.error:
        issues.append(processes.error or "Some MT5 executable paths are inaccessible")
    for path in found:
        state = classify_terminal(path, processes)
        if state["state"] == "running":
            expected.append(str(path))
            matched.add(state["pid"])
        elif state["state"] == "stopped":
            archived.append(str(path))
        else:
            issues.append(f"Cannot classify data folder: {path}")
    for pid, exe in processes.processes:
        if pid not in matched:
            issues.append(f"No data folder matched for active MT5 PID {pid}: {exe}")
    if not expected:
        issues.append("No running MT5 data folders found")
    return {"expected": expected, "archived": archived}, issues


def account_identity(path: Path, processes: ProcessSnapshot) -> dict:
    state = classify_terminal(path, processes)
    if state["state"] != "running":
        return {"error": "Terminal must be running during preparation"}
    executable = next(exe for pid, exe in processes.processes if pid == state["pid"])
    try:
        result = subprocess.run([sys.executable, "-m", "collector.mt5_identity",
                                 "--data-path", str(path), "--exe", executable,
                                 "--pid", str(state["pid"])], capture_output=True, text=True,
                                encoding="utf-8", timeout=30, check=False)
        value = json.loads(result.stdout) if not result.returncode else {}
        if isinstance(value, dict) and (value.get("error") or
                (type(value.get("login")) is int and value["login"] > 0 and value.get("server"))):
            return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {"error": "Account discovery failed; enter and verify login/server manually"}


def validate_proposals(inventory_file: Path, accounts_file: Path | None) -> None:
    inventory = load_inventory(inventory_file)
    if not inventory.expected:
        raise ValueError("inventory.expected must contain running terminals")
    processes = list_terminal_processes()
    if not processes.complete or processes.error:
        raise ValueError(processes.error or "Some MT5 process paths are inaccessible")
    by_install: dict[int, list[str]] = {}
    for path in inventory.expected:
        if classify_terminal(path, processes)["state"] != "running":
            raise ValueError(f"Expected terminal is not confirmed running: {path}")
        pid = classify_terminal(path, processes)["pid"]
        by_install.setdefault(pid, []).append(str(path))
    if any(len(paths) > 1 for paths in by_install.values()):
        raise ValueError("Several data folders map to one MT5 process; retain the verified folder only")
    if {pid for pid, _ in processes.processes} != set(by_install):
        raise ValueError("Not every running MT5 is covered; add its verified data folder to inventory.expected")
    if accounts_file:
        targets = load_targets(accounts_file)
        validate_account_coverage(inventory.expected, targets)
        if {windows_path(str(t.data_path)) for t in targets} != {windows_path(str(p)) for p in inventory.expected}:
            raise ValueError("Accounts profile requires one account target per expected terminal")
        for target in targets:
            observed = account_identity(target.data_path, processes)
            if observed.get("login") and (observed["login"] != target.login or observed["server"] != target.server):
                raise ValueError(f"Account mismatch: {target.data_path}")
            if observed.get("error"):
                print(f"WARNING: {target.data_path}: {observed['error']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("host-setup"))
    parser.add_argument("--profile", choices=("Logs", "Accounts", "Full"), default="Full")
    parser.add_argument("--server-url")
    parser.add_argument("--host-id", default=socket.gethostname())
    parser.add_argument("--day-timezone", default="UTC")
    parser.add_argument("--root", type=Path, action="append", default=[])
    parser.add_argument("--terminal", type=Path, action="append", default=[])
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate:
            plan = json.loads((args.output / "plan.json").read_text(encoding="utf-8-sig"))
            if plan["hostname"] != socket.gethostname():
                raise ValueError("Plan belongs to another computer; prepare it on this VPS")
            server_origin(plan["server_url"])
            validate_proposals(args.output / "inventory.json",
                               args.output / "accounts.json" if plan["profile"] != "Logs" else None)
            return
        if not args.server_url:
            raise ValueError("--server-url is required for preparation")
        origin = server_origin(args.server_url)
        if not args.host_id.strip():
            raise ValueError("host-id must not be empty")
        day_zone(args.day_timezone)
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError("Proposal directory is not empty; use a new --output or review the existing proposal")
        processes = list_terminal_processes()
        inventory, issues = inventory_proposal(args.root, args.terminal, processes)
        accounts, observations = [], []
        if args.profile != "Logs":
            for folder in inventory["expected"]:
                observed = account_identity(Path(folder), processes)
                observations.append({"data_path": folder, **observed})
                accounts.append({"data_path": folder, "login": observed.get("login", 0),
                                 "server": observed.get("server", ""), "day_timezone": args.day_timezone,
                                 "history_days": 7, "strategies": {}})
                if observed.get("error"):
                    issues.append(f"{folder}: {observed['error']}; fill login/server in accounts.json")
        plan = {"version": 1, "hostname": socket.gethostname(), "host_id": args.host_id,
                "server_url": origin, "profile": args.profile, "python": sys.executable,
                "roots": [str(p.resolve()) for p in args.root],
                "terminals": [str(p.resolve()) for p in args.terminal],
                "issues": issues, "observations": observations}
        args.output.mkdir(parents=True, exist_ok=True)
        files = {"inventory.json": inventory, "plan.json": plan}
        if args.profile != "Logs":
            files["accounts.json"] = {"terminals": accounts}
        for name, value in files.items():
            (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        print(f"Review proposals in {args.output}; preparation did not install tasks.")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
