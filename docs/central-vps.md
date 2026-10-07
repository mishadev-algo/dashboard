# Separate central VPS: deployment plan

**Status (2026-10-06): preparation resumed at the operator's request.** Linux deployment files and the [launch runbook](linux-vps.md) are being prepared locally. No central VPS, domain, PostgreSQL import, or cutover has been deployed.

The proposed layout is one central VPS for the ingest API, dashboard, alerts, and database, plus a log collector and account worker on each Windows MT5 VPS. Keep the existing `MT5Dashboard` task running on the current VPS until a separate cutover is ready. Its collector and local queue must be preserved when that task is eventually split.

```text
MT5 VPS A/B/C/D -- HTTPS + individual bearer tokens --> Caddy on central VPS
                                                     --> Python ingest/dashboard on 127.0.0.1:8765
                                                     --> central database and alert worker
```

## Rollout for 3–10 additional Windows VPS

The operator plans 3–10 additional Windows MT5 VPSs; the central location is not selected yet. Use one stable HTTPS hostname as the worker endpoint so a later central-host move changes DNS rather than every host's configuration. A separate Linux VPS is the preferred production home for Caddy, PostgreSQL, the Python API/dashboard, and the alert worker. The current Windows VPS remains a data source. Only outbound HTTPS from MT5 hosts is required; do not expose their MT5 installations, collector databases, PostgreSQL, or Python port 8765 to the internet.

On each Windows host, keep a stable host ID and its own `collector-central.db`, `inventory.json`, and `accounts.json`. The log collector scans Journal/Experts and reads the EA probe report, persists log offsets and unsent events locally, then sends event batches and a status heartbeat to `POST /v1/ingest`. The account worker independently sends current account, position, and recent deal snapshots to `POST /v1/snapshot`. Assign a different token to each host. The central API authenticates the host, validates and stores the payload, and acknowledges accepted events; the dashboard and Telegram worker read central storage. The browser never polls MT5 hosts directly. Show collection/receipt time and stale status, since these are periodic observations rather than a live MT5 connection.

The collector's event queue survives a network outage and replays after acknowledgement; retries are idempotent. Its local log scan remains frequent, but queued logs upload with the five-minute heartbeat, so a fresh log line can take up to five minutes to reach the site. Account snapshots currently retry on the next five-minute worker cycle but have **no durable local upload queue**. Recent deal history is requested again by the worker, while an equity sample or a short-lived position state could be missed during an outage. Add a durable snapshot outbox or explicitly accept that gap before relying on continuous account/equity history. Keep one active central destination per host during cutover to avoid duplicate alerts and divergent state.

For the first production rollout, rehearse with a disposable PostgreSQL database and test data without production Telegram sends. Validate the Caddy configuration on the actual binary, authenticated ingest/snapshot, page access, import counts, strict audit, retry replay, and a restore from backup. Then cut over the current host with its existing collector DB, observe at least 24 hours of queue, freshness, disk, and alert behavior, and add the other VPSs one at a time. Do not delete or recreate a host's collector DB when changing the URL: it contains pending events and file offsets. The current all-in-one scheduled task needs a reviewed split-task replacement before that cutover.

For this host count, begin with one central instance and PostgreSQL; measure real event volume and query latency before adding more services. Define retention for historical heartbeats and logs, daily database backups outside the live disk, and a tested restore. Keep current account/position state and deal history according to the product's reporting needs. Run the independent `/health` monitor on a different VPS, because an alert worker on the central VPS cannot report its own host outage. The 2 vCPU/4 GB/80 GB recommendation below is a starting point to check against the 24-hour measurements.

### Publishing a release to the live domain

The Python service renders the site; there is no separate frontend build or static-site push. Publish a reviewed Git commit or tagged release, record exact Python and dependency versions, install that same set on staging, run tests and the PostgreSQL migration/import rehearsal, then deploy the same revision to the central VPS. Restart the supervised central Python service and verify `/health`, an authenticated browser page, ingestion, snapshots, and the audit. Validate and reload Caddy only when its configuration changes; the domain keeps pointing to Caddy. Roll out matching host-worker code to one Windows VPS first, verify its backlog reaches zero and account/EA data is fresh, then update the remaining hosts. Keep the previous revision and a verified database backup for rollback. Avoid an unattended `git pull` from a moving branch on the live service.

Before the first live publish, validate the prepared Linux service unit and Windows split-task installer on the target hosts, then perform the real PostgreSQL integration trial. The [Linux launch runbook](linux-vps.md) gives the schema migration order. The Caddyfile remains a template, and neither service is installed on production hosts yet.

## Host, domain, and disk sizing

The default alpha deployment is one Linux VPS in a region close to the MT5 hosts, with 2 vCPU, 4 GB RAM, and at least 80 GB SSD for PostgreSQL, Python, and Caddy. Use a subdomain of an existing domain, such as `dashboard.example.com`, or register a domain and create an `A` record to the VPS public IPv4 address. Caddy handles HTTPS on ports 80/443; PostgreSQL and Python stay on loopback. Protect the browser pages with Caddy authentication; collector POST routes use their per-host bearer tokens. A later identity proxy can be added if browser sign-in needs individual users or stronger access controls.

Observed on the current single-host trial before the October 2 cadence changes: `central.db` was about 366 MB after less than three days, with 101,864 stored heartbeats averaging about 2.1 KB of JSON each. A full day contained about 36,500 heartbeats at the original two-second cadence. The new default sends one heartbeat every five minutes while retaining two-second local log scans. That targets 288 heartbeat rows per host per day, or 1,152 for four hosts; the actual rate and storage growth still need a live 24-hour measurement. Set a retention policy before the remote alpha cutover; 80 GB is a starting size, not a guarantee for unlimited history. Keep backups outside the live database disk when possible.

If only a private operator view is required, a VPN or identity-aware tunnel can restrict browser access further. A public domain with Caddy is the simplest fit for the current collector, which already sends HTTPS requests with bearer tokens. A tunnel or external access gateway must explicitly allow the two collector API routes or give each worker a compatible machine credential.

## Endpoint choice

Use a stable domain name pointing at the central VPS, with Caddy serving HTTPS on ports 80 and 443. The Python API must stay on loopback; keep its port 8765 closed to the internet. Caddy's [automatic HTTPS guide](https://caddyserver.com/docs/quick-starts/https) covers the DNS and port requirements. The [Caddyfile template](Caddyfile.example) sends the two collector POST routes to the API with bearer authentication there, and protects all other routes with Caddy Basic authentication. It strips browser Basic credentials before forwarding page requests to Python.

An ngrok HTTPS endpoint can be useful for a short demonstration. With five-minute heartbeats, each collector makes about 8,640 scheduled ingest requests in a 30-day month; each configured account target adds about 8,640 snapshot requests. Four hosts and 15 account targets therefore make about 164,160 scheduled requests per month, before extra log batches and browser traffic. Check the current [ngrok request allowance](https://ngrok.com/pricing) against this count if considering it. A stable HTTPS endpoint and sufficient request allowance are required. Do not point workers at an endpoint whose address can change unexpectedly.

## Prepare without touching the current service

1. Provision the central VPS and a domain. Restrict network access to ports 80 and 443; allow outgoing traffic for Telegram and operating-system updates. Install the same project revision and a supported Python version. Do not copy `.dashboard-start.local.json` from the MT5 VPS: its secrets are encrypted for that Windows user.
2. Create a unique long random token for each MT5 VPS. Store the JSON host-to-token map as `DASHBOARD_HOST_TOKENS` only on the central VPS. Set the matching `DASHBOARD_COLLECTOR_TOKEN` on each host's collector and account worker. Host IDs must match the values already stored in each collector database. Do not reuse a token between hosts.
3. Configure Caddy from `docs/Caddyfile.example`; generate the password hash interactively with `caddy hash-password`, replace the example domain and hash, and run `caddy validate --config Caddyfile`. The template has not yet been validated against an installed Caddy binary.
4. Create a fresh central PostgreSQL database and migrate a **verified backup**, not the live `central.db`, using the procedure below. The Python runtime has an optional PostgreSQL mode. Rehearse ingest, snapshot, pages, alerts, audit, and backup rollback against a disposable PostgreSQL instance before a production cutover; that integration trial has not run yet.
5. Once the new central service is ready, change each MT5 host's collector and account worker to its stable `https://` origin. Use the split launchers below and install appropriate operating-system supervision. The existing `server.run_all` task is an all-in-one local launcher and cannot be reused unchanged for a remote central VPS. Preserve each host's existing collector SQLite database so pending rows and offsets survive the switch.
6. From outside the central VPS, verify unauthenticated `GET /` returns 401 and an authenticated request returns the dashboard; verify unauthenticated `POST /v1/ingest` returns 401. Then check that each host uploads with its own token, reports `pending=0`, and appears once in the central audit. Confirm port 8765 is unreachable externally. See [release.md](release.md) for the full acceptance list.

## Split launchers for a later cutover

These commands supervise their own child processes, but an operating-system service or scheduled task is still needed to restart them after a reboot. On the central VPS, with `DASHBOARD_HOST_TOKENS` and the Telegram variables set in that process environment, choose one storage mode:

```text
python -m server.run_central --db central.db
```

For PostgreSQL, install the central dependency and set `DASHBOARD_POSTGRES_DSN` in the central service's private environment. The DSN must point at the imported PostgreSQL database. The launcher passes only `--postgres` to its child processes, so the DSN does not appear in their command lines:

```text
python -m pip install -r requirements-central.txt
python -m server.run_central --postgres
python -m server.audit --postgres --strict
```

Create tables with `schema.sql` and import the verified backup before starting the PostgreSQL runtime. The API listens only on loopback in either storage mode. The host workers never need the PostgreSQL DSN.

On each Windows MT5 VPS, with its own `DASHBOARD_COLLECTOR_TOKEN` set, use its **existing** collector DB, real inventory, and account config. Repeat any `--root` or `--terminal` options already needed for discovery:

```powershell
py -3.13 -m collector.run_host --collector-db collector-central.db --inventory inventory.json --accounts accounts.json --server-url https://dashboard.example.com
```

The central launcher does not start MT5 workers; the host launcher does not open the central database. The existing `MT5Dashboard` task has not been switched to either launcher. Test the split under supervision and confirm queue replay before removing the old all-in-one task.

## Prepare a PostgreSQL import from a SQLite backup

The exporter reads one SQLite snapshot and refuses to overwrite an existing output directory. It validates the expected table columns, writes one PostgreSQL text `COPY` file per table, records row counts and SHA-256 hashes, and writes `schema.sql` and `load.psql`. The load script checks imported row counts inside its transaction. It does not change either the backup or the running database.

```powershell
$python = (Get-Content .dashboard-start.local.json -Raw | ConvertFrom-Json).python
& $python -m server.backup --db central.db --directory backups
$backup = Get-ChildItem .\backups\central-backup-*.sqlite3 | Sort-Object LastWriteTime -Descending | Select-Object -First 1
& $python -m server.restore_check --backup $backup.FullName
& $python -m server.export_postgres --backup $backup.FullName --output postgres-export
Get-Content .\postgres-export\manifest.json
```

The saved settings supply the actual Python 3.13 executable used by the current scheduled task; `py -3.13` is not available through the Python launcher on this VPS. Choose a new output directory if `postgres-export` already exists. These commands create an export from a backup and do not change the live databases.

Keep the export directory private: raw logs and account data are present. Import only into a **new, empty** PostgreSQL database. Run `psql` from inside the export directory so `load.psql` finds the relative `.copy` files:

```powershell
Set-Location .\postgres-export
psql -X -v ON_ERROR_STOP=1 -d YOUR_EMPTY_DATABASE -f schema.sql
psql -X -v ON_ERROR_STOP=1 -d YOUR_EMPTY_DATABASE -f load.psql
```

Check each imported table count against `manifest.json`; retain the verified SQLite backup for rollback. Start the PostgreSQL runtime against the disposable import and check `/health`, authenticated ingest and snapshots, account and log pages, close-alert retry, and `server.audit --postgres`. Live application writes after the backup are not in this export, so take a final backup during the eventual monitored cutover. PostgreSQL import and application read/write behavior have not been integration-tested on this VPS.
