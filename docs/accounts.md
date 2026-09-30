# MT5 connection and account worker

This optional Windows worker reads broker connection, terminal AutoTrading state, open positions, and historical deals from specific running MT5 installations. It never calls order functions or passes account credentials to MT5. It checks the Windows process list before attaching, then verifies both MT5's reported data folder and the configured account login/server before accepting positions or deals. A stopped process is reported without calling `MetaTrader5.initialize()`. Because MT5 can close between the process check and the attach call, install and test this worker in a maintenance window.

The worker uses MetaQuotes' [`initialize`](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py), [`terminal_info`](https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py), [`account_info`](https://www.mql5.com/en/docs/python_metatrader5/mt5accountinfo_py), [`positions_get`](https://www.mql5.com/en/docs/python_metatrader5/mt5positionsget_py), and [`history_deals_get`](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py) APIs. `initialize` can launch MT5, which is why the worker checks the target process twice before calling it.

The status page shows broker and AutoTrading state separately from process state. The `/accounts` page shows the latest complete open-position snapshot and realized trading PnL for a selected day, with a strategy breakdown where the deal magic number has an explicit mapping. A disconnected broker or wrong account does not replace the last complete positions; that older snapshot is marked stale. Only the configured account is accepted for a terminal. Deals are keyed by broker server, login, and ticket, so the same ticket on another account stays separate.

## Setup on the Windows VPS

Install `MetaTrader5` and `tzdata` in the same Python installation that runs the worker. `tzdata` supplies IANA time zones on Windows:

```powershell
py -3.13 -m pip install MetaTrader5 tzdata
```

Copy [accounts.example.json](accounts.example.json) to `accounts.json` and replace every sample value. Add each active terminal that should have account data. Use the exact data-folder path, current MT5 login, and broker server shown in MT5. Set `day_timezone` to the time zone used when comparing a day in MT5 History; `UTC` is the default. If strategy magic numbers are known, map them to names in `strategies`. Unmapped positions and trades stay labeled `unmapped`. Account passwords do not belong in the file.

Restart the central server after updating its source. In another PowerShell window on the VPS, use the **same host ID and collector token** as the log collector:

```powershell
$env:DASHBOARD_COLLECTOR_TOKEN = 'YOUR_EXISTING_COLLECTOR_TOKEN'
py -3.13 -m collector.accounts --config accounts.json --host-id YOUR_EXISTING_HOST_ID --server-url http://127.0.0.1:8765 --follow
```

For a central server on another machine, use its authenticated HTTPS origin as `--server-url`. Run the worker under the Windows user that runs the target MT5 terminals, with no terminal listed in two worker configs. It polls every 60 seconds by default; `--interval` changes that. Without `--follow`, it performs one pass. The worker uses an isolated Python child process for each terminal, with a 30-second timeout. A missing Python MT5 package, inaccessible process path, or account mismatch appears as an unknown or mismatch status; the log collector keeps running.

## Verify against MT5

1. Open `/` and confirm separate `Running`, `Connected`, and `Enabled` or `Disabled` states for each configured terminal. An account mismatch must show `Account mismatch` and must not create account data.
2. Open `/accounts`. Compare open position tickets, symbol, volume, and profit with MT5. The page marks a snapshot stale after 150 seconds without a complete new reading.
3. Select one day and compare realized PnL for two accounts with MT5 History using the same day time zone. PnL includes `profit + commission + swap + fee` for trade and commission deals. Balance and credit movements appear separately as cash transfers. Other broker adjustments are excluded pending reconciliation.
4. In a controlled test, disconnect the broker and reconnect it. The status page should show `Disconnected` then `Connected`; the Telegram worker should send one disconnect alert and one recovery. Check AutoTrading on/off as a separate state. Avoid changing live trading settings solely to test the dashboard.

The worker fetches the last `history_days` local calendar days on every successful pass (default seven, maximum 90). Older dates show PnL as unavailable. A temporary upload failure is retried with the next overlapping history fetch; do not use the page as a full historical ledger until VPS reconciliation is complete.
