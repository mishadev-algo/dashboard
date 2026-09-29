# MT5 log dashboard prototype

Python collector for MT5 Journal and Experts logs. It discovers terminal data folders, stores complete lines in local SQLite, and uploads them to a central ingest API with acknowledgements and retry. The central server provides a read-only status page and filtered raw logs.

See [collector usage](docs/collector.md) and [central ingest setup](docs/ingest.md).

Run the fixture checks:

```text
python -m unittest discover -s tests -v
```

The status page reports collector and folder freshness. It does not yet check MT5 process or broker connection state. Alerts and account data are planned for later work.
