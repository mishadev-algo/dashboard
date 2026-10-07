from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from server.performance import PerformanceSource, combined_performance
from server.portfolio import render_portfolio
from server.portfolio_selection import account_key, strategy_key, shared_period, strategy_options
from tests import test_performance as fixtures
from tests.test_performance import deal


class CombinedPerformanceTest(unittest.TestCase):
    def test_account_capital_is_counted_once_for_multiple_selected_strategies(self):
        ledger = [deal(1, 1, "1000", "cash"), deal(2, 2, "100", strategy="C15"),
                  deal(3, 3, "110", strategy="C19")]
        source = PerformanceSource(ledger, "1210", frozenset(("C15", "C19")))
        result = combined_performance({("broker", 1): source}, "2026-09-01", "2026-09-30")
        self.assertEqual(result.total_return, Decimal("21"))
        self.assertEqual(result.pnl, 210)

    def test_other_strategy_updates_actual_capital_but_not_selected_result(self):
        source = PerformanceSource([deal(1, 1, "1000", "cash"), deal(2, 2, "1000", strategy="Other"),
                                    deal(3, 3, "200", strategy="C15")], "2200", frozenset(("C15",)))
        result = combined_performance({("broker", 1): source}, "2026-09-01", "2026-09-30")
        self.assertEqual(result.total_return, 10)
        self.assertEqual(result.pnl, 200)

    def test_combined_account_capital_is_weighted_not_sum_or_average_of_returns(self):
        sources = {("b", 1): PerformanceSource([deal(1, 2, "100")], "1100"),
                   ("b", 2): PerformanceSource([deal(1, 2, "900")], "9900")}
        result = combined_performance(sources, "2026-09-01", "2026-09-30")
        self.assertEqual(result.total_return, 10)
        self.assertEqual(result.pnl, 1000)

    def test_fixed_capital_ignores_transfers_other_strategies_and_missing_balances(self):
        source = PerformanceSource([deal(1, 1, "90000", "cash"), deal(2, 2, "100", strategy="C15"),
                                    deal(3, 3, "-85000", "cash"), deal(4, 4, "-50", strategy="C15"),
                                    deal(5, 5, "5000", strategy="Other")], None, frozenset(("C15",)))
        result = combined_performance({("b", 1): source}, "2026-09-01", "2026-09-30", Decimal(1000))
        self.assertEqual(result.total_return, 5)
        self.assertEqual(result.pnl, 50)
        self.assertAlmostEqual(result.max_drawdown, Decimal(50) / 1100 * 100)
        self.assertEqual(result.points[0][1], 0)

    def test_account_basis_neutralizes_deposit_and_withdrawal(self):
        source = PerformanceSource([deal(1, 1, "1000", "cash"), deal(2, 2, "100"),
                                    deal(3, 3, "1100", "cash"), deal(4, 4, "220"),
                                    deal(5, 5, "-1210", "cash"), deal(6, 6, "-121")], "1089")
        result = combined_performance({("b", 1): source}, "2026-09-01", "2026-09-30")
        self.assertEqual(result.total_return, Decimal("8.9"))
        self.assertEqual(result.max_drawdown, 10)

    def test_missing_or_ambiguous_capital_keeps_pnl_but_suppresses_percent(self):
        source = PerformanceSource([deal(1, 1, "1000", "cash"), deal(2, 1, "100")], "1100")
        result = combined_performance({("b", 1): source}, "2026-09-01", "2026-09-30")
        self.assertIsNone(result.total_return)
        self.assertEqual(result.pnl, 100)
        result = combined_performance({("b", 1): replace(source, balance=None)}, "2026-09-01", "2026-09-30")
        self.assertIsNone(result.total_return)

    def test_shared_period_intersection_and_manual_narrowing(self):
        spans = [("2026-07-01", "2026-09-30"), ("2026-08-01", "2026-09-10")]
        period = shared_period(spans, "2026-06-01", "2026-10-01")
        self.assertEqual((period.start, period.end), ("2026-08-01", "2026-09-10"))
        period = shared_period(spans, "2026-08-15", "2026-08-31")
        self.assertEqual((period.start, period.end), ("2026-08-15", "2026-08-31"))
        self.assertTrue(shared_period(spans, "2026-10-01").reason)
        self.assertTrue(shared_period([(None, "2026-09-10")]).reason)
        self.assertTrue(shared_period([]).reason)

    def test_quiet_days_do_not_truncate_strategy_coverage_at_last_trade(self):
        options = strategy_options("broker", 1, [deal(1, 1, "10")], [], "2026-09-30")
        self.assertEqual(options[0].last_day, "2026-09-30")


class PortfolioSelectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PortfolioViewTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connection = self.fixture.connection
        self.now = self.fixture.now

    def page(self, **params):
        return render_portfolio(self.connection, {"currency": ["USD"], **params}, self.now)

    def analysis(self, page):
        return page.split('</form>', 1)[1]

    def setup_july_august(self):
        with self.connection:
            self.connection.execute("DELETE FROM deals")
            self.connection.execute("DELETE FROM accounts")
        def dated(ticket, month, day, net, strategy):
            d = deal(ticket, day, net, strategy=strategy)
            stamp = datetime(2026, month, day, 12, tzinfo=timezone.utc)
            return replace(d, time=stamp, day=stamp.date().isoformat())
        self.fixture.account(1, "USD", "1710", [dated(1, 7, 1, "10", "C15"),
            dated(2, 8, 1, "100", "C15"), dated(3, 8, 1, "500", "C19"), dated(4, 8, 6, "100", "C15")])
        self.fixture.account(2, "USD", "2100", [dated(1, 8, 5, "200", "C19"), dated(2, 8, 5, "900", "C15")])
        with self.connection:
            self.connection.execute("UPDATE accounts SET latest_snapshot_utc='2026-09-10T12:00:00+00:00' WHERE login=2")

    def test_all_default_and_account_subset(self):
        self.assertIn("<b>50.00 USD</b>", self.page())
        page = self.page(account_mode=["selected"], account=[account_key("Broker", 1)])
        analysis = self.analysis(page)
        self.assertIn("<b>100.00 USD</b>", analysis)
        self.assertNotIn("login=2", analysis)
        self.assertIn('name="account" value="' + account_key("Broker", 1) + '" checked', page)
        self.assertNotIn('name="account" value="' + account_key("Broker", 2) + '" checked', page)

    def test_same_strategy_on_two_accounts_can_be_selected_independently(self):
        page = self.page(strategy_mode=["selected"], strategy=[strategy_key("Broker", 2, "Alpha")])
        analysis = self.analysis(page)
        self.assertIn("<b>-50.00 USD</b>", analysis)
        self.assertNotIn("login=1", analysis)
        self.assertIn("login=2", analysis)
        self.assertIn("1 participating account(s)", analysis)

    def test_july_c15_and_august_c19_use_only_common_period_and_exact_pairs(self):
        self.setup_july_august()
        page = self.page(strategy_mode=["selected"], strategy=[strategy_key("Broker", 1, "C15"),
                         strategy_key("Broker", 2, "C19")], basis=["fixed"], capital=["1000"],
                         **{"from": ["2026-07-01"], "to": ["2026-10-01"]})
        analysis = self.analysis(page)
        self.assertIn("Shared analysis period (UTC): 2026-08-05 – 2026-09-10", analysis)
        self.assertIn("<b>300.00 USD</b>", analysis)
        self.assertIn("<strong>30.00%</strong>", analysis)
        self.assertNotIn("<td>900.00</td>", analysis)
        self.assertNotIn("<td>500.00</td>", analysis)

    def test_no_overlap_and_empty_or_stale_selection_do_not_fall_back_to_all(self):
        for params in ({"account_mode": ["selected"]}, {"strategy_mode": ["selected"]},
                       {"account": ["not-a-known-account"]}, {"strategy": [strategy_key("Broker", 3, "Euro only")]}):
            with self.subTest(params=params):
                analysis = self.analysis(self.page(**params))
                self.assertIn("Select at least one", analysis)
                self.assertNotIn('class="performance-chart"', analysis)
        self.setup_july_august()
        page = self.page(strategy=[strategy_key("Broker", 1, "C15"), strategy_key("Broker", 2, "C19")],
                         **{"to": ["2026-07-31"]})
        self.assertIn("No shared history", self.analysis(page))
        self.assertNotIn('class="performance-chart"', page)

    def test_pair_from_excluded_account_is_not_reintroduced(self):
        page = self.page(account=[account_key("Broker", 1)], strategy=[strategy_key("Broker", 2, "Alpha")])
        self.assertIn("Select at least one", self.analysis(page))

    def test_full_shared_history_link_preserves_selection_and_capital(self):
        key = strategy_key("Broker", 1, "Alpha")
        page = self.page(strategy=[key], basis=["fixed"], capital=["2000"], **{"from": ["2026-09-03"]})
        href = re.search(r'href="([^"]+)">Full shared history</a>', page).group(1)
        query = parse_qs(urlsplit(unescape(href)).query)
        self.assertEqual(query["strategy"], [key])
        self.assertEqual(query["capital"], ["2000"])
        self.assertEqual(query["basis"], ["fixed"])
        self.assertNotIn("from", query)

    def test_fixed_capital_validation_and_missing_balance_fallback(self):
        for capital in ("", "0", "NaN", "Infinity", "-1", "1e999999", "1e-999999", "junk"):
            with self.subTest(capital=capital):
                page = self.page(basis=["fixed"], capital=[capital])
                self.assertIn("Enter a fixed starting capital", page)
                self.assertIn("<b>50.00 USD</b>", page)
        with self.connection:
            self.connection.execute("UPDATE accounts SET balance=NULL")
        page = self.page(basis=["fixed"], capital=["1000"])
        self.assertIn("<strong>5.00%</strong>", page)

    def test_open_positions_are_filtered_by_exact_account_strategy_pair(self):
        with self.connection:
            for login, name, profit in ((1, "Alpha", "10"), (1, "Other", "900"), (2, "Alpha", "800")):
                self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        ("Broker", login, int(profit), "XAUUSD", 0, 42, name, .1, 1, 1, profit, "0", ""))
        page = self.page(strategy=[strategy_key("Broker", 1, "Alpha")])
        analysis = self.analysis(page)
        self.assertIn("<b>10.00 USD</b>", analysis)
        self.assertNotIn("900.00", analysis)
        self.assertNotIn("800.00", analysis)
        self.assertNotIn("Other", analysis.split('<h2>Strategy contribution and quality</h2>')[1])

    def test_open_only_selected_strategy_has_no_invented_history(self):
        with self.connection:
            self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    ("Broker", 1, 99, "XAUUSD", 0, 42, "New", .1, 1, 1, "10", "0", ""))
        page = self.page(strategy=[strategy_key("Broker", 1, "New")])
        self.assertIn("no recorded deals yet", self.analysis(page))
        self.assertNotIn('class="performance-chart"', page)

    def test_selector_labels_escape_untrusted_strategy_and_account_names(self):
        hostile = 'C15"><img src=x onerror="alert(1)">'
        with self.connection:
            self.connection.execute("UPDATE deals SET strategy=? WHERE kind='trade'", (hostile,))
        page = self.page(strategy=[strategy_key("Broker", 1, hostile)])
        self.assertNotIn('<img', page)
        self.assertIn('&lt;img', page)


if __name__ == "__main__":
    unittest.main()
