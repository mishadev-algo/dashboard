"""Run the dashboard services in separate processes from one console."""

from __future__ import annotations

import argparse
import os
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
from typing import Mapping

from collector.core import resolve_host_id
from collector.inventory import load_inventory
from collector.accounts import load_targets, validate_account_coverage
from shared.config import host_tokens as load_host_tokens
from shared.sqlite import open_readonly


@dataclass(frozen=True)
class Service:
    name: str
    command: tuple[str, ...]


def service_plan(args: argparse.Namespace, environ: Mapping[str, str]) -> tuple[str, tuple[Service, ...]]:
    """Validate one-host configuration before starting any process."""
    if not args.db.is_file() or not args.collector_db.is_file():
        raise ValueError("central.db and the existing collector DB must both exist")
    if args.db.resolve() == args.collector_db.resolve():
        raise ValueError("central and collector databases must be different files")
    if not args.inventory.is_file():
        raise ValueError("inventory file is missing")
    inventory = load_inventory(args.inventory)
    if not inventory.expected:
        raise ValueError("inventory must list the expected running terminals")
    with closing(open_readonly(args.collector_db)) as connection:
        host_id = resolve_host_id(connection, args.host_id, socket.gethostname())
    tokens = load_host_tokens(environ)
    if host_id not in tokens:
        raise ValueError(f"DASHBOARD_HOST_TOKENS has no token for collector host {host_id!r}")
    token = environ.get("DASHBOARD_COLLECTOR_TOKEN", "")
    if not token:
        raise ValueError("DASHBOARD_COLLECTOR_TOKEN is not set in this PowerShell window")
    if token != tokens[host_id]:
        raise ValueError(f"DASHBOARD_COLLECTOR_TOKEN differs from DASHBOARD_HOST_TOKENS for host {host_id!r}")
    if args.port < 1 or args.port > 65535:
        raise ValueError("port must be between 1 and 65535")
    if args.accounts:
        validate_account_coverage(inventory.expected, load_targets(args.accounts))
    if not args.no_alerts and not all(environ.get(key) for key in (
        "DASHBOARD_TELEGRAM_BOT_TOKEN", "DASHBOARD_TELEGRAM_CHAT_ID",
    )):
        raise ValueError("set both Telegram environment variables or use --no-alerts")

    origin = f"http://127.0.0.1:{args.port}"
    python = sys.executable
    collector = [
        python, "-u", "-m", "collector", "--db", str(args.collector_db),
        "--host-id", host_id, "--inventory", str(args.inventory),
        "--server-url", origin, "--follow",
    ]
    for root in args.root:
        collector.extend(("--root", str(root)))
    for terminal in args.terminal:
        collector.extend(("--terminal", str(terminal)))
    services = [
        Service("server", (python, "-u", "-m", "server", "--db", str(args.db), "--port", str(args.port))),
        Service("collector", tuple(collector)),
    ]
    if not args.no_alerts:
        services.append(Service("alerts", (python, "-u", "-m", "server.alerts", "--db", str(args.db))))
    if args.accounts:
        services.append(Service("accounts", (
            python, "-u", "-m", "collector.accounts", "--config", str(args.accounts),
            "--host-id", host_id, "--server-url", origin, "--follow",
        )))
    return host_id, tuple(services)


def _reader(name: str, stream, lines: queue.Queue[tuple[str, str]]) -> None:
    for line in stream:
        lines.put((name, line.rstrip("\r\n")))


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_for_server(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited with code {process.returncode}")
        if _port_is_open(port):
            return
        time.sleep(0.2)
    raise RuntimeError("server did not open its loopback port within 15 seconds")


def _stop_all(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for _, process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            pass
    deadline = time.monotonic() + 5
    for _, process in reversed(processes):
        if process.poll() is not None:
            continue
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.terminate()
    for _, process in reversed(processes):
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def supervise(services: tuple[Service, ...], port: int | None, directory: Path) -> None:
    if port is not None and _port_is_open(port):
        raise RuntimeError(f"port {port} is already in use; stop the old server first")
    processes: list[tuple[str, subprocess.Popen]] = []
    lines: queue.Queue[tuple[str, str]] = queue.Queue()
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        for service in services:
            process = subprocess.Popen(
                service.command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=flags,
            )
            processes.append((service.name, process))
            threading.Thread(target=_reader, args=(service.name, process.stdout, lines), daemon=True).start()
            if service.name == "server" and port is not None:
                _wait_for_server(process, port)
            print(f"started {service.name} (PID {process.pid})", flush=True)
        print("All services started. Press Ctrl+C to stop them together.", flush=True)
        while True:
            try:
                name, line = lines.get(timeout=0.5)
                print(f"[{name}] {line}", flush=True)
            except queue.Empty:
                pass
            for name, process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited with code {process.returncode}; stopping the other services")
    finally:
        _stop_all(processes)
        while not lines.empty():
            name, line = lines.get_nowait()
            print(f"[{name}] {line}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run dashboard services from one VPS console")
    parser.add_argument("--db", type=Path, required=True, help="Existing central SQLite database")
    parser.add_argument("--collector-db", type=Path, required=True, help="Existing local collector SQLite database")
    parser.add_argument("--inventory", type=Path, default=Path("inventory.json"))
    parser.add_argument("--accounts", type=Path, help="Account config; starts the account worker when supplied")
    parser.add_argument("--host-id", help="Must match the collector database's host ID")
    parser.add_argument("--root", type=Path, action="append", default=[], help="Existing collector scan root; repeatable")
    parser.add_argument("--terminal", type=Path, action="append", default=[], help="Exact portable terminal; repeatable")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-alerts", action="store_true", help="Skip Telegram worker")
    args = parser.parse_args()
    try:
        host_id, services = service_plan(args, os.environ)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))
    print(f"Using collector host {host_id}; central={args.db}; collector={args.collector_db}", flush=True)
    try:
        supervise(services, args.port, Path.cwd())
    except KeyboardInterrupt:
        print("Stopping dashboard services...", flush=True)
    except (OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
