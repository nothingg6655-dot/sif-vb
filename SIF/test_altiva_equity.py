"""Regression checks against Altiva Equity Ex-Top 100's official September PDF."""
import unittest
from pathlib import Path

from app import extract_factsheet
from monitor import fund_period
from output_schema import CANONICAL_FUND_FIELDS,CANONICAL_MANAGER_FIELDS,CANONICAL_PORTFOLIO_FIELDS

ROOT=Path(__file__).resolve().parent
NAME='Altiva Equity Ex-Top 100 Long-Short Fund'


class AltivaEquityPublishedValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates=[ROOT/'tmp/Altiva Equity Ex-Top 100 September 2026.pdf']
        candidates+=list((ROOT/'output/latest').glob('Edelweiss Mutual Fund - equity-ex-top-100*.pdf'))
        if not any(p.is_file() for p in candidates):raise unittest.SkipTest('Official Altiva equity fixture unavailable')
        cls.data=extract_factsheet(next(p for p in candidates if p.is_file()))[NAME]
        if fund_period(cls.data,'')!='2026-09':raise unittest.SkipTest('Different reporting month selected')

    def test_reporting_date_nav_aum_and_expenses(self):
        d=self.data
        # The cover's old risk-band date must not override the actual portfolio date.
        self.assertEqual(d['Portfolio Date'],'30th September 2026')
        self.assertEqual(d['AUM (INR Cr)'],{'Month End AUM':1548.0,'Monthly Average AUM':1508.0})
        self.assertEqual(d['NAV'],{'Direct Plan':10.6804,'Regular Plan':10.6275})
        self.assertEqual(d['Base Expense Ratio (%)'],{'Direct Plan':.55,'Regular Plan':1.81})

    def test_complete_holdings_exclude_sector_subtotals_and_keep_treps(self):
        d=self.data
        self.assertEqual(len(d['Portfolio']),43)
        self.assertEqual(len(d['Sector Allocation (%)']),15)
        self.assertFalse(any(h['Instrument'] in d['Sector Allocation (%)'] for h in d['Portfolio']))
        first=next(h for h in d['Portfolio'] if h['Instrument']=='360 ONE WAM Ltd.')
        self.assertEqual(first['Sector'],'Financial Services')
        self.assertEqual(first['Market Capitalization'],'Mid Cap')
        self.assertEqual(first['Percent to Net Assets'],3.93)
        cash=next(h for h in d['Portfolio'] if h['Instrument']=='TREPS')
        self.assertEqual(cash['Percent to Net Assets'],1.33)
        self.assertTrue(cash['Aggregate'])
        self.assertEqual(d['Extraction Validation']['Status'],'passed')
        self.assertTrue(all(v['Within Tolerance'] for v in d['Extraction Validation']['Checks']['sector_subtotals'].values()))
        self.assertTrue(d['Extraction Validation']['Checks']['portfolio_grand_total']['Within Tolerance'])

    def test_assistant_manager_footnote_does_not_become_part_of_name(self):
        managers={m['Name']:m for m in self.data['Fund Managers']}
        self.assertEqual(len(managers),4)
        self.assertEqual(managers['Ashish Sood']['Role'],'Assistant Fund Manager - Equity')
        self.assertEqual(managers['Ashish Sood']['Managing Since'],'03rd August 2026')
        self.assertEqual(managers['Amit Vora']['Role'],'Fund Manager - Overseas')

    def test_uses_the_shared_output_schema(self):
        d=self.data
        self.assertEqual(tuple(d),CANONICAL_FUND_FIELDS)
        self.assertTrue(all(tuple(row)==CANONICAL_MANAGER_FIELDS for row in d['Fund Managers']))
        self.assertTrue(all(tuple(row)==CANONICAL_PORTFOLIO_FIELDS for row in d['Portfolio']))


if __name__=='__main__':unittest.main()
