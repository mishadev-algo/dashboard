# Start the dashboard on one Windows VPS

These commands assume the Git checkout is in `C:\dashboard`, the ingest server and MT5 terminals run on the same VPS, and Python 3.13 is installed. Keep the current `central.db`, collector database, `inventory.json`, and secrets. The one-console launcher starts separate Python processes and shows their output together in one PowerShell window.

## 1. Check the existing files

Open PowerShell under the Windows user that runs MT5:

```powershell
Set-Location C:\dashboard
py -3.13 --version
Get-ChildItem *.db
Test-Path .\inventory.json
Test-Path .\accounts.json
```

The database list tells you whether your **existing** collector database is `collector.db` or `collector-central.db`. Use that exact name below. Do not create a new collector DB just to use the launcher; its existing cursors and pending upload queue should be preserved. `inventory.json` should return `True`.

`accounts.json` is **not created or filled automatically**. The log collector discovers terminal folders, but it cannot establish which account should be trusted in each terminal. The account worker needs the exact expected login and broker server to reject a wrong account or a terminal that switched accounts. The file is ignored by Git and must be created once on the VPS. If it is missing, copy the example and edit every placeholder before starting the account worker:

```powershell
Copy-Item .\docs\accounts.example.json .\accounts.json
notepad .\accounts.json
```

Copy `data_path` from each active entry in `inventory.json`; read `login` and `server` from the corresponding MT5 terminal. `day_timezone` defaults to `UTC` if omitted. Strategy names are optional; unknown magic numbers remain unmapped. See [the account setup](accounts.md) and [example](accounts.example.json). Do not use the example's placeholder account numbers.

Install the package needed by the optional account worker:

```powershell
py -3.13 -m pip install MetaTrader5 tzdata
```

## 2. Back up and pull the updated source

The online backup can run while the current server is active:

```powershell
py -3.13 -m server.backup --db central.db --directory backups
```

Stop the old **server, collector, Telegram worker, and account worker** windows with Ctrl+C. Run one copy of each service only. Pull the committed source in the existing checkout:

```powershell
git status --short
git pull --ff-only origin main
```

If `git status --short` shows local edits on the VPS, preserve them before pulling and resolve the conflict there. The database files, `inventory.json`, `accounts.json`, and backups are ignored by Git and stay on the VPS.

Check the updated source before starting services:

```powershell
py -3.13 -m unittest discover -s tests -q
```

## 3. Set the existing tokens in this PowerShell window

Use the same host ID and collector token that already worked. The host ID is reused from the existing collector DB. If you need to see it, replace the DB filename in this command with yours:

```powershell
py -3.13 -c "import sqlite3; c=sqlite3.connect('collector-central.db'); print(*c.execute('SELECT host_id FROM terminals UNION SELECT host_id FROM log_events'), sep='\n'); c.close()"
```

Set the server's host-to-token map, matching collector token, and existing Telegram destination. Keep these values out of source files:

```powershell
$env:DASHBOARD_HOST_TOKENS = '{"YOUR_EXISTING_HOST_ID":"YOUR_EXISTING_COLLECTOR_TOKEN"}'
$env:DASHBOARD_COLLECTOR_TOKEN = 'YOUR_EXISTING_COLLECTOR_TOKEN'
$env:DASHBOARD_TELEGRAM_BOT_TOKEN = 'YOUR_EXISTING_BOT_TOKEN'
$env:DASHBOARD_TELEGRAM_CHAT_ID = 'YOUR_EXISTING_CHAT_ID'
```

If the central server accepts several VPS hosts, keep **all** of their host/token pairs in `DASHBOARD_HOST_TOKENS`. The entry for this VPS must match `DASHBOARD_COLLECTOR_TOKEN`.

## 4. Start everything in one window

Replace `collector-central.db` with your existing collector DB name if different:

```powershell
py -3.13 -m server.run_all --db central.db --collector-db collector-central.db --inventory inventory.json --accounts accounts.json
```

The launcher starts the ingest server, log collector, Telegram alert worker, and account worker as **four separate processes in this one PowerShell window**. It checks the files, host ID, and tokens before starting, prefixes each process's output, and stops the group when you press Ctrl+C. If a process exits unexpectedly, it stops the others so you can see and fix the error. Leave this window open while the dashboard runs.

If `accounts.json` is not ready, omit `--accounts`; broker and PnL readings will wait while the server, collector, and alerts run. If the Telegram destination is not set in this window, add `--no-alerts` to skip that worker. If your previous collector command used extra roots or portable terminals, add each `--root "C:\path"` or `--terminal "C:\path"` to the launcher command.

## 5. Check the result

Within a minute, the combined output should show `started server`, `started collector`, and the workers you enabled. The collector should report your expected/archived folders and eventually `pending=0`. Open these pages on the VPS:

- `http://127.0.0.1:8765/` — terminal and collector status
- `http://127.0.0.1:8765/logs` — Journal and Experts lines
- `http://127.0.0.1:8765/accounts` — positions and realized PnL, once the account worker has made a complete snapshot

The one-console launcher is for an attended alpha trial. Closing that PowerShell window stops the services. The [release runbook](release.md) covers the one-off audit, backup, and later HTTPS setup; a Windows service or scheduled task is still needed for unattended startup after a reboot.

To create an audit after the workers have reported, use a temporary second PowerShell window:

```powershell
Set-Location C:\dashboard
py -3.13 -m server.audit --db central.db --output alpha-audit.json
```
