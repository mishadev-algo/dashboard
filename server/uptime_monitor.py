"""Independent HTTP and Telegram monitor for a deployed dashboard server."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .alerts import TelegramSender
from .windows_secrets import load_telegram_settings


def check(url: str) -> bool:
    try:
        with urlopen(Request(url, headers={"Cache-Control": "no-cache"}), timeout=8) as response:
            return response.status == 200 and json.load(response) == {"status": "ok"}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"healthy": None, "failures": 0}
    if not isinstance(state, dict) or type(state.get("failures")) is not int or state.get("healthy") not in (None, True, False):
        return {"healthy": None, "failures": 0}
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def _single_instance_handle():
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, 1, "Local\\MT5DashboardMonitor")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        raise RuntimeError("another dashboard uptime monitor is already running")
    return handle


def step(url: str, path: Path, send, *, healthy: bool | None = None, failures_required: int = 2) -> dict:
    """Check once; persist a transition only after Telegram accepts it."""
    state = load_state(path)
    healthy = check(url) if healthy is None else healthy
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if healthy:
        state["failures"] = 0
        if state["healthy"] is False:
            try:
                send(f"Recovered: dashboard server is reachable at {url} (checked {checked_at})")
            except Exception:
                save_state(path, state)
                raise
        state["healthy"] = True
    else:
        state["failures"] += 1
        if state["healthy"] is not False and state["failures"] >= failures_required:
            try:
                send(f"Dashboard server is unavailable at {url} (checked {checked_at})")
            except Exception:
                save_state(path, state)
                raise
            state["healthy"] = False
    save_state(path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor dashboard availability from another host")
    parser.add_argument("--url", required=True, help="Public HTTPS origin or local http://127.0.0.1:PORT")
    parser.add_argument("--state", type=Path, required=True, help="Persistent state file on the monitoring host")
    parser.add_argument("--saved-settings", type=Path, help="Existing Windows DPAPI startup settings")
    parser.add_argument("--log", type=Path, help="Append status lines to this log file")
    parser.add_argument("--interval", type=float, default=15)
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    local_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if not (parsed.scheme == "https" or local_http) or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment or parsed.username or parsed.password:
        parser.error("--url must be an HTTPS origin or loopback HTTP origin")
    if args.interval < 10:
        parser.error("--interval must be at least 10 seconds")
    try:
        if args.saved_settings:
            token, chat_id = load_telegram_settings(args.saved_settings)
        else:
            token = os.environ["DASHBOARD_TELEGRAM_BOT_TOKEN"]
            chat_id = os.environ["DASHBOARD_TELEGRAM_CHAT_ID"]
        sender = TelegramSender(token, chat_id)
    except (KeyError, ValueError, OSError, RuntimeError):
        parser.error("set Telegram environment variables or valid saved Windows settings")
    url = args.url.rstrip("/") + "/health"
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)

    def report(message: str) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        if args.log:
            with args.log.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
        elif sys.stdout:
            print(line, flush=True)

    try:
        handle = _single_instance_handle()
    except (OSError, RuntimeError) as exc:
        report(f"monitor startup error: {exc}")
        raise SystemExit(1) from None
    try:
        report(f"monitor started; url={url}")
        last_report = 0.0
        while True:
            previous = load_state(args.state).get("healthy")
            try:
                state = step(url, args.state, sender)
                now = time.monotonic()
                if state["healthy"] != previous or now - last_report >= 60:
                    report(f"health={state['healthy']} failures={state['failures']}")
                    last_report = now
            except (OSError, RuntimeError) as exc:
                report(f"monitor error: {exc}")
            time.sleep(args.interval)
    finally:
        if handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    main()
