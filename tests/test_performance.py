from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from server.ingest import IngestHandler, open_database
from server.analytics_web import performance_panel, pnl_breakdown
from server.performance import Deal, PerformanceSource, account_performance, combined_performance, load_deals, strategy_stats
from server.portfolio import _correlations, render_portfolio
from server.web import render_accounts


def deal(ticket, day, amount, kind="trade", *, strategy="Alpha", entry=1, costs="0", hour=12, kind_id=None):
    return Deal(datetime(2026, 9, day, hour, tzinfo=timezone.utc), f"2026-09-{day:02}", ticket,
                kind, kind_id if kind_id is not None else (2 if kind == "cash" else 0),
                entry, strategy, "EURUSD" if kind == "trade" else "", Decimal(amount), Decimal(costs))


class PerformanceTest(unittest.TestCase):
    def test_positive_money_profit_after_withdrawal_is_not_negative_percentage(self):
        deals = [deal(1, 1, "1000", "cash"), deal(2, 2, "200"),
                 deal(3, 3, "-1100", "cash"), deal(4, 4, "-40")]
        account = account_performance(deals, "60", "2026-09-01", "2026-09-04")
        portfolio = combined_performance({("Broker", 1): PerformanceSource(deals, "60")},
                                         "2026-09-01", "2026-09-04")
        for result in (account, portfolio):
            self.assertEqual(result.pnl, 160)
            self.assertEqual(result.pnl_points[-1][1], 160)
            self.assertEqual(result.total_return, -28)
        page = performance_panel(account, "USD")
        self.assertLess(page.index('160.00 USD'), page.index('<details>'))
        self.assertLess(page.index('Cumulative trading PnL'), page.index('Realized return'))

    def test_profit_breakdown_matches_chart_and_excludes_transfers(self):
        deals = [deal(1, 1, "5000", "cash"), deal(2, 2, "-2", entry=0, costs="-2"),
                 deal(3, 3, "170", costs="-30"), deal(4, 4, "-10", "commission"),
                 deal(5, 5, "2", "income", kind_id=15), deal(6, 6, "999"),
                 deal(7, 5, "300", "cash", kind_id=3)]
        result = account_performance(deals, "6159", "2026-09-01", "2026-09-05")
        self.assertEqual(result.pnl, 160)
        page = pnl_breakdown(deals, "USD", "2026-09-01", "2026-09-05")
        for value in ('200.00', '-32.00', '-10.00', '2.00', '160.00'):
            self.assertIn('>' + value + '</td>', page)
        self.assertNotIn('5,000.00', page)
        self.assertNotIn('999.00', page)

    def test_deposits_withdrawals_and_credit_do_not_create_returns(self):
        deals = [deal(1, 1, "1000", "cash"), deal(2, 2, "1000", "cash"),
                 deal(3, 3, "-500", "cash"), deal(4, 4, "300", "cash", kind_id=3)]
        result = account_performance(deals, "1500", "2026-09-01", "2026-09-04")
        self.assertEqual(result.total_return, 0)
        self.assertEqual(result.max_drawdown, 0)
        self.assertEqual(result.pnl, 0)
        self.assertEqual(result.cash, 1500)

    def test_links_returns_on_capital_at_each_trade(self):
        deals = [deal(1, 1, "1000", "cash"), deal(2, 2, "100"),
                 deal(3, 3, "1100", "cash"), deal(4, 4, "220"),
                 deal(5, 5, "-1210", "cash"), deal(6, 6, "-121")]
        result = account_performance(deals, "1089", "2026-09-01", "2026-09-06")
        self.assertEqual(result.total_return, Decimal("8.9"))
        self.assertEqual(result.max_drawdown, Decimal("10"))
        self.assertEqual(result.pnl, 199)
        self.assertEqual(result.points[0][1], 0)

    def test_period_rebases_using_all_history_including_later_transfers(self):
        deals = [deal(1, 1, "1000", "cash"), deal(2, 2, "100"),
                 deal(3, 3, "1100", "cash"), deal(4, 4, "220"), deal(5, 5, "-500", "cash")]
        result = account_performance(deals, "1920", "2026-09-04", "2026-09-04")
        self.assertEqual(result.total_return, 10)
        self.assertEqual(result.pnl, 220)
        self.assertEqual(result.cash, 0)

    def test_entry_and_standalone_fees_reduce_return(self):
        deals = [deal(1, 1, "-10", entry=0, costs="-10"), deal(2, 2, "-5", "commission"),
                 deal(3, 3, "25", costs="-5")]
        result = account_performance(deals, "1010", "2026-09-01", "2026-09-03")
        self.assertAlmostEqual(result.total_return, Decimal("1"))
        self.assertEqual(result.pnl, 10)
        stats = strategy_stats(deals, "2026-09-01", "2026-09-03")
        self.assertEqual(stats["Alpha"].pnl, 15)
        self.assertEqual(stats["Alpha"].closes, 1)
        self.assertEqual(stats["Alpha"].costs, -15)
        self.assertEqual(stats["unallocated commission"].pnl, -5)

    def test_percentage_is_unavailable_without_balance_or_with_unknown_adjustments(self):
        for balance, deals in ((None, [deal(1, 1, "10")]),
                               ("1010", [deal(1, 1, "10", "other")]),
                               ("10", [deal(1, 1, "10")])):
            with self.subTest(balance=balance, deals=deals):
                result = account_performance(deals, balance, "2026-09-01", "2026-09-03")
                self.assertIsNone(result.total_return)
                self.assertTrue(result.reason)
                self.assertFalse(result.points)

    def test_same_timestamp_flow_and_trade_is_not_ordered_by_ticket(self):
        result = account_performance([deal(1, 1, "1000", "cash"), deal(2, 1, "100")],
                                     "1100", "2026-09-01", "2026-09-01")
        self.assertIsNone(result.total_return)
        self.assertIn("order is unknown", result.reason)
        self.assertEqual(result.pnl, 100)

    def test_quiet_period_has_zero_return_only_with_capital(self):
        self.assertEqual(account_performance([], "100", "2026-09-01", "2026-09-02").total_return, 0)
        self.assertIsNone(account_performance([], "0", "2026-09-01", "2026-09-02").total_return)
        result = account_performance([deal(1, 3, "100", "cash")], "100", "2026-09-01", "2026-09-02")
        self.assertIsNone(result.total_return)

    def test_strategy_exit_statistics_do_not_count_entries_or_cash(self):
        deals = [deal(1, 1, "-2", entry=0, costs="-2"), deal(2, 2, "10"),
                 deal(3, 3, "-5"), deal(4, 4, "0"), deal(5, 5, "1000", "cash")]
        stats = strategy_stats(deals, "2026-09-01", "2026-09-05")["Alpha"]
        self.assertEqual(stats.closes, 3)
        self.assertEqual(stats.profit_factor, 2)
        self.assertEqual(stats.pnl, 3)
        self.assertEqual(stats.drawdown, 5)
        self.assertAlmostEqual(stats.win_rate, Decimal(100) / 3)
        self.assertAlmostEqual(stats.average_close, Decimal(5) / 3)

    def test_correlation_requires_sample_and_handles_zero_variance(self):
        deals = [deal(day, day, str(day), strategy="Alpha") for day in range(1, 23)]
        deals += [deal(day + 30, day, str(-2 * day), strategy="Beta") for day in range(1, 23)]
        stats = strategy_stats(deals, "2026-09-01", "2026-09-30")
        self.assertIn("-1.00", _correlations(stats))
        self.assertIn("enough overlapping", _correlations(strategy_stats(deals, "2026-09-01", "2026-09-10")))
        flat = [deal(day + 60, day, "5", strategy="Flat") for day in range(1, 23)]
        self.assertIn("enough overlapping", _correlations(strategy_stats(flat + deals[:22], "2026-09-01", "2026-09-30")))


class PortfolioViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "central.db"
        self.connection = open_database(self.path)
        self.addCleanup(self.connection.close)
        self.now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        self.account(1, "USD", "1100", [deal(1, 1, "1000", "cash"), deal(2, 2, "100")])
        self.account(2, "USD", "950", [deal(1, 1, "1000", "cash"), deal(2, 2, "-50")])
        self.account(3, "EUR", "7999", [deal(1, 1, "7000", "cash"), deal(2, 2, "999", strategy="Euro only")])

    def account(self, login, currency, balance, deals):
        with self.connection:
            self.connection.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)",
                                    ("Broker", login, currency, "UTC", "host", str(login),
                                     self.now.isoformat(), "2026-09-01", balance))
            for d in deals:
                self.connection.execute(
                    "INSERT INTO deals (server,login,ticket,time_utc,day_local,type,kind,entry,position_id,"
                    "symbol,magic,strategy,comment,profit,commission,swap,fee,volume) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("Broker", login, d.ticket, d.time.isoformat(), d.day, d.type, d.kind, d.entry, d.ticket,
                     d.symbol, 42, d.strategy, "", str(d.net - d.costs), str(d.costs), "0", "0", 0.1))

    def test_currency_isolation_and_strategy_account_links(self):
        page = render_portfolio(self.connection, {"currency": ["USD"]}, self.now)
        self.assertIn("50.00 USD", page)
        self.assertIn("10.00%", page)
        self.assertIn("-5.00%", page)
        self.assertIn("login=1", page)
        self.assertIn("login=2", page)
        self.assertNotIn("Euro only", page)
        self.assertNotIn("login=3", page)
        self.assertIn("2 account(s) have stale snapshots", page)
        self.assertIn("Same strategy across accounts", page)

    def test_date_filters_apply_to_portfolio_and_account_return(self):
        page = render_portfolio(self.connection, {"from": ["2026-09-03"], "to": ["2026-09-04"], "currency": ["USD"]}, self.now)
        self.assertIn("0.00 USD", page)
        self.assertNotIn("<b>50.00 USD</b>", page)
        page = render_accounts(self.connection, {"from": ["2026-09-02"], "to": ["2026-09-02"]}, self.now)
        self.assertIn("Realized return · transfers excluded", page)
        self.assertIn("10.00%", page)

    def test_invalid_period_empty_database_and_html_escaping(self):
        self.assertIn("Start date", render_portfolio(self.connection, {"from": ["2026-09-03"], "to": ["2026-09-01"]}))
        with self.connection:
            self.connection.execute("UPDATE deals SET strategy=?", ('<script>alert("x")</script>',))
        page = render_portfolio(self.connection, {"currency": ["USD"]}, self.now)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        with self.connection:
            self.connection.execute("DELETE FROM accounts")
        self.assertIn("No verified", render_portfolio(self.connection, {}))

    def test_open_only_strategy_and_shared_symbol_remain_visible_outside_period(self):
        with self.connection:
            for login, name, side in ((1, "Open only", 0), (2, "Other open", 1)):
                self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        ("Broker", login, 1, "XAUUSD", side, 99, name, 0.2, 1, 1, "12", "-2", ""))
        page = render_portfolio(self.connection, {"currency": ["USD"], "from": ["2026-09-10"]}, self.now)
        self.assertIn("Open only", page)
        self.assertIn("Other open", page)
        self.assertIn("XAUUSD", page)
        self.assertIn("20.00 USD", page)

    def test_portfolio_http_route(self):
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=self.path, postgres_dsn=None)
        handler.path = "/portfolio?currency=USD"
        replies = []
        handler._respond_html = lambda status, value: replies.append((status, value))
        handler.do_GET()
        self.assertEqual(replies[0][0], 200)
        self.assertIn("Strategy portfolio", replies[0][1])

    def test_recorded_dividends_are_income_without_changing_the_stored_ledger(self):
        self.account(4, "CHF", "1015", [deal(1, 1, "1000", "cash"),
                                        deal(2, 2, "20", "other", kind_id=15),
                                        deal(3, 3, "-5", "other", kind_id=17)])
        deals = load_deals(self.connection, "Broker", 4)
        result = account_performance(deals, "1015", "2026-09-01", "2026-09-30")
        self.assertAlmostEqual(result.total_return, Decimal("1.5"))
        self.assertEqual(result.pnl, 15)
        stats = strategy_stats(deals, "2026-09-01", "2026-09-30")
        self.assertEqual(stats["unallocated income / charges"].pnl, 15)
        self.assertEqual(stats["unallocated income / charges"].costs, -5)
        self.assertEqual(stats["unallocated income / charges"].closes, 0)
        self.assertEqual(self.connection.execute("SELECT kind FROM deals WHERE login=4 AND ticket=2").fetchone()[0], "other")
        page = render_accounts(self.connection, {"server": ["Broker"], "login": ["4"]}, self.now)
        self.assertIn("15.00 CHF", page)
        self.assertIn("1.50%", page)


if __name__ == "__main__":
    unittest.main()
