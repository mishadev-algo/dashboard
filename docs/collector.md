# Collector prototype

The collector discovers MT5 data folders and stores complete Journal and Experts log lines in a local SQLite database. With `--server-url`, it also uploads the stored lines to the central ingest API and retries unsent lines after an outage. It does not read trade data or connect to MT5. See [central ingest setup](ingest.md).

On the Windows VPS, install Python from PowerShell or Command Prompt if needed:

```text
winget install --id Python.Python.3.13 -e --source winget
```

Close and reopen the shell, then check `py -3.13 --version`. This collector needs Python 3.10+ and has no pip dependencies. [Windows Package Manager documentation](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)

If `winget` is unavailable, use PowerShell to download the official 64-bit Python 3.13.15 installer and verify its SHA-256 before running it:

```powershell
$installer = Join-Path $env:TEMP 'python-3.13.15-amd64.exe'
Invoke-WebRequest 'https://www.python.org/ftp/python/3.13.15/python-3.13.15-amd64.exe' -OutFile $installer
if ((Get-FileHash $installer -Algorithm SHA256).Hash -ne 'EDEC09C4853AEAE9AC36EFB8C9F95B6B8E2FEE65EEE56D9767A8B7C69C574403') { throw 'Python installer checksum mismatch' }
Start-Process -FilePath $installer -ArgumentList '/passive InstallAllUsers=0 PrependPath=1 Include_test=0' -Wait
```

Close and reopen PowerShell, then run `py -3.13 --version`. The download and checksum come from the [official Python 3.13.15 release](https://www.python.org/downloads/release/python-31315/); the installer flags are documented in [Python's Windows installation guide](https://docs.python.org/3.13/using/windows.html). This installs for the current Windows user.

Run from this project directory:

```powershell
py -3.13 -m collector --db collector.db --host-id vps-92 --root "C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal" --follow
```

The standard `%APPDATA%\MetaQuotes\Terminal` root is used automatically when no `--root` or `--terminal` is supplied. Repeat `--root` for other parent directories and `--terminal` for a specific portable data folder. `--expected` can be repeated to report missing known folders. Without `--follow`, the collector performs one scan and exits.

An MT5 data folder under the standard root has `Logs` and `MQL5\Logs` subfolders. They must be readable by the Windows user running the collector. The collector discovers sibling MT5 folders under the same root automatically.

Run the local fixture checks with:

```text
python -m unittest discover -s tests -v
```

The fixture creates two terminals and proves that four labeled streams enter SQLite, with no duplicate rows after a restart. It also checks partial lines, file replacement, missing expected folders, and an explicit portable folder.

The collector currently scans new files from the last two days by default and continues tracking files it has already seen. Use `--lookback-days` to widen initial history. Logs can be buffered by MT5 before they reach disk, so file collection can lag the terminal UI.

When remote delivery is enabled on Windows, the collector also checks MT5 process executable paths about every 30 seconds. It matches normal installs through each data folder's `origin.txt` and portable installs through a terminal executable in the data folder. If it cannot read an install or process path, it reports process state as `unknown`; log silence alone does not imply a stopped terminal.

The console's `events` number counts newly stored complete lines in that scan. A large first count followed by zero is normal when there are no new lines on disk. `discovered` counts folders found under configured roots. Empty `missing` and `unknown` values do not prove full coverage unless `--expected` was supplied.

With remote delivery enabled, `uploaded` counts acknowledged lines in the scan and `pending` counts lines still waiting in the local SQLite database. Existing `collector.db` files with one host ID are upgraded automatically with a delivery column; previously captured lines are queued for the first upload. The collector reuses that existing host ID unless `--host-id` is given. It refuses a database containing multiple host IDs, since those would appear as separate hosts centrally. Keep such a database as an archive and use a new `--db` for the central trial. The collector advances its file cursor only after lines are committed locally, so it can keep reading during a network outage. It marks lines delivered only after the API acknowledges their event IDs. A `QUEUE_WARNING` appears at 100,000 pending lines by default; the queue is not yet capped.

To inspect counts by terminal and stream in a second PowerShell window, from the same directory as `collector.db` run:

```powershell
py -3.13 -c "import sqlite3; c=sqlite3.connect('collector.db'); print(*c.execute('SELECT t.data_path, e.stream, COUNT(*) FROM log_events e JOIN terminals t USING (terminal_id) GROUP BY t.data_path, e.stream ORDER BY t.data_path, e.stream'), sep='\n')"
```

Compare a few stored lines with MT5's Journal and Experts tabs. A missing stream can simply mean that its log file has no complete lines in the scanned dates; inspect the terminal's data folder before treating it as a collector failure.

The query above omits discovered terminals with zero events. To list **every** discovered folder, including zero counts, run:

```powershell
py -3.13 -c "import sqlite3; c=sqlite3.connect('collector.db'); print(*c.execute('SELECT t.data_path, COUNT(e.rowid), MAX(e.file_name) FROM terminals t LEFT JOIN log_events e USING (terminal_id) GROUP BY t.terminal_id ORDER BY t.data_path'), sep='\n')"
```

The last column is the latest log-file name ingested for that folder, or `None`. Discovery checks data folders, not whether their terminal processes are currently running.
