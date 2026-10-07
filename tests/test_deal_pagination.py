from html import unescape
import re
import unittest
from urllib.parse import parse_qs, urlsplit

from server.strategy import render_strategy
from server.web import render_accounts
from tests import test_performance as fixtures


class DealPaginationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PortfolioViewTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.connection = self.fixture.connection

    def add_deals(self, count):
        with self.connection:
            for ticket in range(10, 10 + count):
                self.connection.execute(
                    "INSERT INTO deals SELECT server,login,?,time_utc,day_local,type,kind,entry,position_id,symbol,magic,strategy,comment,profit,commission,swap,fee,volume FROM deals WHERE login=1 AND ticket=2", (ticket,))

    def section(self, page):
        return re.search(r'<section id="deals".*?</section>', page, re.S).group()

    def next_params(self, section):
        url = unescape(re.search(r'class="show-more" href="([^"]+)"', section)[1])
        self.assertEqual(urlsplit(url).fragment, "deals")
        return parse_qs(urlsplit(url).query)

    def test_account_expands_15_30_then_all_and_keeps_period_and_account(self):
        self.add_deals(37)
        params = {"server": ["Broker"], "login": ["1"], "from": ["2026-09-02"], "to": ["2026-09-02"]}
        for expected in (15, 30, 38):
            section = self.section(render_accounts(self.connection, params, self.fixture.now))
            self.assertEqual(section.count('<tr>'), expected + 1)
            self.assertIn(f'Showing {expected} of 38 deals', section)
            if expected < 38:
                params = self.next_params(section)
                self.assertEqual(params["login"], ["1"])
                self.assertEqual(params["from"], ["2026-09-02"])
                self.assertEqual(params["to"], ["2026-09-02"])
            else:
                self.assertNotIn('Show more', section)

    def test_strategy_expands_without_duplicates_and_preserves_all_filters(self):
        self.add_deals(103)
        params = {"server": ["Broker"], "login": ["1"], "strategy": ["Alpha"], "symbol": ["EURUSD"],
                  "from": ["2026-09-02"], "to": ["2026-09-02"], "zone": ["UTC"], "basis": ["fixed"], "capital": ["2000"]}
        previous = set()
        for expected in (50, 100, 104):
            section = self.section(render_strategy(self.connection, params, self.fixture.now))
            tickets = re.findall(r'<tr id="deal-(\d+)"', section)
            self.assertEqual(len(tickets), expected)
            self.assertEqual(len(set(tickets)), expected)
            self.assertTrue(previous <= set(tickets))
            previous = set(tickets)
            if expected < 104:
                following = self.next_params(section)
                for key in ("server", "login", "strategy", "symbol", "from", "to", "zone", "basis", "capital"):
                    self.assertEqual(following[key], params[key])
                params = following
            else:
                self.assertNotIn('Show more', section)

    def test_limit_is_validated_and_empty_filtered_list_has_no_button(self):
        self.add_deals(20)
        for value in ("-1", "0", "nonsense", "999999999999999999999"):
            section = self.section(render_accounts(self.connection, {"server": ["Broker"], "login": ["1"], "deals_limit": [value]}, self.fixture.now))
            self.assertIn('of 21 deals', section)
            self.assertLessEqual(section.count('<tr>'), 22)
        section = self.section(render_accounts(self.connection, {"from": ["2026-09-03"], "to": ["2026-09-04"]}, self.fixture.now))
        self.assertIn('Showing 0 of 0 deals', section)
        self.assertNotIn('Show more', section)


if __name__ == '__main__':
    unittest.main()
