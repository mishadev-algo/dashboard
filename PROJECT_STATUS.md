# Dashboard alpha — project status

**Updated:** 2026-09-29  
**Phase:** Collector, ingest, and first read-only status/log page prototype. Three active VPS data folders have both log streams in the user's local SQLite output. The first VPS loopback upload succeeded; the user reports completing the outage/recovery test and installing the status/log page. Real midnight rollover and full expected-terminal coverage remain deferred. Alerts and account data are not implemented.  
**Active ticket:** Add explicit expected-terminal inventory and validate the new Windows process probe when the user resumes VPS checks. Owner: Codex for implementation; VPS operator for live verification.  
**Current milestone acceptance:** The page shows collector heartbeat, folder discovery, and separate MT5 process `running`/`stopped`/`unknown` state, plus filtered Journal/Experts logs. A stopped state requires a readable installation path and complete Windows process list; broker connection and AutoTrading remain separate.

## Verified so far

- The collector discovers two fixture terminals and captures all four Journal/Experts streams in SQLite under the correct terminal IDs.
- Fixture checks pass for restart idempotency, partial lines, file replacement, missing expected folders, and an explicit portable folder (`python3 -m unittest discover -s tests -v`, 4 tests).
- On the September 29 check-in, the original four local collector tests passed. The new remote-delivery suite brings the total to 10 passing tests, including an existing-database migration, token rejection, handler authentication, outage replay, lost acknowledgement retry, and a previous-day/current-day file switch.
- A previous session recorded the Python collector, usage guide, and tests as pushed to `mishadev-algo/dashboard` on `main` at `ee70f8f`. This workspace has no `.git` directory, so that remote state was not rechecked at this check-in.
- The user's earlier VPS screenshot showed `discovered=4 events=814` on first scan. A later user-provided SQLite query showed three data folders with both streams: Experts/Journal counts of 261/31, 28/79, and 385/31, totaling 815 lines. One new line arrived after the first scan. A left-join query identified the fourth folder as `1FE10C17EA17AE7857194D46E9BABD7C`, with zero events. Its newest on-disk log was `20260922.log`, seven days before the September 29 check, so zero is expected under the default two-day initial lookback. The user confirmed only three terminals were running then.
- On September 29, the user reported that a newly added log line was captured and visible through the SQLite latest-line query after a scan showing `discovered=4 events=1`. The user then supplied SQLite counts for all six active terminal/stream groups: `9488CE...` Experts/Journal 522/62, `D0E820...` 56/158, and `E4CC92...` 784/62 (1,644 stored lines total). The latest-line output shows `20260929.log` entries from both streams in all three folders. The user's MT5 tab comparison was reported complete; the MT5 tab contents themselves were not supplied. The latest-line query returned two rows for each path/stream while the count query grouped by path/stream. This strongly suggests two host IDs (and thus two terminal IDs) for each folder after changing between explicit and default host IDs; confirm with `SELECT host_id, COUNT(*) FROM terminals GROUP BY host_id` on the VPS. This corrects the earlier paste-artifact interpretation.
- The supplied Experts samples show FX Blue Publisher `Unexpected HTTP response #400` in folders `9488CE...` and `E4CC92...`. This is an observed application error for follow-up, not evidence that log collection failed.
- The central ingest prototype uses per-host bearer tokens and SQLite, accepts idempotent event batches and heartbeats, and marks local rows delivered only after matching acknowledgements. The local test environment disallows loopback socket binding, so automated tests exercise request/handler and storage logic without a live socket; the VPS now supplies the live HTTP check. An outage trial on the VPS is still required. Existing local databases with multiple host IDs are rejected instead of silently uploading duplicate terminal identities; a new database can start the central trial while preserving the old one as an archive.
- On the VPS, the user ran the new server and collector over `127.0.0.1:8765`. Their screenshot shows `POST /v1/ingest` responses of `200`, an initial `discovered=4 events=887 uploaded=887 pending=0`, and subsequent quiet scans with `events=0 uploaded=0 pending=0`. This verifies live loopback delivery and acknowledgement for the initial batch. It does not yet demonstrate a network outage or prove expected-terminal coverage, since `--expected` was not supplied.
- The user then stopped the ingest server, observed collector upload errors, and restarted the server. A later collector line showed `events=0 uploaded=0 pending=0`. This demonstrates loss and restoration of API reachability, but does not establish backlog replay because no `events>0 pending>0` line during the outage or `uploaded>0` recovery line has been supplied.
- After the required `events>0 pending>0` / `uploaded>0 pending=0` check was explained, the user reported it complete. Treat VPS backlog replay as user-reported success; the exact before/after lines and central count were not supplied for independent verification.
- The user asked to postpone the remaining time-consuming VPS tests and continue development. Real midnight rollover and a full expected-terminal coverage trial remain open.
- Git publication is still pending. On September 29, GitHub `mishadev-algo/dashboard` had `main` at `ee70f8fd4c772db639ba013d816bd95b56b78282`. A temporary checkout produced tested ingest commit `405f19f`, but `git push --dry-run origin main` was rejected with `Invalid username or token`. The connected GitHub account is still `ShotaMk` with `push: false`. A source ZIP and an ingest-only Git bundle are available locally; neither has been pushed.
- The first status-page slice added `GET /` for collector/folder status and `GET /logs` for filtered raw lines. It distinguishes stale collector, missing folder, and quiet log timestamps. The Git bundle remains the earlier ingest-only commit; the updated source ZIP includes the page.
- The user then reported installing the status/log page on the VPS; no screenshot or page output was supplied. A further local slice adds a nonblocking Windows process query matched through `origin.txt` or a portable executable and sends `running`/`stopped`/`unknown` separately from log freshness. Sixteen fast local tests pass, including path matching and inaccessible-process fallback. This process probe is packaged but has not been validated on the VPS.
- See [collector usage](docs/collector.md).

## Prioritized backlog

| Priority | Work | Acceptance criteria |
| --- | --- | --- |
| P0 | Establish discovery roots and verify expected terminals | Use the path in [mt5-list.md](mt5-list.md) as the first real example; identify accessible roots on each host. Before alpha release, reconcile discovered folders against every running instance, including portable/custom folders. |
| P0 | Define ingestion contract and storage | Local and central SQLite schemas, idempotent API acknowledgements, and host heartbeats are implemented and fixture-tested. Validate live delivery; choose hosting and move central storage to PostgreSQL before release. |
| P0 | Build all-terminal log listener | Fixture discovery/tailing and simulated outage replay are verified. Validate real MT5 encoding/access, midnight rollover, and API outage/recovery with no lost or duplicate lines. |
| P1 | Show terminal health and logs | Dashboard distinguishes stopped terminal, stale collector, missing folder, and quiet logs; operator can filter raw logs by terminal and stream. |
| P1 | Add actionable Telegram alerts | A simulated failure sends one alert and one recovery message, with cooldown and visible alert state. |
| P1 | Add account positions and realized PnL | Two accounts reconcile against MT5 History; account changes do not mix deals; unmapped strategy trades remain marked unmapped. |
| P1 | Run alpha trial | Trial crosses a trading day and midnight; every expected terminal is accounted for, logs replay once after an outage, health and alerts behave correctly, and PnL reconciles. |

## Risks and unknowns

- [mt5-list.md](mt5-list.md) supplies one VPS label, MT5 data path, and account label. The total number of Windows hosts, users, MT5 installs, portable folders, and access to them is not documented yet.
- Log encoding, buffering, file replacement behavior, midnight rollover, and Windows permissions need validation on real terminals. Fixture tests do not cover midnight or an API outage.
- Hosting location, secure collector connectivity, and Telegram destination are undecided.
- The current VPS `collector.db` appears to contain two host IDs for the same three folders. Confirm the IDs on the VPS and use a fresh database for the central trial unless the historical records are reconciled. Keep the old database as an archive.
- The local upload queue has a warning threshold but no hard bound or automatic alert yet. The built-in ingest server requires an HTTPS reverse proxy for remote use.
- Broker connection and AutoTrading status require a per-terminal probe; logs alone cannot prove those states.
- `PortfolioManager` in [idea.md](idea.md) has no defined role in the alpha.

## Next concrete action

Next implementation work is a one-time expected-terminal inventory that identifies the three active folders without repeatedly typing paths. When convenient, copy the updated ZIP to the VPS and restart both collector and central server to activate the process probe, then inspect `http://127.0.0.1:8765/`. No long VPS test is required now. Later, resume the deferred real midnight rollover and coverage trial, rotate the token before remote exposure, choose a permanent HTTPS endpoint, and collect other host roots before alpha release. Confirm the old database's two apparent host IDs separately; the fresh trial database avoids mixing them.

## PM check-in format

At each check-in, update the date, phase, completed acceptance criteria, active ticket, blockers, and next action. Mark work done only when its acceptance criteria have been demonstrated.
