# Central dashboard deployment — 2026-10-07

## Current layout

- Public address: `https://46.225.132.195/`. Browser login uses Basic Auth user `operator`; the password is supplied separately.
- Ubuntu VPS: `mt5-dashboard.service` runs the central Python API and alert worker as `dashboard`; PostgreSQL database and role are named `dashboard`. Source lives in `/opt/mt5-dashboard`, private settings in `/etc/mt5-dashboard/central.env` (mode 0600).
- Caddy serves the Let's Encrypt IP certificate and routes `/health`, `/v1/ingest`, and `/v1/snapshot` to the API. Browser pages require Basic Auth. Certificate renewal uses `snap.certbot.renew.timer` and `/etc/letsencrypt/renewal-hooks/deploy/mt5-dashboard-caddy.sh` to copy the renewed certificate and reload Caddy. The first certificate expires on 2026-10-13; `certbot renew --dry-run` passed on 2026-10-07.
- Windows MT5 host: `MT5CollectorRemote` sends data to the HTTPS address and `MT5DashboardMonitor` checks its `/health` from outside the VPS. `MT5Dashboard` is disabled but retained for rollback. Both running tasks start at interactive Administrator logon; a reboot without that logon will leave the MT5 collector and monitor stopped.

## Verified at cutover

- Public `/health`: HTTP 200 with valid TLS; browser page: HTTP 401 without credentials and HTTP 200 with credentials.
- Migrated SQLite export included all 13 tables, including backtest data. The collector sent 246 new log events on its first run, bringing `log_events` from 2,633 to 2,879. Heartbeats and three complete account snapshots also arrived.
- `server.audit --postgres --strict` exited successfully on 2026-10-07 with no findings: collector online, three expected terminals and account snapshots fresh, one archived terminal, `pending_at_scan=0`.
- The initial Windows collector exited when a connection reset error contained Unicode that the task's output encoding could not write. `remote_host_task.ps1` now sets UTF-8 output; the restarted task remained running and all three account probes uploaded. An Ubuntu package update briefly restarted PostgreSQL during cutover; the API recovered through systemd and then remained active.
- A daily PostgreSQL dump timer runs at approximately 02:20 UTC. The first 3.9 MB custom-format dump restored successfully into a temporary database with 2,879 events. A matching checksum copy is in `C:\dashboard\backups\dashboard-postgres-20261007T070217Z.dump`; future automatic dumps remain on the VPS, so periodic off-VPS transfer is still needed.

## Operations

On the Ubuntu VPS:

```sh
systemctl status mt5-dashboard caddy snap.certbot.renew.timer mt5-dashboard-backup.timer
journalctl -u mt5-dashboard -n 80 --no-pager
journalctl -u mt5-dashboard-backup.service -n 20 --no-pager
ls -lh /var/backups/mt5-dashboard
```

Run the strict audit as the service user from the project directory:

```sh
sudo -u dashboard bash -c 'cd /opt/mt5-dashboard; set -a; source /etc/mt5-dashboard/central.env; exec .venv/bin/python -m server.audit --postgres --strict'
```

Run and verify an extra backup:

```sh
sudo systemctl start mt5-dashboard-backup.service
sudo journalctl -u mt5-dashboard-backup.service -n 20 --no-pager
```

The backup files contain trading and account data. Copy them to protected storage outside the VPS. There is no automatic off-VPS destination or retention policy yet; monitor disk use and arrange both before long-term operation. To restore, create a separate empty database and use `pg_restore --exit-on-error --no-owner -d DATABASE FILE`; verify data there before changing the live database.

On the Windows MT5 host, use Task Scheduler or `Get-ScheduledTask` to check `MT5CollectorRemote` and `MT5DashboardMonitor`. The collector writes `run-logs\remote-collector-*.log`; the monitor writes `run-logs\monitor.log`. Keep the interactive Administrator session signed in after reboot. The SSH deployment key is in the ignored `.deploy` directory on that host.
