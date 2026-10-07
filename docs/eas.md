# Expert Advisors page

The `/eas` page lists EAs attached to open charts, with the account login, broker server, Windows host, chart symbol, timeframe, and last scan. The dashboard does not show terminal folder names or paths. It labels missing and stale reports explicitly. An attached EA may still be unable to trade; this page does not prove that AutoTrading is enabled or that an EA's strategy logic is healthy.

The MetaTrader5 Python integration exposes account, orders, positions, deals, symbols, and terminal information, but no chart/EA enumeration. MT5's MQL5 chart API does expose `CHART_EXPERT_NAME` for an open chart. The included [DashboardEaProbe.mq5](../mql5/DashboardEaProbe.mq5) service reads those chart properties and writes a small report under that terminal's `MQL5\Files` folder. The existing collector forwards that report in its heartbeat. No EA names or account mappings need to be entered into `accounts.json` for this page. Keep `accounts.json` for verified position and PnL collection.

## Install on each terminal

1. In that terminal, choose **File → Open Data Folder**. Copy `mql5/DashboardEaProbe.mq5` into its `MQL5\Services` directory.
2. Open the source in MetaEditor and compile it. Confirm the compile has no errors.
3. In MT5 Navigator, expand **Services**, create one instance of **DashboardEaProbe** with **Add Service**, and start it. Repeat for every monitored MT5 terminal, including portable installations. MT5 restarts services that were running when the terminal shut down.
4. Confirm `MQL5\Files\dashboard-eas.tsv` appears in the same data folder and continues updating. The service waits 30 seconds between local scans by default; chart reads can make the actual interval longer. The dashboard receives the report on the next collector heartbeat (default five minutes).

The service only reads chart/account properties and writes a local report. It does not send orders or contact the central server. The collector reads the report from each terminal's own data folder, so accounts on different VPS hosts remain separate.

`No EA attached` means a recent scan found no EAs on open charts. `No probe report` means no report was found; check whether the service is installed and running. `Stale report` means the service report is older than 12.5 minutes or the terminal is explicitly stopped; the page does not count those EAs as currently observed. The collector itself is considered offline after 12.5 minutes without a heartbeat. `Collector offline` and `Terminal not seen` identify missing coverage separately. A disconnected account can make its report invalid until MT5 has a current login/server.

The service reports the EA name shown by MT5, but it cannot identify the EA version or input parameters of arbitrary third-party EAs. Those require each EA to publish its own metadata or a separate explicit registry.

Sources: [MQL5 chart properties](https://www.mql5.com/en/docs/constants/chartconstants/enum_chart_property), [chart enumeration](https://www.mql5.com/en/docs/chart_operations/chartfirst), [services](https://www.mql5.com/en/docs/runtime/running), [MQL5 file sandbox](https://www.mql5.com/en/docs/files/fileopen).
