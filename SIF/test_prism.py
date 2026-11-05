"""Regression checks against the official Prism August and Platinum reporting PDFs."""
import unittest
from collections import Counter
from pathlib import Path

from app import extract_factsheet
from monitor import fund_period

ROOT=Path(__file__).resolve().parent


class PrismPublishedValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=ROOT/'output/latest/Jio BlackRock Mutual Fund.pdf'
        if not path.exists():path=ROOT/'tmp/Prism August 2026.pdf'
        if not path.exists():raise unittest.SkipTest('Official Prism August fixture is unavailable')
        cls.data=extract_factsheet(path)['Prism Hybrid Long-Short Fund']
        if fund_period(cls.data,'')!='2026-08':raise unittest.SkipTest('A different Prism reporting month is now selected')

    def test_displayed_aum_overrides_covered_obsolete_text(self):
        d=self.data
        self.assertEqual(d['AUM (INR Cr)'],{'Month End AUM':302.73,'Monthly Average AUM':240.02})
        self.assertEqual(d['Published AUM Corrections']['Monthly Average AUM']['Text Drawings in Paint Order'],[76.48,240.02])
        self.assertEqual(d['NAV'],{'Direct Plan - Growth':10.1921,'Regular Plan - Growth':10.1773})
        self.assertEqual(d['Base Expense Ratio (%)'],{'Direct Plan':.40,'Regular Plan':1.38})
        self.assertEqual(len(d['Fund Managers']),5)

    def test_all_holdings_reconcile_and_futures_do_not_inflate_assets(self):
        d=self.data
        self.assertEqual(d['Extraction Validation']['Status'],'passed')
        self.assertEqual(Counter(h['Category'] for h in d['Portfolio'])['Equity & Equity Related'],38)
        self.assertEqual(len(d['Derivative Portfolio']),5)
        self.assertFalse(any('FUT' in h['Instrument'] for h in d['Portfolio']))
        short=next(h for h in d['Derivative Portfolio'] if h['Instrument'].startswith('Indian Hotels'))
        self.assertEqual(short['Derivatives Percent to Net Assets'],-2.75)
        self.assertEqual(d['Derivative Totals (%)']['Total'],3.54)
        self.assertTrue(d['Extraction Validation']['Checks']['portfolio_grand_total']['Within Tolerance'])
        self.assertEqual(next(h for h in d['Portfolio'] if h['Instrument']=='Net Receivables / (Payables)')['Percent to Net Assets'],-8.84)
        self.assertTrue(all(c['Within Tolerance'] for c in d['Extraction Validation']['Checks']['portfolio_subtotals'].values()))


class PlatinumPublishedValuesTests(unittest.TestCase):
    def test_august_reporting_month_and_nav_after_layout_move(self):
        path=ROOT/'tmp/Platinum-SIF-Factsheet-September-2026.pdf'
        if not path.exists():path=ROOT/'output/latest/Mirae Asset Mutual Fund.pdf'
        if not path.exists():self.skipTest('Official Platinum fixture unavailable')
        d=extract_factsheet(path)['Platinum Hybrid Long-Short Fund']
        if fund_period(d,'')!='2026-08':self.skipTest('Different reporting month selected')
        self.assertEqual(d['AUM (INR Cr)']['Month End AUM'],369.66)
        self.assertEqual(d['NAV']['Direct Plan - Growth'],10.237)
        self.assertEqual(d['NAV']['Regular Plan - Growth'],10.206)
        self.assertEqual(d['Base Expense Ratio (%)'],{'Regular Plan':1.3,'Direct Plan':.15})
        self.assertEqual(d['Extraction Validation']['Status'],'passed_with_warnings')
        self.assertNotIn('NAV values are not present',' '.join(d['Extraction Validation']['Warnings']))


if __name__=='__main__':unittest.main()
