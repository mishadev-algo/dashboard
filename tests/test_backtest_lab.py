from __future__ import annotations

from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
import re
import tempfile
from threading import Thread
import unittest
from zipfile import ZipFile

from server.backtest_lab import _metrics, import_backtest, parse_backtest, render_backtest_lab
from server.ingest import IngestServer, open_database


class BacktestLabTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.db"
        self.connection = open_database(self.path)
        self.addCleanup(self.connection.close)

    def test_csv_import_normalizes_costs_and_skips_cash(self):
        raw = (b"time,type,profit,commission,swap,fee\n"
               b"2026.01.01 09:00,balance,1000,0,0,0\n"
               b"2026.01.02 09:00,buy,100,-2,-1,-1\n"
               b"2026.01.02 10:00,sell,-20,-1,0,0\n")
        daily, count = parse_backtest(raw, "report.csv")
        self.assertEqual(count, 2)
        self.assertEqual(str(daily["2026-01-02"]), "75")
        run_id = import_backtest(self.connection, strategy="Alpha", currency="USD",
                                 starting_capital="1000", file_name="report.csv", content=raw)
        self.assertEqual(self.connection.execute("SELECT net_pnl FROM backtest_days WHERE run_id=?",
                                                 (run_id,)).fetchone()[0], "75")
        with self.assertRaisesRegex(ValueError, "already been imported"):
            import_backtest(self.connection, strategy="Alpha", currency="USD",
                            starting_capital="1000", file_name="report.csv", content=raw)

    def test_mt5_html_deals_table_and_dates(self):
        raw = (b"<html><table><tr><th>Metric</th><th>Profit</th></tr><tr><td>Total</td><td>999</td></tr></table>"
               b"<table><tr><th>Time</th><th>Type</th><th>Profit</th><th>Commission</th></tr>"
               b"<tr><td>2026.01.01 08:00</td><td>buy</td><td>30</td><td>-2</td></tr>"
               b"<tr><td>2026.01.02 08:00</td><td>sell</td><td>-10</td><td>-1</td></tr></table></html>")
        daily, count = parse_backtest(raw, "mt5.htm")
        self.assertEqual((count, daily["2026-01-01"], daily["2026-01-02"]), (2, 28, -11))

    def test_xlsx_import_reads_excel_dates_and_shared_strings(self):
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
            archive.writestr("xl/styles.xml", '''<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                <cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>''')
            archive.writestr("xl/sharedStrings.xml", '''<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                <si><t>Time</t></si><si><t>Type</t></si><si><t>Profit</t></si>
                <si><t>Commission</t></si><si><t>balance</t></si><si><t>buy</t></si></sst>''')
            archive.writestr("xl/worksheets/sheet1.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Summary</t></is></c></row></sheetData></worksheet>''')
            archive.writestr("xl/worksheets/sheet2.xml", '''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
                <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>
                    <c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c></row>
                <row r="2"><c r="A2" s="1"><v>46023</v></c><c r="B2" t="s"><v>4</v></c>
                    <c r="C2"><v>1000</v></c></row>
                <row r="3"><c r="A3" s="1"><v>46024</v></c><c r="B3" t="s"><v>5</v></c>
                    <c r="C3"><v>30</v></c><c r="D3"><v>-2</v></c></row>
                <row r="4"><c r="A4" t="d"><v>2026-01-02T09:00:00</v></c>
                    <c r="C4"><v>-10</v></c><c r="D4"><v>-1</v></c></row>
                </sheetData></worksheet>''')
        raw = buffer.getvalue()
        daily, count = parse_backtest(raw, "mt5.xlsx")
        self.assertEqual(count, 2)
        self.assertEqual(daily, {"2026-01-02": 17})
        run_id = import_backtest(self.connection, strategy="Excel", currency="USD",
                                 starting_capital="1000", file_name="mt5.xlsx", content=raw)
        self.assertEqual(self.connection.execute("SELECT row_count FROM backtest_runs WHERE run_id=?",
                                                 (run_id,)).fetchone()[0], 2)
        self.assertIn('accept=".csv,.xlsx,.htm,.html"', render_backtest_lab(self.connection, {}))

    def test_portfolio_overlap_ranking_and_escaped_name(self):
        rows = []
        for i in range(1, 31):
            rows.append(f"2026-01-{i:02},10")
        a = ("date,net_pnl\n" + "\n".join(rows)).encode()
        b = ("date,net_pnl\n" + "\n".join(f"2026-01-{i:02},{-5 if i % 2 else 5}" for i in range(1, 31))).encode()
        first = import_backtest(self.connection, strategy='<img src=x onerror="1">', currency="USD",
                                starting_capital="1000", file_name="a.csv", content=a)
        second = import_backtest(self.connection, strategy="Beta", currency="USD",
                                 starting_capital="1000", file_name="b.csv", content=b)
        page = render_backtest_lab(self.connection, {"currency": ["USD"], "run": [first, second]})
        self.assertIn("Best combinations", page)
        self.assertIn("Daily return correlation", page)
        self.assertIn("2026-01-01 to 2026-01-30", page)
        self.assertIn("&lt;img", page)
        self.assertNotIn("<img", page)
        self.assertIn(f"run={first}&amp;run={second}", page)
        self.assertIn("Select at least one run", render_backtest_lab(self.connection, {"currency": ["USD"]}))
        weighted = render_backtest_lab(self.connection, {"currency": ["USD"], "run": [first, second],
                                                        "weight_" + first: ["3"], "weight_" + second: ["1"]})
        self.assertIn("22.50%", weighted)
        self.assertIn("75.0%", weighted)

    def test_drawdown_uses_portfolio_value(self):
        result = _metrics([0.1, -0.11])
        self.assertAlmostEqual(result["drawdown"], 10)
        self.assertAlmostEqual(result["drawdown_from_initial"], 0.11)

    def test_absolute_return_and_drawdown_use_selected_capital(self):
        raw = b"date,net_pnl\n2026-01-01,100\n2026-01-02,-110\n"
        run_id = import_backtest(self.connection, strategy="Swing", currency="USD",
                                 starting_capital="1000", file_name="swing.csv", content=raw)
        page = render_backtest_lab(self.connection, {"currency": ["USD"], "run": [run_id]})
        self.assertIn("-1.00% / -10.00 USD", page)
        self.assertIn("10.00% / 110.00 USD", page)
        self.assertIn("Fixed-allocation cumulative PnL", page)
        self.assertIn("Show cumulative return in %", page)

    def test_upload_requires_page_token(self):
        server = IngestServer(("127.0.0.1", 0), self.path, {})
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = HTTPConnection("127.0.0.1", server.server_address[1])
        client.request("GET", "/backtests")
        response = client.getresponse()
        page = response.read().decode()
        self.assertEqual(response.status, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        boundary = "backtest-boundary"
        def payload(csrf):
            parts = [("csrf_token", csrf), ("strategy", "Alpha"), ("currency", "USD"),
                     ("starting_capital", "1000")]
            body = b"".join((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
                            for name, value in parts)
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n"
                     "Content-Type: text/csv\r\n\r\ndate,net_pnl\n2026-01-01,10\n\r\n"
                     f"--{boundary}--\r\n").encode()
            return body
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        client.request("POST", "/backtests/import", payload("bad"), headers)
        denied = client.getresponse()
        denied.read()
        self.assertEqual(denied.status, 403)
        client.request("POST", "/backtests/import", payload(token), headers)
        accepted = client.getresponse()
        accepted.read()
        self.assertEqual(accepted.status, 303)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0], 1)
        client.close()


if __name__ == "__main__":
    unittest.main()
