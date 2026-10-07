"""One-time local MT5 history backfill for the all-in-one Windows dashboard."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from collector.accounts import load_targets, probe_target
from collector.process import list_terminal_processes
from shared.paths import same_windows_path
from shared.timezones import day_zone

from .ingest import open_database
from .snapshots import store_snapshot


def backfill(db_path: Path, accounts_path: Path, since: date, dry_run: bool = False) -> list[dict]:
    """Read account history from live MT5, then baseline any new historical closes."""
    if since < date(1970, 1, 1) or since > datetime.now(timezone.utc).date():
        raise ValueError("since must be between 1970-01-01 and today")
    targets = load_targets(accounts_path)
    processes = list_terminal_processes()
    report = []
    with closing(open_database(db_path)) as connection:
        for index, target in enumerate(targets, 1):
            registered = connection.execute(
                "SELECT a.host_id,a.terminal_id,t.data_path FROM accounts a "
                "JOIN terminals t ON t.host_id=a.host_id AND t.terminal_id=a.terminal_id "
                "WHERE a.server=? AND a.login=?", (target.server, target.login),
            ).fetchone()
            if registered is None or not same_windows_path(registered[2], str(target.data_path)):
                raise ValueError(f"account {index} is not registered at its configured terminal")
            host_id, terminal_id, _ = registered
            local_today = datetime.now(timezone.utc).astimezone(day_zone(target.day_timezone)).date()
            history_days = (local_today - since).days + 1
            if history_days < 1:
                raise ValueError("since is after the current account day")
            payload = probe_target(replace(target, history_days=history_days), host_id, processes,
                                   timeout=120)
            if (payload["terminal_id"] != terminal_id or
                    payload["status"].get("data_complete") is not True):
                reason = payload["status"].get("reason", payload["status"].get("state", "unknown"))
                raise RuntimeError(f"account {index} history probe failed: {reason}")
            old_count, old_earliest = connection.execute(
                "SELECT COUNT(*),MIN(day_local) FROM deals WHERE server=? AND login=?",
                (target.server, target.login),
            ).fetchone()
            deals = payload["deals"]
            item = {
                "account_index": index,
                "requested_since": since.isoformat(),
                "mt5_earliest": min((deal["day_local"] for deal in deals), default=None),
                "mt5_deals": len(deals),
                "previous_earliest": old_earliest,
            }
            if dry_run:
                item["new_deals"] = None
            else:
                store_snapshot(connection, payload, host_id, historical_backfill=True)
                new_count, new_earliest = connection.execute(
                    "SELECT COUNT(*),MIN(day_local) FROM deals WHERE server=? AND login=?",
                    (target.server, target.login),
                ).fetchone()
                item["new_deals"] = new_count - old_count
                item["stored_earliest"] = new_earliest
            report.append(item)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill MT5 account history into the local central DB")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2000, 1, 1))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        result = backfill(args.db, args.accounts, args.since, args.dry_run)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
