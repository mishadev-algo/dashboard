# MT5 log collection and ingest prototype

Python collector for MT5 Journal and Experts logs. It discovers terminal data folders, stores complete lines in local SQLite, and can upload them to a central ingest API with acknowledgements and retry. See [collector usage](docs/collector.md) and [central ingest setup](docs/ingest.md).

Run fixture checks:

```text
python -m unittest discover -s tests -v
```

The dashboard and alerts are not included in this prototype. The ingest server currently uses SQLite; live VPS delivery and outage replay still need a network trial.
