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

Keep the backup directory outside the web server's served paths. Copy backups to a separate storage location and test a restore before release. To restore, stop the ingest and alert processes, preserve the current `central.db`, copy the chosen backup to `central.db`, check it, and restart the processes. Do not replace a live database file in place. The check is:

```powershell
py -3.13 -c "import sqlite3; c=sqlite3.connect('central.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
```

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
