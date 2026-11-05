"""Regression checks against downloaded official AMC documents and published values."""
import json
import unittest
from pathlib import Path
from app import extract_factsheet
from extended_adapters import number,validate_extended
from fund_registry import FUND_HOUSES
from automation import filename
from monitor import fund_period

ROOT=Path(__file__).resolve().parent/'output/pdf'
FIXTURES={
 'arudha':('Arudha_Factsheet_August_2026.pdf','Arudha Equity Long-Short Fund',127.87,('Regular Plan - Growth',10.51)),
 'diviniti':('ITI DIVINITI SIF-Factsheet.pdf','Diviniti Equity Long-Short Fund',291.47,('Direct Plan - Growth',940.6063)),
 'arthaya':('arthaya-equity-long-short-fund.pdf','Arthaya Equity Long-Short Fund',133.73,('Direct Plan',10.0507)),
 'isif':('iSIF Factsheet September 2026.pdf','iSIF Active Asset Allocator Long-Short Fund',1145.42,('Direct Plan - Growth',10.2994)),
 'titanium':('Titanium SIF-Factsheet-30Sept2026.pdf','Titanium Hybrid Long-Short Fund',540.57,('Direct Plan - Growth',10.0137)),
 'sapphire':('Sapphire-SIF-Factsheet-as-on-August-31-2026.pdf','Sapphire Equity Long-Short Fund',216.02,('Direct Plan - Growth',1018.0210)),
 'platinum':('Platinum-SIF-Factsheet-1.pdf','Platinum Hybrid Long-Short Fund',201.38,('Direct Plan - Growth',10.0870)),
 'redhex':('redhex-sif-factsheet-as-on-august-2026.pdf','RedHex Hybrid Long-Short Fund',1072.84,('Regular Growth',10.1850)),
 'wsif':('WSIF_Factsheet_September_2026_ba57f4ec45.pdf','WSIF Equity Long-Short Fund',42.21,('Direct Plan - Growth Option',10.1303)),
 'summit':('summit-sif---factsheet-sep-2026.pdf','Summit Equity Long-Short Fund',375.61,('Direct Plan - Growth',10.18)),
 'magnum':('magnum-sif-as-on-august-2026-final.pdf','Magnum Hybrid Long-Short Fund',4004.89,('Dir-Plan-Growth',10.6251)),
 'infinity':('KotakSIFFactsheetSeptember2026.pdf','Infinity Hybrid Long-Short Fund',2994.62,('Direct Plan - Growth',10.3680)),
}

class PublishedValuesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.extracted={}
        for brand,(file,name,*_) in FIXTURES.items():
            path=ROOT/brand/file
            if not path.exists():path=ROOT.parent/'latest'/(filename(FUND_HOUSES[name])+'.pdf')
            if path.exists():
                data=extract_factsheet(path)
                import pymupdf
                with pymupdf.open(path) as doc:text='\n'.join(p.get_text() for p in doc)
                expected='2026-06' if brand=='platinum' else ('2026-09' if brand in {'isif','titanium','wsif','summit','infinity'} else '2026-08')
                if fund_period(data[name],text)==expected:cls.extracted[brand]=data
        path=ROOT/'qsif/qsif_-_Factsheet_-_October_2026.pdf'
        if not path.exists():path=ROOT.parent/'latest/quant Mutual Fund.pdf'
        if path.exists():cls.extracted['qsif']=extract_factsheet(path)

    def test_published_nav_aum_and_validation_for_every_retrieved_house(self):
        if not self.extracted:self.skipTest('Official fixture PDFs are not downloaded')
        for brand,(_,name,aum,(nav_label,nav_value)) in FIXTURES.items():
            if brand not in self.extracted:continue
            with self.subTest(brand=brand):
                d=self.extracted[brand][name]
                self.assertEqual(d['AUM (INR Cr)']['Month End AUM'],aum)
                self.assertEqual(d['NAV'][nav_label],nav_value)
                for fund in self.extracted[brand].values():
                    self.assertIn(fund['Extraction Validation']['Status'],{'passed','passed_with_warnings'})

    def test_derivatives_remain_signed_and_sector_subtotals_are_not_holdings(self):
        if 'diviniti' not in self.extracted:self.skipTest('Fixture unavailable')
        d=next(iter(self.extracted['diviniti'].values()))
        self.assertFalse(any(h['Instrument']=='Financial Services' for h in d['Portfolio']))
        self.assertAlmostEqual(sum(h['Percent to Net Assets'] or 0 for h in d['Portfolio'] if h['Category']=='Equity & Equity Related'),82.08)
        self.assertTrue(any(h.get('Derivatives Percent to Net Assets') is not None and h['Percent to Net Assets'] is None for h in d['Portfolio']))

    def test_headers_drawn_before_body_text_still_extract_objective_and_managers(self):
        if 'arudha' not in self.extracted:self.skipTest('Fixture unavailable')
        d=self.extracted['arudha']['Arudha Hybrid Long-Short Fund']
        self.assertIn('optimal returns',d['Investment Objective'])
        self.assertEqual([m['Name'] for m in d['Fund Managers']],['Kapil Kankonkar','Nilesh Saha','Brijesh Shah','Debraj Lahiri'])
        if 'qsif' in self.extracted:
            names=[m['Name'] for m in self.extracted['qsif']['qsif Active Asset Allocator Long-Short Fund']['Fund Managers']]
            self.assertIn('Ankit Pande',names)

    def test_parenthesized_derivative_is_negative_and_not_a_cash_position(self):
        self.assertEqual(number('(0.26)'),-.26)
        if 'sapphire' not in self.extracted:self.skipTest('Fixture unavailable')
        d=next(iter(self.extracted['sapphire'].values()))
        p=next(h for h in d['Portfolio'] if h['Instrument']=='PG Electroplast Ltd')
        self.assertIsNone(p['Percent to Net Assets']);self.assertEqual(p['Derivatives Percent to Net Assets'],-.26)

    def test_partial_portfolio_and_missing_nav_are_explicit(self):
        if 'qsif' not in self.extracted:self.skipTest('Fixture unavailable')
        self.assertEqual(len(self.extracted['qsif']),5)
        for d in self.extracted['qsif'].values():
            self.assertEqual(len(d['Portfolio']),10);self.assertEqual(d['NAV'],{})
            self.assertIn('Top 10',d['Portfolio Scope'])
            self.assertEqual(d['Extraction Validation']['Status'],'passed_with_warnings')
        active=self.extracted['qsif']['qsif Active Asset Allocator Long-Short Fund']
        debt=next(h for h in active['Portfolio'] if h['Instrument']=='CAPRI GLOBAL CAPITAL LTD CP')
        self.assertEqual(debt['Asset Class'],'Debt');self.assertEqual(debt['Rating'],'AA+')

    def test_bad_subtotal_blocks_publication(self):
        d={'Portfolio':[{'Instrument':'A','Category':'Equity & Equity Related','Percent to Net Assets':10}],
           'Portfolio Totals (%)':{'Equity & Equity Related':90},'AUM (INR Cr)':{'Month End AUM':100},'NAV':{'Direct':10}}
        validate_extended('Arudha Equity Long-Short Fund',d,'test')
        self.assertEqual(d['Extraction Validation']['Status'],'failed')

    def test_dyna_september_commercial_paper_has_its_own_subtotal(self):
        pdfs=list((ROOT.parent/'archive/360 ONE Asset/2026-09').glob('*.pdf'))
        if not pdfs and (ROOT.parent/'latest/360 ONE Asset.pdf').exists():pdfs=[ROOT.parent/'latest/360 ONE Asset.pdf']
        if not pdfs:self.skipTest('September official Dyna fixture unavailable')
        d=extract_factsheet(pdfs[0])['DynaSIF Active Asset Allocator Long-Short Fund']
        self.assertEqual(d['Portfolio Totals (%)']['Debt Instruments'],27.95)
        self.assertEqual(d['Portfolio Totals (%)']['Commercial Paper'],4.60)
        self.assertEqual(d['Rating Profile (%)']['AA-'],42.87)
        self.assertIn(d['Extraction Validation']['Status'],{'passed','passed_with_warnings'})

if __name__=='__main__':unittest.main()
