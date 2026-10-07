# Central ingest prototype

The central API accepts batched MT5 log events and periodic collector heartbeats at `POST /v1/ingest`. The current VPS stores them in a separate SQLite database; an optional PostgreSQL runtime mode is prepared for the future central VPS. Each event has a deterministic ID from host ID, terminal ID, stream, file name, generation, and byte offset. The API acknowledges every accepted ID, including a retry of an already stored event. The collector marks only acknowledged local rows as delivered. A lost response therefore causes a safe retry.

The server also has a read-only status page at `/`, a raw log view at `/logs`, and an account view at `/accounts`. The status page shows collector heartbeat freshness, discovered folders, MT5 process state when the Windows probe can match an installation, last received Journal/Experts times, recent lines containing `error` or `failed`, and alert state. The process check uses each data folder's `origin.txt` installation path (or a portable executable in the data folder) and the Windows process list. If the installation path or a process path cannot be read, it reports `unknown` rather than claiming the terminal is stopped. The optional [account worker](accounts.md) adds broker/AutoTrading state, positions, and realized PnL through `/v1/snapshot`. An optional [Telegram alert worker](alerts.md) handles collector offline, expected folder missing, explicit stopped-process, and verified broker-disconnect states. Central PostgreSQL storage and queue limits remain for later work.

## Local smoke test on one machine

This section starts a **new standalone** server and collector. Do not run these commands alongside the scheduled `MT5Dashboard` task on the VPS. That task already runs both services with `collector-central.db` and its saved token; `collector.db` is an older local database. Copying the placeholder `YOUR_TOKEN` into a new window against the running server produces `401 Unauthorized`, and starting a second collector is unnecessary. For the running VPS, check the latest task log or run `server.audit` instead.

Open PowerShell in the project directory. Find the collector's default host ID and generate a token:

```powershell
py -3.13 -c "import socket; print(socket.gethostname())"
py -3.13 -c "import secrets; print(secrets.token_urlsafe(32))"
```

In the first PowerShell window, replace `YOUR_HOSTNAME` with the exact printed hostname and `YOUR_TOKEN` with the generated token. Start the server:

```powershell
$env:DASHBOARD_HOST_TOKENS = '{"YOUR_HOSTNAME":"YOUR_TOKEN"}'
py -3.13 -m server --db central.db
```

From a browser on the same machine, open `http://127.0.0.1:8765/` for status or `http://127.0.0.1:8765/logs` for filtered raw lines. The server keeps these pages on loopback; they have no login in this prototype.

In a second PowerShell window, use the same token and the existing local collector database:

```powershell
$env:DASHBOARD_COLLECTOR_TOKEN = 'YOUR_TOKEN'
py -3.13 -m collector --db collector.db --server-url http://127.0.0.1:8765 --follow
```

No `--root` is needed for the standard `%APPDATA%\MetaQuotes\Terminal` folder. If `collector.db` already contains one host ID, the collector reuses it automatically. Use that ID as the key in `DASHBOARD_HOST_TOKENS`; it may differ from the computer hostname. Do not run two collector processes against the same local database at once.

If the old database contains multiple host IDs, the collector stops with an error. This can happen after switching between an explicit `--host-id` and the default hostname. Preserve the old file and start the central trial with a fresh database instead:

```powershell
py -3.13 -m collector --db collector-central.db --server-url http://127.0.0.1:8765 --follow
```

This starts a new two-day lookback and leaves `collector.db` intact as an archive. Use `--lookback-days` if the initial central history should cover more days. The host ID in the server token map must match the current hostname (or an explicit `--host-id` in this new command).

The collector prints `events`, `uploaded`, and `pending`. The first upload includes any previously stored lines; after acknowledgement, `pending=0`. To inspect the central database in a third PowerShell window:

```powershell
py -3.13 -c "import sqlite3; c=sqlite3.connect('central.db'); print(*c.execute('SELECT host_id, stream, COUNT(*) FROM log_events GROUP BY host_id, stream ORDER BY host_id, stream'), sep='\n')"
```

For an outage test with the server and collector running in **separate consoles**, stop only the server with Ctrl+C, wait for a new complete MT5 log line, and observe `pending` increase and `upload_error` appear in the collector console. Restart the server with the same `central.db` and token. `pending` should return to zero; each event ID remains unique in central storage. Do not stop just the server child of `server.run_all` or the scheduled `MT5Dashboard` task for this test: the launcher stops the collector and other workers when any child exits. Arrange a separate-process maintenance trial if a deliberate API shutdown is still required.

## Remote setup

Run one central server and register each collector's exact host ID with a distinct token in `DASHBOARD_HOST_TOKENS`. The built-in server binds only to loopback and does not provide TLS. For a VPS collector to reach a server on another machine, put the API behind an HTTPS reverse proxy and pass its origin as `--server-url https://your-host`. Protect the status and log pages with authentication at that proxy before exposing them remotely. The collector rejects plain HTTP to non-local hosts. Keep tokens in environment variables or a secret store; do not put them in source files or command-line arguments.

The heartbeat stores the discovered terminal paths, latest file cursor for each stream, expected, archived, missing, and unknown folder lists, and local pending count. The coverage fields describe folders; process state is reported separately. Create a local `inventory.json` from [the example](inventory.example.json) to make expected coverage persistent across collector restarts.
The collector scans logs every two seconds by default and saves new lines in its local SQLite queue. Every five minutes it uploads queued lines together with one terminal-state heartbeat; a large backlog can require multiple batched POSTs. The first upload after startup runs immediately. The server treats a collector as stale after 750 seconds (12.5 minutes), allowing for one missed five-minute cycle and some jitter. Terminal stop and EA status normally arrive within five minutes plus the alert worker's polling interval. `--upload-interval` changes both queued-log and heartbeat delivery; the older `--heartbeat-interval` spelling is retained as an alias. Changing the interval also requires reviewing the central freshness timeout.
On Windows, the collector classifies MT5 executable paths when it prepares a heartbeat and sends `running`, `stopped`, or `unknown`. A stale collector heartbeat makes that process state unknown on the page.
