# VPS handoff — 2026-10-01

Read this file first in a new chat on the VPS, then use [PROJECT_STATUS.md](PROJECT_STATUS.md) for the full evidence history and [docs/release.md](docs/release.md) for the release procedures. Continue from the **Next check** below; the current task has already been started manually.

## Current VPS state

- Project directory: `C:\dashboard`. Windows user: `VPS72565826\Administrator`. Collector host ID: `vps72565826`.
- `MT5Dashboard` is registered in Task Scheduler and was reported **Running** after `Start-ScheduledTask`. It launches `server.run_all`, which supervises the server, log collector, Telegram alert worker, and MT5 account worker. The server listens on `127.0.0.1:8765`; its pages are `/` and `/accounts`.
- The latest task log supplied by the operator showed HTTP 200 for `/v1/ingest` and `/v1/snapshot`; collector scans had `discovered=4`, `expected=3`, `archived=1`, `pending=0`, `missing=()`, and `unknown=()`. Three account probes uploaded snapshots. The alert worker reported `open alerts=0`.
- The task uses `central.db`, `collector-central.db`, `inventory.json`, and `accounts.json`. The last two files and `.dashboard-start.local.json` are local and ignored by Git. The startup settings contain secrets encrypted for this Windows user; do not copy that file to another VPS or put tokens in chat or Git.
- The operator's `py -0p` listed Python 3.13 at `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe`. The operator was given the explicit `-PythonExe` command after automatic path detection failed; the later Running task and live logs show a usable Python executable was saved.

## Evidence already collected

- A 2026-10-01 07:10 UTC audit showed a live collector, three expected running terminals with Journal and Experts events, one stopped archived terminal, no missing or unknown folders, and zero pending uploads. It showed fresh complete snapshots for account logins `8035865` and `8084537`. At that time a third terminal reported `account_mismatch`; the operator corrected it and later supplied `"findings": []` from a fresh audit. Only that findings excerpt was supplied for the later audit.
- After comparing the two accounts' open positions and one day's realized PnL against MT5 History, the operator reported that everything was good. The day, ticket list, and totals were not supplied, so this is an operator-reported comparison.
- The VPS backup `backups\central-backup-20261001T075008Z-5a073d5b.sqlite3` passed `server.restore_check`: integrity `ok`, audit findings `[]`, with 1 host, 4 terminals, 1,844 log events, 3 account rows, 3 position rows, and 75 deal rows. This was a restore to a temporary database, not a replacement of the live database.
- An API outage/replay check and Telegram failure/recovery check were previously reported complete by the operator, but full before/after artifacts were not supplied. The real MT5 midnight rollover test was deliberately deferred.

## Next check: restart after reboot

The scheduled task has started the services **manually**. It has **not** yet been shown to start them after a real VPS reboot and user logon. The task trigger is interactive logon for `Administrator`; it does not start before that user signs in. The task does not start MT5 terminals, so use the normal MT5 startup procedure after logon if needed.

The current log has spaces between characters because an older script mixed UTF-8 and UTF-16LE. A fix is on `origin/main`; the next task run after pulling will create a readable log. There is no reason to stop the working task just for this log fix.

When ready for the reboot check, in PowerShell on this VPS:

```powershell
Set-Location C:\dashboard
git status --short
git pull --ff-only origin main
```

After the VPS reboots and `Administrator` signs in, wait for the dashboard and MT5 terminals to settle, then run:

```powershell
Set-Location C:\dashboard
Get-ScheduledTask -TaskName MT5Dashboard | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName MT5Dashboard | Select-Object LastRunTime,LastTaskResult
$log = Get-ChildItem .\run-logs\dashboard-*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log.FullName -Tail 80
py -3.13 -m server.audit --db central.db --output alpha-audit-after-reboot.json
Get-Content .\alpha-audit-after-reboot.json
```

Acceptance: the task is Running; the **new** log shows `started server`, `started collector`, `started alerts`, and `started accounts`; collector uploads/heartbeats resume with `pending=0`; account snapshots become fresh; and the audit has no findings once MT5 is running. If the task or audit fails, inspect the task result and latest log before starting a manual launcher; two launchers must not compete for port `8765`.

After this, the next open live checks are broker-disconnect/Telegram recovery evidence and retaining API outage/replay counts. Secure remote deployment still needs a host/domain decision, HTTPS and authenticated page access, per-host tokens, and the planned PostgreSQL migration. Midnight rollover remains deferred at the user's request.
