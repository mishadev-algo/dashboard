# Telegram health alerts

The optional alert worker reads the central database and sends health transitions through the [Telegram Bot API `sendMessage`](https://core.telegram.org/bots/api#sendmessage). It covers a collector whose last heartbeat is over 750 seconds (12.5 minutes) old, an expected terminal folder reported missing, an expected terminal whose Windows process probe explicitly reports `stopped`, and a fresh account probe that explicitly reports broker disconnection. Heartbeats are sent every five minutes by default; the extra timeout allows one missed cycle and some jitter. `unknown` or stale process/broker state and quiet logs do not trigger a stopped or broker alert. Repeated log errors are not classified yet.

Create a bot with BotFather and obtain the destination chat ID. In a **third** PowerShell window on the central server, from `C:\dashboard`, set these only in that window or in the server's secret store:

```powershell
$env:DASHBOARD_TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN'
$env:DASHBOARD_TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'
py -3.13 -m server.alerts --db central.db
```

Use the same `central.db` as the ingest server. Run one alert worker for that database. The worker polls every 10 seconds and stores alert state in the `alerts` table. The dashboard shows active, recovering, and resolved alerts. Each new failure sends one message; an unchanged failure can send a reminder after 30 minutes. A recovery sends one message. Failed sends are retried no sooner than 60 seconds later, with the delivery error shown on the page. If Telegram accepts a message but its response is lost, an eventual retry can duplicate it.

The worker sends no message until both environment variables are set and it is started. To test without a real outage, the local automated tests simulate failure, cooldown, send failure, and recovery with a fake sender:

```powershell
py -3.13 -m unittest tests.test_alerts -v
```

When a terminal is moved out of `expected` in `inventory.json`, the next fresh heartbeat ends monitoring for that target. Existing terminal-stop, missing-folder, and broker-disconnect alerts for it become resolved without a Telegram recovery message. Restart `MT5Dashboard` after editing the local inventory so the running collector and account worker reload it.

The bot token is never included in an alert message or an application error string. Do not put it in the repository or command line. The central server and collector continue collecting logs if the alert worker is stopped.

## Server availability from another host

The database alert worker cannot report an outage of its own machine: `server.run_all` stops it when another child exits, and a VPS outage stops every local process. Run the independent monitor as a **separate process**, outside `server.run_all`. On the current VPS it can check loopback and report a server-process failure while Windows remains online. For full VPS outage detection, run it on a **different** always-on host with its own scheduled task or service. The check reaches the Python server and checks database access. The Caddy template routes public HTTPS `/health` without browser authentication. A healthy response is exactly HTTP 200 with `{"status":"ok"}`; a Caddy Basic Auth 401 is not considered healthy.

Set the Telegram variables on the monitoring host, then run one monitor instance with a persistent state path:

```powershell
$env:DASHBOARD_TELEGRAM_BOT_TOKEN = 'YOUR_BOT_TOKEN'
$env:DASHBOARD_TELEGRAM_CHAT_ID = 'YOUR_CHAT_ID'
py -3.13 -m server.uptime_monitor --url https://dashboard.example.com --state monitor-state.json
```

For a separate monitor process on the current VPS, use `--url http://127.0.0.1:8765 --state .dashboard-monitor-state.json`. A separate [monitor_task.ps1](../scripts/monitor_task.ps1) task can launch it at Administrator logon using the existing encrypted `.dashboard-start.local.json` and the saved Python path. It registers `pythonw.exe` directly so Task Scheduler tracks and stops the one monitor process. Install and start it after deploying the new source, under the same Windows user that saved those settings:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\monitor_task.ps1 -Mode Install -ProjectDir C:\dashboard
Start-ScheduledTask -TaskName MT5DashboardMonitor
Get-ScheduledTask -TaskName MT5DashboardMonitor | Select-Object TaskName,State
Get-Content .\run-logs\monitor.log -Tail 20
```

This task is separate from `MT5Dashboard`; the launcher cannot stop both at once. A loopback monitor cannot report a power, Windows, or network outage of the VPS. Do not point it at a non-local plain HTTP address. The task was installed on the current VPS and survived a controlled `MT5Dashboard` stop/restart, sending the outage message while the server was down and recovery after restart. Its direct-process task action was then checked to stop without leaving an orphan.

The first two failed checks, 15 seconds apart by default, trigger a failure message even if the server was already down when the monitor started. One successful check triggers recovery. The state file prevents repeated messages after monitor restart; keep it on the monitoring host and run only one copy. Delivery failures are retried on later checks. A same-host controlled outage was verified on October 1. A second-host monitor has not been deployed and is still required for whole-VPS loss detection.

## Stopped terminals and closed deals

A stopped-terminal alert needs an expected inventory entry and a fresh collector heartbeat. It uses an explicit `stopped` process result; if that process result is `unknown`, a fresh account-worker `stopped` result can also establish the outage. An unknown result never proves recovery. On the VPS, inspect the latest collector line, alert-worker log, and `/` page together when a terminal is stopped; these show whether the folder was expected, the process was classified, and Telegram delivery failed.

After the first complete snapshot of each account, newly observed MT5 exit deals produce a Telegram message with account, symbol, strategy, position and deal tickets, volume, close time, and PnL on that deal. The first snapshot establishes a baseline so old history is not sent in a burst. Each exit deal is queued by broker server, account login, and deal ticket; partial closes can produce more than one message for a position. The account page lists up to 100 exit deals for the selected day. A separate commission deal may change the day's total PnL after the close message, so the message identifies its value as deal PnL.
