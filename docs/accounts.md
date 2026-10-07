# MT5 connection and account worker

This optional Windows worker reads broker connection, terminal AutoTrading state, open positions, and historical deals from specific running MT5 installations. It never calls order functions or passes account credentials to MT5. It checks the Windows process list before attaching, then verifies both MT5's reported data folder and the configured account login/server before accepting positions or deals. A stopped process is reported without calling `MetaTrader5.initialize()`. Because MT5 can close between the process check and the attach call, install and test this worker in a maintenance window.

The worker uses MetaQuotes' [`initialize`](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py), [`terminal_info`](https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py), [`account_info`](https://www.mql5.com/en/docs/python_metatrader5/mt5accountinfo_py), [`positions_get`](https://www.mql5.com/en/docs/python_metatrader5/mt5positionsget_py), and [`history_deals_get`](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py) APIs. `initialize` can launch MT5, which is why the worker checks the target process twice before calling it.

The status page shows broker and AutoTrading state separately from process state. The `/accounts` page has account tabs on the left and displays the selected account's content on the right. Its top filter accepts a start and end date; switching accounts keeps that range. With no dates selected, it shows all recorded history. The main view shows a separate realized-return chart and strategy statistics for the selected period, a balance chart for the entire available history, a symbol breakdown, and the 15 latest closed deals. Open positions remain under an expandable section. The `/portfolio` page compares strategies across accounts, separately for each currency. See [portfolio analytics](portfolio.md) for formulas, filters, and data limitations. A disconnected broker or wrong account does not replace the last complete positions; that older snapshot is marked stale. Only the configured account is accepted for a terminal. Deals are keyed by broker server, login, and ticket, so the same ticket on another account stays separate.

The alert worker sends one message for each newly observed exit deal after the account's first complete snapshot. Historical exit deals in that first snapshot establish a baseline and do not generate a burst of messages. See [Telegram alerts](alerts.md) for delivery behavior.

## Setup on the Windows VPS

Install `MetaTrader5` and `tzdata` in the same Python installation that runs the worker. `tzdata` supplies IANA time zones on Windows:

```powershell
py -3.13 -m pip install MetaTrader5 tzdata
```

The worker does not generate `accounts.json`. Copy [accounts.example.json](accounts.example.json) to `accounts.json` and replace every sample value. Add each active terminal that should have account data. Copy its exact data-folder path from `inventory.json`, then read its current login and broker server from MT5. These expected values prevent data from a switched or mismatched account being attributed to the wrong terminal. Set `day_timezone` to the time zone used when comparing a day in MT5 History; `UTC` is the default. It must be an IANA time zone: use `Etc/GMT-3` for a **fixed UTC+3** day, or `Europe/Kyiv` if the day follows Kyiv daylight saving time. `UTC+3` is not accepted by Python's `ZoneInfo`. If strategy magic numbers are known, map them to names in `strategies`. Unmapped positions and trades stay labeled `unmapped`. Account passwords do not belong in the file. The file is ignored by Git and stays on the VPS.

Restart the central server after updating its source. In another PowerShell window on the VPS, use the **same host ID and collector token** as the log collector:

```powershell
$env:DASHBOARD_COLLECTOR_TOKEN = 'YOUR_EXISTING_COLLECTOR_TOKEN'
py -3.13 -m collector.accounts --config accounts.json --host-id YOUR_EXISTING_HOST_ID --server-url http://127.0.0.1:8765 --follow
```

For a central server on another machine, use its authenticated HTTPS origin as `--server-url`. Run the worker under the Windows user that runs the target MT5 terminals, with no terminal listed in two worker configs. It polls every five minutes by default; `--interval` changes that. Without `--follow`, it performs one pass. The worker uses an isolated Python child process for each terminal, with a 30-second timeout. A missing Python MT5 package, inaccessible process path, or account mismatch appears as an unknown or mismatch status; the log collector keeps running.

## Verify against MT5

1. Open `/` and confirm separate `Running`, `Connected`, and `Enabled` or `Disabled` states for each configured terminal. An account mismatch must show `Account mismatch` and must not create account data.
2. Open `/accounts`. Compare open position tickets, symbol, volume, and profit with MT5. The page marks a snapshot stale after 750 seconds without a complete new reading.
3. Select the same start and end day to compare realized PnL for two accounts with MT5 History using the same day time zone. PnL includes `profit + commission + swap + fee` for trade and commission deals. The balance chart is reconstructed from the latest verified MT5 balance and stored account deals; MT5 credit is excluded because it is separate from balance. Its axis covers all recorded history regardless of the selected date range. Other broker adjustments may need separate reconciliation.
4. In a controlled test, disconnect the broker and reconnect it. The status page should show `Disconnected` then `Connected`; the Telegram worker should send one disconnect alert and one recovery. Check AutoTrading on/off as a separate state. Avoid changing live trading settings solely to test the dashboard.

The worker fetches the last `history_days` local calendar days on every successful pass (default seven, maximum 90). The database retains previously received deals, but the chart and period totals can only use history that has been collected. A temporary upload failure is retried with the next overlapping history fetch; do not treat the chart as a full account-lifetime ledger until VPS history has been reconciled. Accounts migrated from older snapshots show a balance placeholder until their next complete MT5 snapshot.

### Load older MT5 history on the current Windows VPS

The normal seven-day probe keeps recent data up to date. To fill older dates on the current all-in-one VPS, keep the dashboard task running and use the same Windows user that runs MT5. First create a verified backup as described in [release.md](release.md), then preview and apply the one-time backfill:

```powershell
py -3.13 -m server.backfill_history --db central.db --accounts accounts.json --since 2000-01-01 --dry-run
py -3.13 -m server.backfill_history --db central.db --accounts accounts.json --since 2000-01-01
```

The command checks each configured account, running terminal, and registered data path before writing. It inserts historical deals by ticket and marks newly discovered old closes as notification baselines, so the alert worker does not send a burst of old close messages. Re-running it is safe for existing deal tickets. MetaQuotes documents that [`history_deals_get`](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py) returns the deals available in the requested date interval. The probe currently accepts at most 10,000 deals in one account run; a larger history needs chunked backfill.
