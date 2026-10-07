from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from html import unescape
import re
from types import SimpleNamespace
import unittest
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from server.activity_web import activity_panels
from server.analytics_web import line_chart
from server.ingest import IngestHandler
from server.links import strategy_url
from server.performance import PerformanceSource, combined_performance
from server.portfolio import render_portfolio
from server.portfolio_selection import strategy_key
from server.strategy import render_strategy
from server.web import render_accounts
from tests import test_performance as fixtures


def links(page):
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', page)]


class StrategyViewTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PortfolioViewTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connection = self.fixture.connection
        self.now = self.fixture.now

    def page(self, **params):
        return render_strategy(self.connection, {"server": ["Broker"], "login": ["1"], "strategy": ["Alpha"], **params}, self.now)

    def test_account_strategy_and_deal_links_preserve_identity_and_period(self):
        page = render_accounts(self.connection, {"server": ["Broker"], "login": ["1"], "from": ["2026-09-02"], "to": ["2026-09-03"]}, self.now)
        urls = [url for url in links(page) if url.startswith('/strategy?')]
        self.assertGreaterEqual(len(urls), 2)
        for url in urls:
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(query["login"], ["1"])
            self.assertEqual(query["strategy"], ["Alpha"])
            self.assertEqual(query["from"], ["2026-09-02"])
            self.assertEqual(query["to"], ["2026-09-03"])
        detail = self.page()
        self.assertIn("Strategy realized return", detail)
        self.assertIn("<b>100.00 USD</b>", detail)
        self.assertNotIn("<b>-50.00 USD</b>", detail)

    def test_portfolio_strategy_links_and_return_comparison_keep_scope(self):
        page = render_portfolio(self.connection, {"currency": ["USD"], "strategy": [strategy_key("Broker", 1, "Alpha")],
                                                 "basis": ["fixed"], "capital": ["2000"]}, self.now)
        query = parse_qs(urlsplit(next(u for u in links(page) if u.startswith('/strategy?'))).query)
        self.assertEqual(query["zone"], ["UTC"])
        self.assertEqual(query["basis"], ["fixed"])
        self.assertEqual(query["capital"], ["2000"])
        detail = self.page(basis=["fixed"], capital=["2000"])
        self.assertIn("<b>5.00%</b>", detail)
        comparisons = [parse_qs(urlsplit(u).query) for u in links(detail) if u.startswith('/portfolio?')]
        self.assertEqual(comparisons[-1]["strategy"], [strategy_key("Broker", 1, "Alpha"), strategy_key("Broker", 2, "Alpha")])
        self.assertEqual(comparisons[-1]["account_mode"], ["all"])

    def test_same_strategy_name_does_not_leak_other_account_deals(self):
        page = self.page()
        history = page.split('<h2>Strategy deals</h2>', 1)[1].split('</section>', 1)[0]
        self.assertIn("100.00", history)
        self.assertNotIn("-50.00", history)
        peers = page.split('<h2>Same strategy on other accounts</h2>', 1)[1]
        self.assertIn("login=2", peers)
        self.assertNotIn("login=3", peers)

    def test_invalid_strategy_account_and_symbol_have_explicit_empty_states(self):
        for params in ({"login": ["invalid"]}, {"login": [str(2**100)]}, {"server": ["Unknown"]}, {"strategy": ["Missing"]}):
            page = self.page(**params)
            self.assertNotIn("Strategy realized return", page)
        page = self.page(symbol=["Unknown"])
        self.assertIn("no recorded activity for this strategy", page)
        self.assertNotIn("Strategy realized return", page)

    def test_month_day_and_symbol_links_preserve_capital_and_filters(self):
        page = self.page(basis=["fixed"], capital=["2000"], symbol=["EURUSD"], zone=["UTC"])
        day_links = [parse_qs(urlsplit(url).query) for url in links(page) if url.startswith('/strategy?')
                     and 'from=2026-09-02' in url and 'to=2026-09-02' in url]
        self.assertTrue(day_links)
        for query in day_links:
            self.assertEqual(query["capital"], ["2000"])
            self.assertEqual(query["symbol"], ["EURUSD"])
            self.assertEqual(query["zone"], ["UTC"])
        self.assertIn("Monthly PnL", page)
        self.assertIn("Day-of-week results", page)

    def test_date_and_symbol_filter_only_selected_results_not_account_capital(self):
        with self.connection:
            self.connection.execute("INSERT INTO deals SELECT server,login,9,time_utc,day_local,type,kind,entry,position_id,'XAUUSD',magic,strategy,comment,'200',commission,swap,fee,volume FROM deals WHERE login=1 AND ticket=2")
            self.connection.execute("UPDATE accounts SET balance='1300' WHERE login=1")
        page = self.page(symbol=["EURUSD"], basis=["fixed"], capital=["1000"])
        self.assertIn("<b>100.00 USD</b>", page)
        self.assertIn("<b>10.00%</b>", page)
        history = page.split('<h2>Strategy deals</h2>', 1)[1].split('</section>', 1)[0]
        self.assertNotIn("XAUUSD", history)
        later = self.page(**{"from": ["2026-09-03"], "to": ["2026-09-04"]})
        self.assertIn("<b>0.00 USD</b>", later)
        self.assertIn("No recorded deals in this period", later)

    def test_unallocated_dividends_do_not_enter_strategy_return(self):
        with self.connection:
            self.connection.execute("INSERT INTO deals SELECT server,login,9,time_utc,day_local,15,'other',0,position_id,symbol,magic,strategy,comment,'1000',commission,swap,fee,volume FROM deals WHERE login=1 AND ticket=2")
            self.connection.execute("UPDATE accounts SET balance='2100' WHERE login=1")
        page = self.page(basis=["fixed"], capital=["1000"])
        self.assertIn("<b>100.00 USD</b>", page)
        self.assertNotIn("<b>1,100.00 USD</b>", page)

    def test_open_only_strategy_keeps_positions_without_invented_return(self):
        with self.connection:
            self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    ("Broker", 1, 99, "XAUUSD", 0, 77, "New", .1, 1, 1, "12", "-2", ""))
        page = self.page(strategy=["New"])
        self.assertIn("no recorded deals yet", page)
        self.assertIn("10.00 USD", page)
        self.assertNotIn("Strategy realized return", page)

    def test_local_day_and_utc_links_have_explicit_date_semantics(self):
        with self.connection:
            self.connection.execute("UPDATE deals SET time_utc='2026-09-01T23:30:00+00:00',day_local='2026-09-02' WHERE login=1 AND kind='trade'")
        local = self.page(**{"from": ["2026-09-02"], "to": ["2026-09-02"]})
        utc = self.page(zone=["UTC"], **{"from": ["2026-09-02"], "to": ["2026-09-02"]})
        self.assertIn("<b>100.00 USD</b>", local)
        self.assertIn("<b>0.00 USD</b>", utc)

    def test_hostile_names_are_escaped_in_titles_and_links(self):
        hostile = '<script>alert("x")</script>&C15'
        with self.connection:
            self.connection.execute("UPDATE deals SET strategy=? WHERE login=1 AND kind='trade'", (hostile,))
        page = self.page(strategy=[hostile])
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        query = parse_qs(urlsplit(strategy_url("Broker & Co", 1, hostile)).query)
        self.assertEqual(query["strategy"], [hostile])

    def test_missing_fixed_capital_does_not_silently_switch_basis(self):
        page = self.page(basis=["fixed"], capital=["NaN"])
        self.assertIn("Enter a fixed starting capital", page)
        self.assertIn("100.00 USD", page)
        self.assertNotIn('aria-label="Strategy realized return"', page)

    def test_strategy_http_route(self):
        handler = IngestHandler.__new__(IngestHandler)
        handler.server = SimpleNamespace(db_path=self.fixture.path, postgres_dsn=None)
        handler.path = strategy_url("Broker", 1, "Alpha")
        responses = []
        handler._respond_html = lambda status, page: responses.append((status, page))
        handler.do_GET()
        self.assertEqual(responses[0][0], 200)
        self.assertIn("Strategy realized return", responses[0][1])


class ActivityAndChartTest(unittest.TestCase):
    def test_chart_keeps_intraday_extremes_and_has_accessible_tooltips(self):
        points = [("2026-09-01", Decimal(0)), ("2026-09-02", Decimal(20)),
                  ("2026-09-02", Decimal(-10)), ("2026-09-03", Decimal(5))]
        markup = line_chart(points, 'Return <test>', '%')
        svg = re.search(r'<svg.*?</svg>', markup, re.S).group()
        root = ElementTree.fromstring(svg)
        self.assertEqual(len(root.find('polyline').attrib['points'].split()), 4)
        self.assertTrue(root.findall('.//linearGradient'))
        self.assertIn('tabindex="0"', markup)
        self.assertIn('Return &lt;test&gt;', markup)
        self.assertIn('-10.00 %', markup)
        self.assertEqual(markup, line_chart(points, 'Return <test>', '%'))

    def test_flat_single_day_chart_is_valid_and_empty_chart_is_explicit(self):
        markup = line_chart([("2026-09-01", Decimal(0))], 'Quiet', '%')
        svg = re.search(r'<svg.*?</svg>', markup, re.S).group()
        ElementTree.fromstring(svg)
        self.assertNotIn('NaN', markup)
        self.assertIn('No recorded values', line_chart([], 'Empty', '%'))

    def test_calendar_includes_entry_costs_and_preserves_exact_day_links(self):
        deals = [fixtures.deal(1, 2, '-2', entry=0, costs='-2'), fixtures.deal(2, 2, '12'), fixtures.deal(3, 3, '-4')]
        page = activity_panels(deals, '2026-09-01', '2026-09-30', 'USD', lambda a, b: '/strategy?from=' + a + '&to=' + b)
        self.assertIn('6.00 USD', page)
        self.assertIn('2 exits', page)
        self.assertIn('10.00 USD', page)
        self.assertIn('from=2026-09-02&amp;to=2026-09-02', page)

    def test_symbol_selection_keeps_other_results_in_capital_reconstruction(self):
        deals = [fixtures.deal(1, 1, '1000', 'cash'),
                 replace(fixtures.deal(2, 2, '1000'), symbol='XAUUSD'), fixtures.deal(3, 3, '200')]
        source = PerformanceSource(deals, '2200', frozenset(('Alpha',)), frozenset(('EURUSD',)))
        result = combined_performance({('Broker', 1): source}, '2026-09-01', '2026-09-30')
        self.assertEqual(result.pnl, 200)
        self.assertEqual(result.total_return, 10)


if __name__ == '__main__':
    unittest.main()
