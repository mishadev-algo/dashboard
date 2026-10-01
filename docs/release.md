# Alpha trial and secure deployment

## Trial evidence

The read-only audit command creates a JSON snapshot from `central.db`:

```powershell
py -3.13 -m server.audit --db central.db --output alpha-audit.json
```

It reports every host's latest heartbeat, expected/discovered/missing/unknown folders, per-terminal Journal and Experts event counts, process and broker/AutoTrading states, pending upload count, and each account's latest complete snapshot. `--strict` exits with code 1 when the database checks have findings. The report lists the live checks it cannot establish from SQLite alone: a real midnight rollover, API outage replay with before/after counts, a sample comparison to MT5 logs, two-account History reconciliation, and Telegram delivery. Store the JSON alongside screenshots or operator notes for those checks. A zero-finding report by itself is not an alpha release pass.

Run the full trial across a trading day and midnight when the operator is ready. The user has deferred that live rollover check for now. Keep the collector, ingest server, and alert worker running; compare central counts before and after a controlled API outage; then rerun the audit. Resolve missing and unknown folders before release.

## Consistent SQLite backup

While the prototype still uses SQLite, create an online backup with Python's `sqlite3.Connection.backup()` API. The command checks the resulting database with `PRAGMA integrity_check` and publishes a new timestamped file only after validation:

```powershell
py -3.13 -m server.backup --db central.db --directory backups
```

The restore check uses the newest backup as input, restores it to a temporary separate database, runs `PRAGMA integrity_check` and the dashboard audit queries, prints table counts, then removes the temporary copy. It does not modify `central.db`:

```powershell
$backup = Get-ChildItem .\backups\central-backup-*.sqlite3 | Sort-Object LastWriteTime -Descending | Select-Object -First 1
py -3.13 -m server.restore_check --backup $backup.FullName
```

Keep the backup directory outside the web server's served paths and copy backups to a separate storage location. The restore check proves the file can be restored and queried; its audit findings describe the data at check time and can include stale heartbeats in an older backup. For a real recovery, stop the launcher, preserve the current `central.db`, copy a checked backup to `central.db`, then start the launcher. Never replace the live database in place.

## Restart after a VPS reboot

The one-console launcher currently needs an open PowerShell window. [vps_task.ps1](../scripts/vps_task.ps1) prepares a Windows Task Scheduler job that runs the same launcher at the MT5 Windows user's **logon**. Logon is the chosen trigger because the worker attaches to that user's running MT5 terminals. A reboot without that user logging on does not start this job. The task must be checked on the VPS before relying on it.

In the existing launcher PowerShell window, press Ctrl+C. The four environment variables remain in that window. Pull the current Git revision, then save the startup settings with the **existing** collector database filename and any previously used `-Root` or `-Terminal` paths:

```powershell
Set-Location C:\dashboard
git pull --ff-only origin main
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps_task.ps1 -Mode Save -ProjectDir C:\dashboard -CollectorDb collector-central.db
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\vps_task.ps1 -Mode Install -ProjectDir C:\dashboard
Start-ScheduledTask -TaskName MT5Dashboard
```

`Save` uses the four `DASHBOARD_*` variables already set in that window, finds the Python 3.13 executable, and writes `.dashboard-start.local.json`. The secret values in that ignored file are encrypted for the current Windows user with [DPAPI](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/convertfrom-securestring); run the scheduled task as that same user. `Install` registers an [interactive logon task](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger), sets an [unlimited execution time](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-executiontimelimit), avoids parallel copies, and allows three restarts after a failure. It does not start the task until `Start-ScheduledTask` is run. The task writes a new ignored file in `run-logs` each time it starts. To inspect it:

If `Save` fails, stop before `Install` or `Start-ScheduledTask`: an existing startup-settings file may contain older values. Run `py -3.13 -c "import sys; print(sys.executable)"` and `py -0p` on that VPS to check the Python launcher and installed versions. `Install` checks that the saved executable still exists, but `Save` must succeed with the current settings before starting the task.

If the launcher lists Python 3.13 but automatic detection fails, pass the exact executable to `Save` with `-PythonExe 'C:\path\to\Python313\python.exe'`. The helper verifies that it runs Python 3.13 before saving the settings.

```powershell
Get-ScheduledTask -TaskName MT5Dashboard | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName MT5Dashboard | Select-Object LastRunTime,LastTaskResult
$log = Get-ChildItem .\run-logs\dashboard-*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $log.FullName -Tail 50
```

Confirm `started server`, `started collector`, `started alerts`, `started accounts`, and a collector line with `pending=0`. Check `/` and `/accounts`, then verify one clean return after a VPS reboot and user logon. If the task fails, inspect its log and Task Scheduler's last result before starting a manual launcher. The task registration and DPAPI reload path were prepared locally but have not been run on the Windows VPS.

## HTTPS and page authentication

The Python server binds to loopback. For a public domain, a reverse proxy can terminate HTTPS and protect the pages. [Caddy's HTTPS guide](https://caddyserver.com/docs/quick-starts/https) documents the DNS and port 80/443 prerequisites; [its `basic_auth` directive](https://caddyserver.com/docs/caddyfile/directives/basic_auth) accepts a hashed password. [The reverse proxy directive](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) forwards requests to the loopback server.

Use [Caddyfile.example](Caddyfile.example) as a template after choosing the central host and domain. Replace its domain and password hash. Generate the hash interactively with `caddy hash-password`; do not put a plaintext password in the file or shell command history. Run `caddy validate --config Caddyfile` before starting Caddy.

After deployment, check these responses from outside the central host:

1. `GET /` without browser credentials returns `401`; the same request with the configured credentials returns the dashboard over valid HTTPS.
2. `POST /v1/ingest` without a bearer token returns `401`; a collector with its assigned token receives an acknowledgement and `pending=0` after upload.
3. `POST /v1/snapshot` with the assigned token accepts a verified account snapshot; another host's token cannot submit for this host.
4. The Python server still listens only on `127.0.0.1:8765`; port 8765 is not publicly reachable.

The Caddyfile routes only `POST /v1/ingest` and `POST /v1/snapshot` to the server without browser Basic authentication; the server still requires each host's bearer token. All other paths require Basic authentication at Caddy. Give every host a distinct token in `DASHBOARD_HOST_TOKENS`, set the matching `DASHBOARD_COLLECTOR_TOKEN` on its Windows workers, and rotate any token that has appeared in logs or shared material. Use `https://your-domain` as the workers' `--server-url`. Keep `central.db`, tokens, account configuration, and backups readable only by the service account.

This is a deployment template, not a live deployment. Hosting, domain, DNS, service supervision, and the planned PostgreSQL move remain to be chosen and verified before remote alpha release.
