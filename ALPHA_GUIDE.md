# MT5 dashboard: step-by-step path to alpha

This guide turns [idea.md](idea.md) into a build order. The first requirement is a **log listener for every MT5 terminal**. "Every" means every registered MT5 data folder on each monitored Windows host, including portable installations. If terminals run on several VPSs, install a collector on each VPS; a collector cannot read another machine's local files. If MT5 runs under different Windows users on one host, run a collector for each user or grant one collector access to every configured data root.

## Alpha outcome

The alpha should show, on one page, all registered terminals and whether each is running, when its Journal and Experts logs were last received, recent errors, and the current account. It should also show basic account positions, realized PnL, a list of closed deals, and a small set of actionable Telegram alerts. Send one Telegram notification with account, instrument, position/deal identity, and result when a new close is observed. The dashboard is read-only: it does not place or modify trades.

**Out of alpha:** automated live-versus-backtest comparison, AI diagnosis, full EA version/parameter inventory, and automated detection of different trade decisions across accounts. Those need reliable trade and EA identity data first. Keep the data model ready for them.

## Architecture

```text
Windows VPS A: MT5 instances -> local collector ---+
Windows VPS B: MT5 instances -> local collector ---+--> ingest API -> PostgreSQL -> web dashboard
                                                        \-> alert worker -> Telegram
```

Suggested small stack: Python collector, Python/FastAPI API and server-rendered dashboard, PostgreSQL, and one alert worker. Run the collector as a Windows scheduled task at startup; run the server components on one always-on machine. Use HTTPS and a separate collector token per host.

## Build steps

### 1. Record a real MT5 example and discovery roots

- Start with [mt5-list.md](mt5-list.md) as a real data-folder example. Confirm that the collector can read its Journal and Experts folders. Record the Windows user and machine identifier needed to access it; a shortened VPS label alone may not identify a host uniquely.
- For other hosts, record the Windows users and root directories the collector will scan. You can build automatic discovery and a two-terminal fixture before the complete manual inventory exists.
- In normal mode, MT5 commonly stores data under `%APPDATA%\MetaQuotes\Terminal\<instance_id>`. The folder's `origin.txt` points to its installation. Portable mode stores data under the installation directory. Multiple running terminals require separate installation directories. [MT5 startup documentation](https://www.metatrader5.com/en/terminal/help/start_advanced/start)
- Record portable roots and any other nonstandard data roots explicitly in collector configuration. Give each terminal a stable ID based on **host ID + canonical data-folder path**; keep the account identity separate because a terminal can switch accounts. The install path and account/server help later with process health and account data; they are not required to begin log tailing.

**Done when:** one real path is usable as a collector example and every host's scan roots are known. Complete the expected-terminal inventory before the alpha release gate, using automatic discovery and manual checking together.

### 2. Create the repository skeleton and data contract

- Add `collector/`, `server/`, `web/`, `migrations/`, `tests/`, and `docs/`.
- Define records before implementation: `hosts`, `terminals`, `log_events`, `collector_heartbeats`, `accounts`, `deals`, `position_snapshots`, and `alerts`.
- A log event needs host ID, terminal ID, stream (`journal` or `experts`), source file date/name and generation, byte offset or sequence, raw line, parsed local timestamp, source, message, receive time, and parse status.
- Make `(host_id, terminal_id, stream, file_name, file_generation, byte_offset)` unique so retries cannot duplicate an event while a replaced or truncated file can still be ingested. Store times with an explicit source time zone and UTC receive time; MT5 journal time follows the computer's local time. [MT5 log documentation](https://www.metatrader5.com/en/terminal/help/start_advanced/journal)

**Done when:** a sample event and heartbeat can be inserted, queried, and safely inserted again.

### 3. Build the all-terminal log listener (first working milestone)

- On startup and periodically, scan the standard MT5 data root for folders containing MT5 structure, then add configured portable/custom roots. Compare discovered folders with any expected terminals already registered. Display unknown folders and missing expected folders; never silently drop either.
- For **each** terminal, tail both `<data folder>\Logs\YYYYMMDD.log` (Journal) and `<data folder>\MQL5\Logs\YYYYMMDD.log` (Experts). MT5 writes a daily file for each stream. [MT5 file structure](https://www.metatrader5.com/en/terminal/help/start_advanced/structure)
- Start with the current file and a configurable lookback (for example, the previous day). Read appended bytes incrementally. Persist per-file offsets locally, complete only whole lines, and reopen on date change, truncation, replacement, or collector restart. Test the actual file encoding on the target terminals and decode accordingly; preserve the raw text when parsing fails.
- Batch-send events to the API. Save offsets only after server acknowledgement. Queue locally during network outages and replay after reconnection. Bound the queue and alert if it approaches its limit.
- Send a heartbeat with every discovered terminal, both file states, collector health, and last successful upload. A running terminal with no new log lines is not automatically unhealthy.
- Treat log delivery as **eventual**, because MT5 can buffer log writes before they appear on disk. The UI should show the last received time, not claim real-time visibility. [MQL5 logging behavior](https://www.mql5.com/en/book/common/output/output_print)

**Done when:** with two or more MT5 instances, a fresh Journal and Experts message from each appears under the correct terminal; restarting the collector and crossing midnight neither loses nor duplicates lines; disconnecting the API and reconnecting replays the backlog.

### 4. Add terminal and collector health

- On each host, compare discovered instances with running MT5 processes and their installation paths. Report `running`, `stopped`, or `unknown` separately from `logs_recent`.
- Mark a collector offline if its heartbeat is stale. Mark a registered terminal missing if its folder is no longer discovered. Keep these states distinct so an empty Journal does not become a false outage.
- For broker connection and AutoTrading state, add a small read-only MQL5 status probe per terminal or an explicitly targeted MT5 API worker. The probe should report account login/server, terminal build, connection state, trading permission, and a heartbeat. MT5 exposes connection and trade permission properties through `TerminalInfoInteger`. [MQL5 terminal properties](https://www.mql5.com/en/docs/constants/environment_state/terminalstatus)

**Done when:** stopping one terminal, disconnecting one broker connection, and stopping one collector produce three different states on the dashboard.

### 5. Build the first dashboard page

- Top row: number of expected, discovered, running, and unhealthy terminals; last collector heartbeat per host.
- Terminal table: friendly name, host, account/server, running state, connection state if probe is installed, Journal/Experts last received times, recent error count, and missing-inventory flag.
- Log view: filter by host, terminal, stream, time, and text; retain the raw line even when a parser cannot classify it.
- Avoid inferring EA identity or a trade outcome solely from a free-text log line.

**Done when:** an operator can find the origin of an error in under a minute and can see which registered terminal has stopped sending data.

### 6. Add a few Telegram alerts

- Start with collector offline, expected terminal stopped/missing, broker disconnected (if probe available), and selected repeated Journal/Experts errors.
- Deduplicate by terminal + alert type, add a cooldown and a recovery message, and expose the alert state in the UI. Do not send every log line as a Telegram message.
- Keep bot token and chat ID in server secrets, not in source or log messages. Telegram's Bot API supports sending text with `sendMessage`. [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage)
- Monitor server availability from another host so a server or VPS outage sends a notification while the service is down. A terminal that is explicitly stopped must also trigger a timely notification; verify both failure and recovery delivery on a safe test setup.

**Done when:** each simulated failure sends one useful alert and one recovery notice, without alert storms.

**Observed on the current VPS:** the server-down message arrives only after recovery, and stopping a terminal has not produced a Telegram notification. Treat these as open live validation issues until the failure and recovery messages are observed with timestamps.

### 7. Add basic account data and PnL

- Use a separate read-only data worker, targeted at each known terminal executable, to collect account info, open positions, and historical deals. The MT5 Python API can target an executable path; its `initialize()` call may launch a terminal, so the health checker must first verify that the intended terminal is already running. [MT5 Python integration](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py)
- Run one isolated worker process per terminal and verify that the returned account login/server matches the expected mapping before accepting data. Never mix deals from two accounts that used the same terminal at different times.
- Store deals by broker/server + account login + deal ticket. Show open positions and realized PnL by account and date in the account currency. Reconcile the result against MT5 History, including commission, swap, and fees; keep deposits/withdrawals separate from trading PnL. The official deal API exposes the required fields. [Deal history API](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py), [deal properties](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties)
- Map `magic` numbers and comments to strategies only where there is an explicit registry. Show `unmapped` otherwise; preserve the raw values for later strategy comparison.
- Keep exit-deal history visible for each account and retain the data needed for a future equity chart. A true account equity curve requires periodic balance and equity samples, including floating PnL; closed deals alone support a realized-PnL curve. Confirm which curve is required before designing the sampling and retention policy.

**Done when:** positions and a chosen day's realized PnL match MT5 for two different accounts, and unmapped trades stay visibly unmapped.

### 8. Run an end-to-end alpha trial

- Run continuously across a trading day and a midnight file rollover. Include at least two terminals on one host; if terminals also live on another VPS, include that host too.
- Verify every expected terminal appears in the coverage table. Restart a terminal, the collector, and the API separately. Simulate a short network outage and confirm queued logs replay once.
- Compare a small sample of Journal and Experts entries with MT5 itself. Compare positions and PnL against MT5 History. Confirm Telegram failure/recovery notifications.
- Document installation, configuration, backup, restart, and a simple way to inspect collector errors.

**Alpha release gate:** zero unaccounted expected terminals; both log streams represented for every terminal that produces them; no lost or duplicate lines in the trial; clear offline/missing states; correct account attribution and reconciled PnL; closed-deal history and notifications verified; server and terminal outage alerts delivered during failure and recover cleanly.

## Next after alpha

1. Add an explicit strategy/EA registry: strategy ID, magic numbers, symbols, expected account/chart, EA version, and parameter-set fingerprint.
2. Collect structured EA heartbeats from each expected chart to verify that the right EA and parameter set are loaded. A terminal heartbeat alone cannot prove this.
3. Compare equivalent deals between accounts, then compare live results with imported backtest runs using the same strategy and period.
4. Add an AI summary only after deterministic checks expose concrete mismatches and supporting logs; keep the underlying evidence visible.

## First implementation ticket

Implement steps 1–3 with a small test fixture containing two terminal data folders and both log streams. The first demo should show four labeled streams entering the database and a coverage report that names any missing terminal. This gives us a measurable starting point for the rest of the alpha.
