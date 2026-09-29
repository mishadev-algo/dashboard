# MT5 log collector prototype

Python collector for MT5 Journal and Experts logs. It discovers terminal data folders and writes complete log lines to local SQLite. See [setup and usage](docs/collector.md).

Run fixture checks:

```text
python -m unittest discover -s tests -v
```

The remote ingest API, dashboard, and alerts are not included in this prototype.
