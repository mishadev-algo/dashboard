# MT5 log dashboard prototype

Python collector for MT5 Journal and Experts logs. It discovers terminal data folders, stores complete lines in local SQLite, and uploads them to a central ingest API with acknowledgements and retry. The central server provides a read-only status page and filtered raw logs. An optional worker sends Telegram health alerts.

See [one-window VPS startup](docs/VPS_START.md), [collector usage](docs/collector.md), [central ingest setup](docs/ingest.md), [Telegram alerts](docs/alerts.md), [account worker setup](docs/accounts.md), and [alpha release/deployment](docs/release.md).

Run the fixture checks:

```text
python -m unittest discover -s tests -v
```

The status page reports collector and folder freshness plus Windows MT5 process state where installation matching succeeds. An optional Windows worker adds broker/AutoTrading status and verified account positions and realized PnL.
