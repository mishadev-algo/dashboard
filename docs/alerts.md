# Telegram health alerts

The optional alert worker reads the central SQLite database and sends health transitions through the [Telegram Bot API `sendMessage`](https://core.telegram.org/bots/api#sendmessage). It covers a collector whose last heartbeat is over 30 seconds old, an expected terminal folder reported missing, and an expected terminal whose Windows process probe explicitly reports `stopped`. `unknown` process state and quiet logs do not trigger a stopped alert. Broker connection and repeated log errors are not classified yet.

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

The bot token is never included in an alert message or an application error string. Do not put it in the repository or command line. The central server and collector continue collecting logs if the alert worker is stopped.
