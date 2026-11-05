"""Arthaya's printed cash, derivative and net columns must stay distinct."""
import unittest

import pymupdf

from factsheetDataIngestion.arthaya_portfolio import extract_arthaya_portfolio
from factsheetDataIngestion.market_cap import text_table, enrich_market_cap


class ArthayaExtractionTests(unittest.TestCase):
    def setUp(self):
        self.document = pymupdf.open()
        self.page = self.document.new_page(width=595, height=842)

    def tearDown(self):
        self.document.close()

    def row(self, label, y, *values, right=False):
        self.page.insert_text((407 if right else 224, y), label, fontsize=7)
        for x, value in zip((490, 520, 548) if right else (307, 335, 364), values):
            self.page.insert_text((x, y), value, fontsize=7)

    def test_published_subtotals_and_derivative_continuation_are_separate(self):
        self.row('BANKS', 218, '28.07%', '-20.66%', '7.41%')
        self.row('HDFC Bank Ltd.', 232, '5.70%', '-3.76%', '1.93%')
        self.row('METALS & MINERALS TRADING', 260, '0.96%', '-0.96%', '0.00%')
        self.row('Adani Enterprises Ltd.', 274, '0.96%', '-0.96%', '0.00%')
        self.row('Equity Shares', 760, '6.66%', '-4.72%', '1.94%')
        self.page.insert_text((224, 780), 'Optstkdrreddy', fontsize=7)
        self.row('27-Oct-2026pe1200', 218, '0.01%', right=True)
        self.row('Index Futures', 232, '-12.19%', right=True)
        self.row('Nifty 50 Index - Futures', 246, '-5.64%', right=True)
        self.row('Bank Nifty Index - Futures', 260, '-6.55%', right=True)
        self.row('Index Options', 274, '0.40%', right=True)
        self.row('Nifty Put Option', 288, '0.40%', right=True)
        self.row('Treasury Bills', 306, '15.14%', right=True)
        self.row('Sovereign', 316, '15.14%', right=True)
        self.row('91 DAY T-BILL', 330, '11.36%', right=True)
        self.row('364 DAY T-BILL', 342, '3.78%', right=True)
        self.row('Grand Total', 372, '100.00%', right=True)
        data = {}
        holdings, totals, _ = extract_arthaya_portfolio(self.page, data)
        self.assertEqual(data['Sector Allocation (%)'], {'BANKS': 28.07, 'METALS & MINERALS TRADING': 0.96})
        self.assertEqual([h['Instrument'] for h in holdings],
                         ['HDFC Bank Ltd.', 'Adani Enterprises Ltd.', '91 DAY T-BILL', '364 DAY T-BILL'])
        self.assertEqual(holdings[0]['Percent to Net Assets'], 5.7)
        self.assertEqual(holdings[0]['Derivatives Percent to Net Assets'], -3.76)
        self.assertEqual(holdings[0]['Published Net Exposure (%)'], 1.93)
        self.assertEqual(totals['Treasury Bill'], 15.14)
        self.assertEqual(data['Derivative Portfolio Totals (%)'],
                         {'Stock Options': 0.01, 'Index Futures': -12.19, 'Index Options': 0.4})
        self.assertEqual(data['Derivative Portfolio'][0]['Instrument'], 'Optstkdrreddy 27-Oct-2026pe1200')
        self.assertEqual(data['Derivative Portfolio'][1]['Percent to Net Assets'], -5.64)

    def test_market_cap_retains_net_assets_denominator_and_excludes_top_holdings(self):
        self.page.insert_text((415, 721), 'Market Cap % of net assets (Equity)', fontsize=8)
        for label, y, value in [('Large Cap', 754, '50.95%'), ('Mid Cap', 766, '17.01%'),
                                 ('Small Cap', 778, '5.16%'), ('% of top 5 holdings', 790, '23.63%')]:
            self.page.insert_text((405, y), label, fontsize=8)
            self.page.insert_text((521, y), value, fontsize=8)
        values, evidence = text_table(self.page, 'Union Mutual Fund')
        self.assertEqual(values, {'Large Cap': 50.95, 'Mid Cap': 17.01, 'Small Cap': 5.16})
        self.assertAlmostEqual(sum(values.values()), 73.12)
        self.assertEqual(evidence['page'], 1)

    def test_detected_but_unreadable_market_cap_is_a_parse_failure(self):
        self.page.insert_text((415, 721), 'Market Cap % of net assets (Equity)', fontsize=8)
        data = {'AMC / Fund House': 'Union Mutual Fund', 'Source Pages': [1]}
        enrich_market_cap(self.document, {'Arthaya Equity Long-Short Fund': data})
        self.assertEqual(data['Field Availability']['Market Capitalization (%)'], 'PARSE_FAILED')


if __name__ == '__main__':
    unittest.main()
