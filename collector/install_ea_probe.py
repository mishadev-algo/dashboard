"""Copy and compile the EA inventory service without touching charts or trading flags."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .inventory import load_inventory
from .process import install_path


def install_service(data_path: Path, source: Path) -> str:
    folder = install_path(data_path)
    if not folder:
        raise ValueError(f"Cannot locate MetaEditor for {data_path}")
    editor = Path(folder) / "metaeditor64.exe"
    if not editor.is_file():
        raise ValueError(f"MetaEditor is missing: {editor}")
    target = data_path / "MQL5" / "Services" / source.name
    binary = target.with_suffix(".ex5")
    content = source.read_bytes()
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError(f"Existing service source differs; review before replacing: {target}")
        if binary.is_file() and binary.stat().st_mtime >= target.stat().st_mtime:
            return f"Already compiled: {target}"
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    log = target.with_suffix(".log")
    if log.exists():
        log.unlink()  # Only this service's previous compile log, never terminal logs.
    completed = subprocess.run([str(editor), f"/compile:{target}", "/log"], timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if not log.is_file():
        raise ValueError(f"No compile report for {target}; compile it in MetaEditor")
    raw = log.read_bytes()
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16-le", errors="replace")
    if completed.returncode or "0 errors, 0 warnings" not in text or not binary.is_file():
        raise ValueError(f"Compile not confirmed; inspect {log}")
    return f"Compiled: {target}; start one instance in MT5 Navigator > Services > Add Service"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path("mql5/DashboardEaProbe.mq5"))
    args = parser.parse_args()
    try:
        for path in load_inventory(args.inventory).expected:
            print(install_service(path, args.source), flush=True)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
