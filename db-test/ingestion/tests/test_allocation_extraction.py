"""Allocation regressions checked against the original published labels."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pymupdf

from factsheetDataIngestion.allocations import enrich_allocations, extract_allocation_groups
from factsheetDataIngestion.app import extract_factsheet
from factsheetDataIngestion.raster_allocations import relative_weights

ROOT=Path(__file__).resolve().parents[2]
PDFS=ROOT/'data/factsheets/latest'

@unittest.skipUnless((PDFS/'Franklin Templeton Mutual Fund.pdf').is_file(),'Local source PDFs required')
class AllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.funds=json.loads((PDFS/'extracted_factsheets.json').read_text(encoding='utf-8'))

    def groups(self,name):
        data=copy.deepcopy(self.funds[name])
        with pymupdf.open(data['Factsheet Source']['Local PDF']) as doc:
            return extract_allocation_groups(doc,data)

    def test_sapphire_exact_chart_and_wrapped_holdings_sector(self):
        funds=extract_factsheet(PDFS/'Franklin Templeton Mutual Fund.pdf')
        data=funds['Sapphire Equity Long-Short Fund']
        values=data['Sector Allocation (%)']
        self.assertEqual(len(values),35)
        self.assertEqual(values['BANKS'],17.47)
        self.assertEqual(values['AGRICULTURAL, COMMERCIAL & CONSTRUCTION VEHICLES'],1.39)
        self.assertEqual(values['CALL,CASH AND OTHER CURRENT ASSET'],6.25)
        self.assertAlmostEqual(sum(values.values()),100.01)
        tata=next(h for h in data['Portfolio'] if h['Instrument'].startswith('Tata Motors'))
        self.assertIn('Construction Vehicles',tata['Sector'])
        self.assertEqual(data['Asset Allocation (%)'],{'Total Holdings':93.75,'Margin on Derivatives':0.82,'Call,cash and other current asset':5.43})

    def test_bandhan_both_columns_and_rating_separate(self):
        groups=self.groups('Arudha Hybrid Long-Short Fund')
        rows=next(g['Rows'] for g in groups if g['Family']=='SECTOR')
        values={r['Label']:r['Percentage'] for r in rows}
        self.assertEqual(len(values),29)
        self.assertEqual(values['IT - Software'],0.37)
        self.assertEqual(values['Metals & Minerals Trading'],0.34)
        self.assertEqual(values['Realty'],0.03)
        self.assertNotIn('AA+',values)
        ratings=next(g['Rows'] for g in groups if g['Family']=='RATING')
        self.assertEqual({r['Label']:r['Percentage'] for r in ratings},{'AAA Equivalent':89.74,'AA+':5.17,'AA':5.09})

    def test_iti_net_cash_and_benchmark_are_distinct(self):
        groups=self.groups('Diviniti Equity Long-Short Fund')
        values={g['Family']:{r['Label']:r['Percentage'] for r in g['Rows']} for g in groups}
        self.assertEqual(values['SECTOR']['Financial Services'],23.35)
        self.assertEqual(values['BENCHMARK_SECTOR']['Financial Services'],37.45)
        self.assertEqual(values['SECTOR_CASH']['Financial Services'],30.61)
        self.assertEqual(values['SECTOR_DERIVATIVE']['Financial Services'],-7.26)

    def test_quant_review_is_hash_bound_and_retains_negative_weights(self):
        with pymupdf.open(PDFS/'quant Mutual Fund.pdf') as doc:
            groups=relative_weights(doc[26])
            self.assertEqual(groups[1]['Rows'][-1]['Percentage'],-1)
            self.assertEqual(groups[0]['Rows'][-1]['Percentage'],3)
            with patch('factsheetDataIngestion.raster_allocations.hashlib.sha256') as digest:
                digest.return_value.hexdigest.return_value='changed-image'
                with self.assertRaisesRegex(ValueError,'Unreviewed'):relative_weights(doc[26])

    def test_unavailable_review_is_parse_failure_not_not_published(self):
        data=copy.deepcopy(self.funds['qsif Hybrid Long-Short Fund'])
        with pymupdf.open(data['Factsheet Source']['Local PDF']) as doc:
            with patch('factsheetDataIngestion.raster_allocations.relative_weights',side_effect=ValueError('Unreviewed image')):
                enrich_allocations(doc,{'Example':data})
        self.assertEqual(data['Field Availability']['Sector Allocation (%)'],'PARSE_FAILED')
        self.assertIn('Unreviewed',data['Supplementary Fields']['Allocation Extraction Error'])

    def test_wealth_wrapped_label_and_published_derivatives(self):
        funds=extract_factsheet(PDFS/'The Wealth Company Mutual Fund.pdf')
        data=funds['WSIF Equity Ex-Top 100 Long-Short Fund']
        self.assertEqual(data['Extraction Validation']['Errors'],[])
        self.assertEqual(data['Sector Allocation (%)']['Agricultural, Commercial & Construction Vehicles'],1.31)
        self.assertEqual([h['Percent to Net Assets'] for h in data['Derivative Portfolio']],[1.91,0.56])
        self.assertEqual(data['Portfolio'][-1]['Percent to Net Assets'],4.88)
        self.assertEqual(len(data['Portfolio']),11)

    def test_icici_negative_exposures_not_rebased(self):
        groups=self.groups('iSIF Active Asset Allocator Long-Short Fund')
        assets=next(g for g in groups if g['Family']=='ASSET')
        values={r['Label']:r['Percentage'] for r in assets['Rows']}
        self.assertEqual(values['Stock Futures (Short)'],-2.35)
        self.assertEqual(values['Stock Options (Call)'],-28.48)
        self.assertEqual(assets['Source Page'],18)

    def test_quant_mixed_asset_top_ten_positions_are_valid(self):
        funds=extract_factsheet(PDFS/'quant Mutual Fund.pdf')
        self.assertEqual(len(funds),5)
        for name,data in funds.items():
            with self.subTest(scheme=name):
                self.assertEqual(data['Extraction Validation']['Errors'],[])
                self.assertEqual(data['Field Availability']['Sector Allocation (%)'],'PUBLISHED')

if __name__=='__main__':unittest.main()
