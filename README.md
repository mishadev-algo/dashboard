# MT5 log dashboard prototype

Python collector for MT5 Journal and Experts logs. It discovers terminal data folders, stores complete lines in local SQLite, and uploads them to a central ingest API with acknowledgements and retry. The central server provides a read-only status page and filtered raw logs. An optional worker sends Telegram health alerts.

The dashboard also has a separate [Backtest Lab](docs/backtest-lab.md) at `/backtests` for importing MT5 HTML or CSV strategy results and comparing portfolio combinations.

See [one-window VPS startup](docs/VPS_START.md), [collector usage](docs/collector.md), [central ingest setup](docs/ingest.md), [Telegram alerts](docs/alerts.md), [account worker setup](docs/accounts.md), [Expert Advisors page](docs/eas.md), and [alpha release/deployment](docs/release.md). The release runbook includes a separate backup restore check and a Windows logon task helper.

For a separate Linux central VPS, use the [Linux launch runbook](docs/linux-vps.md). It includes a source-only package, PostgreSQL import, systemd unit, and Caddy configuration.

Run the fixture checks:

```text
python -m unittest discover -s tests -v
```

The status page reports collector and folder freshness plus Windows MT5 process state where installation matching succeeds. An optional Windows worker adds broker/AutoTrading status and verified account positions and realized PnL.

The code is split by responsibility: `collector/core.py` tails local logs, `collector/remote.py` uploads acknowledged batches, `collector/accounts.py` and `collector/mt5_snapshot.py` collect account snapshots, `server/ingest.py` handles the API and storage, `server/snapshots.py` validates account data, and `server/web.py` renders the read-only pages. The `shared/` package keeps time-zone, URL, token, and Windows path validation consistent between the collector and server.
