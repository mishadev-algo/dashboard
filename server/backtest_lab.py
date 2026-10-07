"""Import isolated backtest ledgers and compare fixed-allocation portfolios."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
from io import BytesIO, StringIO
from itertools import combinations
from math import sqrt
import re
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from .analytics_web import line_chart, number
from .web import _escape, _page


MAX_SELECTED = 10
MAX_ROWS = 100_000
MAX_XLSX_UNPACKED = 64 * 1024 * 1024
ZERO = Decimal(0)
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def ensure_backtest_schema(connection) -> None:
    statements = (
        "CREATE TABLE IF NOT EXISTS backtest_runs ("
        "run_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, file_name TEXT NOT NULL, "
        "file_hash TEXT NOT NULL UNIQUE, currency TEXT NOT NULL, starting_capital TEXT NOT NULL, "
        "first_day TEXT NOT NULL, last_day TEXT NOT NULL, row_count INTEGER NOT NULL, imported_utc TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS backtest_days ("
        "run_id TEXT NOT NULL, day TEXT NOT NULL, net_pnl TEXT NOT NULL, "
        "PRIMARY KEY (run_id, day), FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE)",
        "CREATE INDEX IF NOT EXISTS idx_backtest_days_day ON backtest_days(day)",
    )
    with connection:
        for statement in statements:
            connection.execute(statement)


def _decimal(value: str) -> Decimal:
    text = str(value).strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".") if len(text.rsplit(",", 1)[1]) <= 2 else text.replace(",", "")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid monetary value: {value!r}") from exc
    if not result.is_finite() or abs(result) > 10**15:
        raise ValueError("Monetary value is out of range")
    return result


def _day(value: str) -> str:
    value = value.strip().replace("\u00a0", " ")
    match = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})(?:\s|T|$)", value)
    if not match:
        raise ValueError(f"Invalid date: {value!r}; use YYYY-MM-DD or YYYY.MM.DD")
    try:
        return date(*(int(part) for part in match.groups())).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value!r}") from exc


def _field(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self.depth = 0
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.tables.append([])
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.row is not None and tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None and self.depth == 1:
            self.tables[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1


def _records(rows: list[list[str]]) -> tuple[dict[str, Decimal], int]:
    header_index = next((i for i, row in enumerate(rows) if
                         any(_field(cell) in ("date", "time", "datetime") for cell in row) and
                         any(_field(cell) in ("netpnl", "netprofit", "profit", "pnl") for cell in row)), None)
    if header_index is None:
        raise ValueError("No date/time and net_pnl or profit columns found")
    fields = [_field(cell) for cell in rows[header_index]]
    date_i = next(i for i, field in enumerate(fields) if field in ("date", "time", "datetime"))
    net_i = next((i for i, field in enumerate(fields) if field in ("netpnl", "netprofit", "pnl")), None)
    profit_i = next((i for i, field in enumerate(fields) if field == "profit"), None)
    amount_i = net_i if net_i is not None else profit_i
    type_i = next((i for i, field in enumerate(fields) if field in ("type", "dealtype")), None)
    cost_indices = [i for i, field in enumerate(fields) if field in ("commission", "swap", "fee")]
    daily: dict[str, Decimal] = {}
    count = 0
    for row in rows[header_index + 1:]:
        if len(row) <= max(date_i, amount_i) or not row[date_i].strip():
            continue
        try:
            day = _day(row[date_i])
        except ValueError:
            if re.match(r"^\d{4}[-./]", row[date_i].strip()):
                raise
            continue  # Report footers and section headings are not deals.
        if type_i is not None and len(row) > type_i:
            kind = _field(row[type_i])
            if kind in ("balance", "credit", "deposit", "withdrawal", "withdraw", "transfer"):
                continue
        if not row[amount_i].strip():
            continue
        amount = _decimal(row[amount_i])
        if net_i is None:
            amount += sum((_decimal(row[i]) for i in cost_indices if i < len(row) and row[i].strip()), ZERO)
        daily[day] = daily.get(day, ZERO) + amount
        count += 1
        if count > MAX_ROWS:
            raise ValueError("Too many backtest rows")
    if not count:
        raise ValueError("No dated result rows found")
    return daily, count


def _xlsx_rows(content: bytes) -> list[list[list[str]]]:
    """Read worksheet values from an XLSX without requiring an Excel installation."""
    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_XLSX_UNPACKED:
                raise ValueError("XLSX contents exceed 64 MB")
            names = archive.namelist()
            sheets = sorted(name for name in names if re.fullmatch(r"xl/worksheets/[^/]+\.xml", name))
            if not sheets:
                raise ValueError("No worksheets found in XLSX file")
            strings: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                strings = ["".join(node.itertext()) for node in root.findall(f"{XLSX_NS}si")]
            date_styles: set[int] = set()
            if "xl/styles.xml" in names:
                root = ET.fromstring(archive.read("xl/styles.xml"))
                formats = {int(node.attrib["numFmtId"]): node.attrib.get("formatCode", "")
                           for node in root.findall(f"{XLSX_NS}numFmts/{XLSX_NS}numFmt")}
                for index, xf in enumerate(root.findall(f"{XLSX_NS}cellXfs/{XLSX_NS}xf")):
                    fmt_id = int(xf.attrib.get("numFmtId", "0"))
                    fmt = re.sub(r'"[^"]*"|\\.', "", formats.get(fmt_id, "")).lower()
                    if fmt_id in set(range(14, 23)) | set(range(27, 37)) | set(range(50, 59)) or (
                            fmt_id >= 164 and re.search(r"[dy]", fmt)):
                        date_styles.add(index)
            date_1904 = False
            if "xl/workbook.xml" in names:
                root = ET.fromstring(archive.read("xl/workbook.xml"))
                props = root.find(f"{XLSX_NS}workbookPr")
                date_1904 = props is not None and props.attrib.get("date1904", "0") in ("1", "true")
            epoch = datetime(1904, 1, 1) if date_1904 else datetime(1899, 12, 30)
            worksheets = []
            for name in sheets:
                root = ET.fromstring(archive.read(name))
                rows = []
                for element in root.findall(f".//{XLSX_NS}sheetData/{XLSX_NS}row"):
                    row: list[str] = []
                    for cell in element.findall(f"{XLSX_NS}c"):
                        ref = re.match(r"([A-Z]+)", cell.attrib.get("r", ""))
                        column = 0
                        if ref:
                            for letter in ref.group(1):
                                column = column * 26 + ord(letter) - ord("A") + 1
                            column -= 1
                        else:
                            column = len(row)
                        if column > 1000:
                            raise ValueError("XLSX has too many columns")
                        row.extend([""] * max(0, column + 1 - len(row)))
                        kind = cell.attrib.get("t", "")
                        value_node = cell.find(f"{XLSX_NS}v")
                        value = value_node.text if value_node is not None and value_node.text else ""
                        if kind == "s" and value:
                            value = strings[int(value)]
                        elif kind == "inlineStr":
                            inline = cell.find(f"{XLSX_NS}is")
                            value = "".join(inline.itertext()) if inline is not None else ""
                        elif value and (kind == "d" or int(cell.attrib.get("s", "0")) in date_styles):
                            if kind != "d":
                                value = (epoch + timedelta(days=float(value))).isoformat(sep=" ")
                        row[column] = value
                    rows.append(row)
                    if len(rows) > MAX_ROWS + 1000:
                        raise ValueError("Too many XLSX rows")
                worksheets.append(rows)
            return worksheets
    except (BadZipFile, ET.ParseError, KeyError, IndexError, OverflowError) as exc:
        raise ValueError("Invalid XLSX file") from exc


def parse_backtest(content: bytes, file_name: str) -> tuple[dict[str, Decimal], int]:
    if len(content) > 4 * 1024 * 1024:
        raise ValueError("Backtest file exceeds 4 MB")
    if not file_name.lower().endswith((".csv", ".htm", ".html", ".xlsx")):
        raise ValueError("Upload a CSV, XLSX, or MT5 HTML report")
    if file_name.lower().endswith(".xlsx"):
        candidates = []
        for rows in _xlsx_rows(content):
            try:
                candidates.append(_records(rows))
            except ValueError as exc:
                if str(exc) in ("Too many backtest rows", "Monetary value is out of range"):
                    raise
        if not candidates:
            raise ValueError("No dated Deals table with a Profit column found in XLSX file")
        return max(candidates, key=lambda item: item[1])
    try:
        decoded = (content.decode("utf-16") if content.startswith((b"\xff\xfe", b"\xfe\xff"))
                   else content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        decoded = content.decode("cp1252")
    if file_name.lower().endswith((".htm", ".html")):
        parser = _Tables()
        parser.feed(decoded)
        candidates = []
        for table in parser.tables:
            try:
                candidates.append(_records(table))
            except ValueError:
                continue
        if not candidates:
            raise ValueError("No dated Deals table with a Profit column found in HTML report")
        return max(candidates, key=lambda item: item[1])
    sample = decoded[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return _records(list(csv.reader(StringIO(decoded), dialect)))


def import_backtest(connection, *, strategy: str, currency: str, starting_capital: str,
                    file_name: str, content: bytes) -> str:
    strategy = strategy.strip()
    currency = currency.strip().upper()
    if not strategy or len(strategy) > 120:
        raise ValueError("Enter a strategy name (up to 120 characters)")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Enter a three-letter currency code")
    capital = _decimal(starting_capital)
    if capital < Decimal("0.01"):
        raise ValueError("Starting capital must be at least 0.01")
    file_name = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:180]
    daily, count = parse_backtest(content, file_name)
    digest = sha256(content).hexdigest()
    run_id = digest[:24]
    if connection.execute("SELECT 1 FROM backtest_runs WHERE file_hash=?", (digest,)).fetchone():
        raise ValueError("This backtest file has already been imported")
    with connection:
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, strategy, file_name, digest, currency, str(capital), min(daily), max(daily),
             count, datetime.now(timezone.utc).isoformat()),
        )
        connection.executemany("INSERT INTO backtest_days VALUES (?,?,?)",
                               [(run_id, day, str(amount)) for day, amount in sorted(daily.items())])
    return run_id


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 20:
        return None
    mean_a, mean_b = sum(left) / len(left), sum(right) / len(right)
    aa = sum((x - mean_a) ** 2 for x in left)
    bb = sum((y - mean_b) ** 2 for y in right)
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(left, right)) / sqrt(aa * bb) if aa and bb else None


def _metrics(returns: list[float]) -> dict[str, float | None]:
    total = sum(returns)
    running = 0.0
    peak = 0.0
    drawdown = 0.0
    drawdown_from_initial = 0.0
    for value in returns:
        running += value
        peak = max(peak, running)
        if 1 + peak > 0:
            current_drawdown = (peak - running) / (1 + peak)
            if current_drawdown > drawdown:
                drawdown = current_drawdown
                drawdown_from_initial = peak - running
    active = [value for value in returns if value]
    if len(active) >= 20:
        mean = sum(active) / len(active)
        variance = sum((value - mean) ** 2 for value in active) / (len(active) - 1)
        sharpe = mean / sqrt(variance) * sqrt(252) if variance else None
    else:
        sharpe = None
    return {"return": total * 100, "drawdown": drawdown * 100,
            "drawdown_from_initial": drawdown_from_initial,
            "return_drawdown": total / drawdown if drawdown else None,
            "sharpe": sharpe, "active_days": len(active)}


def _days(start: str, end: str):
    current, final = date.fromisoformat(start), date.fromisoformat(end)
    while current <= final:
        yield current.isoformat()
        current += timedelta(days=1)


def render_backtest_lab(connection, params: dict[str, list[str]], message: str = "", csrf_token: str = "") -> str:
    rows = connection.execute("SELECT run_id,strategy,file_name,currency,starting_capital,first_day,last_day,row_count "
                              "FROM backtest_runs ORDER BY imported_utc DESC").fetchall()
    runs = {row[0]: row for row in rows}
    chosen_ids = [value for value in params.get("run", []) if value in runs]
    if not params:
        chosen_ids = list(runs)[:MAX_SELECTED]
    chosen_ids = list(dict.fromkeys(chosen_ids))
    currency = params.get("currency", [""])[0]
    if not currency and chosen_ids:
        currency = runs[chosen_ids[0]][3]
    chosen_ids = [key for key in chosen_ids if runs[key][3] == currency]
    if params.get("run") and not chosen_ids and currency in {row[3] for row in rows}:
        chosen_ids = [row[0] for row in rows if row[3] == currency][:MAX_SELECTED]
    body = ('<h1>Backtest Lab</h1><p class="muted">Import strategy test results, combine fixed allocations, '
            'and inspect historical daily-return relationships.</p>')
    if message:
        body += f'<p class="note" role="status">{_escape(message)}</p>'
    body += ('<section class="panel"><h2>Import a backtest</h2>'
             '<p class="muted">CSV or XLSX: date,net_pnl or time,profit,commission,swap,fee. MT5 HTML: a dated Deals table. '
             'One strategy per file; amounts must be in the stated currency. Starting capital normalizes results '
             'for portfolio weights.</p>'
             '<form method="post" action="/backtests/import" enctype="multipart/form-data">'
             f'<input type="hidden" name="csrf_token" value="{_escape(csrf_token)}">'
             '<label>Strategy name<input name="strategy" maxlength="120" required></label>'
             '<label>Currency<input name="currency" value="USD" maxlength="3" required></label>'
             '<label>Starting capital<input name="starting_capital" type="number" min="0.01" step="any" required></label>'
             '<label>Backtest file<input name="file" type="file" accept=".csv,.xlsx,.htm,.html" required></label>'
             '<button type="submit">Import backtest</button></form></section>')
    if not rows:
        return _page("Backtest Lab", body + '<p class="note">No backtests imported yet.</p>')
    currencies = sorted({row[3] for row in rows})
    body += '<section class="panel"><h2>Choose strategy runs</h2><form method="get" action="/backtests" class="portfolio-filter">'
    body += '<div class="filter-row"><label>Currency<select name="currency">' + ''.join(
        f'<option value="{_escape(value)}"{ " selected" if value == currency else ""}>{_escape(value)}</option>'
        for value in currencies) + '</select></label><button type="submit">Build portfolio</button></div>'
    body += '<div class="selection-grid">' + ''.join(
        f'<div class="selection-group"><label class="selection-choice"><input type="checkbox" name="run" value="{row[0]}"'
        f'{" checked" if row[0] in chosen_ids else ""}><span><strong>{_escape(row[1])}</strong>'
        f'<small>{_escape(row[2])} · {row[5]} to {row[6]} · {number(Decimal(row[4]), " " + row[3])} capital</small></span></label>'
        f'<label>Allocation weight<input type="number" name="weight_{row[0]}" min="0.0001" max="1000000" step="any"'
        f' value="{_escape(params.get("weight_" + row[0], ["1"])[0][:32])}"></label></div>'
        for row in rows if row[3] == currency) + '</div></form>'
    body += '<p class="muted">Select up to 10 runs in one currency. Weights are relative shares and default to equal allocation. Ranked combinations use equal weights for a fair comparison.</p></section>'
    if len(chosen_ids) > MAX_SELECTED:
        return _page("Backtest Lab", body + '<p class="note">Select no more than 10 runs.</p>')
    if not chosen_ids:
        return _page("Backtest Lab", body + '<p class="note">Select at least one run.</p>')
    try:
        weights = {key: _decimal(params.get("weight_" + key, ["1"])[0]) for key in chosen_ids}
        if any(value < Decimal("0.0001") or value > 1_000_000 for value in weights.values()):
            raise ValueError("Allocation weights must be between 0.0001 and 1,000,000")
    except ValueError as exc:
        return _page("Backtest Lab", body + f'<p class="note">{_escape(exc)}</p>')
    total_weight = sum(weights.values(), ZERO)
    shares = {key: float(value / total_weight) for key, value in weights.items()}
    selected = [runs[key] for key in chosen_ids]
    portfolio_capital = sum((Decimal(row[4]) for row in selected), ZERO)
    start = max(row[5] for row in selected)
    end = min(row[6] for row in selected)
    if start > end:
        return _page("Backtest Lab", body + '<p class="note">These runs have no overlapping dates.</p>')
    data = {key: {} for key in chosen_ids}
    placeholders = ",".join("?" for _ in chosen_ids)
    for run_id, day, amount in connection.execute(
            f"SELECT run_id,day,net_pnl FROM backtest_days WHERE run_id IN ({placeholders}) AND day>=? AND day<=?",
            (*chosen_ids, start, end)):
        data[run_id][day] = float(Decimal(amount) / Decimal(runs[run_id][4]))
    days = list(_days(start, end))
    if len(days) > 20000:
        return _page("Backtest Lab", body + '<p class="note">Shared period exceeds 20,000 days.</p>')
    series = {key: [data[key].get(day, 0.0) for day in days] for key in chosen_ids}
    combined = [sum(series[key][i] * shares[key] for key in chosen_ids) for i in range(len(days))]
    metrics = _metrics(combined)
    running = ZERO
    curve = [(start, ZERO)]
    for day, value in zip(days, combined):
        running += Decimal(str(value)) * 100
        curve.append((day, running))
    money_curve = [(day, value * portfolio_capital / 100) for day, value in curve]
    return_amount = money_curve[-1][1]
    drawdown_amount = Decimal(str(metrics["drawdown_from_initial"])) * portfolio_capital
    body += (f'<p class="note"><strong>Common test period: {start} to {end}</strong> · {len(chosen_ids)} run(s) · '
             f'{len(days)} calendar days. Zero PnL is assumed between each run’s first and last dated row. '
             'Selected allocation: ' + ', '.join(f'{_escape(runs[key][1])} {shares[key] * 100:.1f}%'
                                              for key in chosen_ids) + '.</p>')
    body += '<div class="cards portfolio-cards">' + ''.join(
        f'<div class="card"><span class="muted">{label}</span><b>{value}</b></div>'
        for label, value in (("Fixed-allocation return", f'{metrics["return"]:.2f}% / {number(return_amount, " " + currency)}'),
                             ("Max drawdown", f'{metrics["drawdown"]:.2f}% / {number(drawdown_amount, " " + currency)}'),
                             ("Active days", str(metrics["active_days"])))) + '</div>'
    body += '<section class="panel"><h2>Combined cumulative return</h2>'
    body += line_chart(money_curve, "Fixed-allocation cumulative PnL", currency)
    body += ('<details><summary>Show cumulative return in %</summary>'
             + line_chart(curve, "Fixed-allocation cumulative return", "%") + '</details></section>')
    body += ('<p class="muted">Daily portfolio return = weighted sum of each run’s daily net PnL / its original starting capital. '
             'Weights stay fixed; original trade sizes are replayed without compounding, fees beyond those in the import, '
             'or execution overlap modeling. Amounts use the sum of selected starting capitals as model portfolio capital. '
             'Maximum drawdown amount is the peak-to-trough loss at the largest percentage drawdown.</p>')
    body += '<h2>Selected run comparison</h2><div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Return</th><th>Max drawdown</th><th>Active days</th></tr></thead><tbody>'
    for key in chosen_ids:
        item = _metrics(series[key])
        body += (f'<tr><td>{_escape(runs[key][1])}</td><td>{item["return"]:.2f}%</td>'
                 f'<td>{item["drawdown"]:.2f}%</td><td>{item["active_days"]}</td></tr>')
    body += '</tbody></table></div>'
    if len(chosen_ids) >= 2:
        body += '<h2>Daily return correlation</h2><div class="table-wrap"><table><thead><tr><th>Strategy pair</th><th>Correlation</th><th>Active overlap days</th></tr></thead><tbody>'
        for left, right in combinations(chosen_ids, 2):
            paired = [(a, b) for a, b in zip(series[left], series[right]) if a or b]
            corr = _pearson([a for a, _ in paired], [b for _, b in paired])
            body += (f'<tr><td>{_escape(runs[left][1])} / {_escape(runs[right][1])}</td>'
                     f'<td>{corr:.2f}</td>' if corr is not None else
                     f'<tr><td>{_escape(runs[left][1])} / {_escape(runs[right][1])}</td><td>—</td>')
            body += f'<td>{len(paired)}</td></tr>'
        body += '</tbody></table></div><p class="muted">Pearson correlation uses days when either run has a nonzero result; fewer than 20 active days or a constant series shows —.</p>'
        combos = []
        for size in range(2, len(chosen_ids) + 1):
            for keys in combinations(chosen_ids, size):
                values = [sum(series[key][i] for key in keys) / size for i in range(len(days))]
                score = _metrics(values)
                combos.append((keys, score))
        combos.sort(key=lambda item: (item[1]["return_drawdown"] if item[1]["return_drawdown"] is not None else
                                     (float("inf") if item[1]["return"] > 0 else float("-inf")),
                                     item[1]["return"]), reverse=True)
        body += '<h2>Best combinations</h2><p class="muted">Ranked by total return / maximum drawdown over the common period. A profitable combination with zero measured drawdown ranks first; compare its active days and individual runs before relying on it.</p>'
        body += '<div class="table-wrap"><table><thead><tr><th>Runs · equal weights</th><th>Return</th><th>Max drawdown</th><th>Return / drawdown</th><th>Active days</th></tr></thead><tbody>'
        for keys, score in combos[:20]:
            ratio = score["return_drawdown"]
            query = "&amp;".join([f"currency={currency}"] + [f"run={key}" for key in keys])
            body += (f'<tr><td><a href="/backtests?{query}">{" + ".join(_escape(runs[key][1]) for key in keys)}</a></td>'
                     f'<td>{score["return"]:.2f}%</td><td>{score["drawdown"]:.2f}%</td>'
                     f'<td>{ratio:.2f}</td>' if ratio is not None else
                     f'<tr><td><a href="/backtests?{query}">{" + ".join(_escape(runs[key][1]) for key in keys)}</a></td>'
                     f'<td>{score["return"]:.2f}%</td><td>{score["drawdown"]:.2f}%</td><td>—</td>')
            body += f'<td>{score["active_days"]}</td></tr>'
        body += '</tbody></table></div>'
    body += '<h2>Imported runs</h2><div class="table-wrap"><table><thead><tr><th>Strategy</th><th>File</th><th>Period</th><th>Rows</th><th>Capital</th></tr></thead><tbody>'
    body += ''.join(f'<tr><td>{_escape(row[1])}</td><td>{_escape(row[2])}</td><td>{row[5]} to {row[6]}</td>'
                    f'<td>{row[7]}</td><td>{number(Decimal(row[4]), " " + row[3])}</td></tr>' for row in rows)
    body += '</tbody></table></div>'
    return _page("Backtest Lab", body)
