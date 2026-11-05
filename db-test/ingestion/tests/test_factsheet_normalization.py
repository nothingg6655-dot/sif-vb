"""Regression checks for published values previously lost by the database loader."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import psycopg

from factsheetDataIngestion.config import FactsheetConfig
from factsheetDataIngestion.loader import FactsheetIngester
from factsheetDataIngestion.normalization import (
    allocation_rows, availability_status, clean_numeric, idcw_rows,
    inr_amount, performance_rows, profile_values, quant_rows,
)
from ingestion.tigzig.config import DatabaseConfig


class PublishedValueTests(unittest.TestCase):
    def test_gross_and_net_market_cap_are_distinct_numeric_values(self):
        rows = allocation_rows({'Market Capitalization (%)': {'Large Cap': {'Gross': 33.6, 'Net': 34.47}}})
        self.assertEqual([(r['label'], r['percent']) for r in rows],
                         [('Large Cap (Gross)', Decimal('33.6')), ('Large Cap (Net)', Decimal('34.47'))])
        self.assertEqual(rows[1]['extra']['basis'], 'Net')

    def test_asset_rows_use_the_percent_column_and_keep_the_source(self):
        published = {'cells': ['Equity', '2,90,89,08,518', '84.6%'], 'page': 6, 'y': 205.81}
        row = allocation_rows({'Published Asset Allocation Rows': [published]})[0]
        self.assertEqual(row['percent'], Decimal('84.6'))
        self.assertEqual(row['page'], 6)
        self.assertEqual(row['extra']['published_row'], published)

    def test_nested_scheme_details_and_indian_currency_units(self):
        profile = profile_values({'Fund Details': {'Published Scheme Details': {
            'Inception Date': '08th June 2026', 'Benchmark': 'Nifty 500 TRI',
            'Min. Application Amount': 'INR 10 Lakhs',
        }}})
        self.assertEqual(profile['launch_date'], date(2026, 6, 8))
        self.assertEqual(profile['published_benchmark'], 'Nifty 500 TRI')
        self.assertEqual(profile['minimum_investment_inr'], Decimal('1000000'))
        self.assertEqual(inr_amount('INR 10 lakh (subject to min investment across strategies)'), Decimal('1000000'))
        self.assertEqual(inr_amount('Rs. 1,00,000/-'), Decimal('100000'))
        self.assertEqual(inr_amount('INR 0'), Decimal('0'))

    def test_invalid_numbers_are_not_partially_parsed(self):
        for value in [True, float('nan'), float('inf'), {'Gross': 30}, '10-20%', '10 apples']:
            self.assertIsNone(clean_numeric(value), repr(value))
        self.assertEqual(clean_numeric('\u22120.71%'), Decimal('-0.71'))
        self.assertEqual(clean_numeric('\u20b9 1,234.56 Cr'), Decimal('1234.56'))

    def test_availability_distinguishes_extracted_from_unparsed(self):
        self.assertEqual(availability_status('extracted'), 'PUBLISHED')
        self.assertEqual(availability_status('not normalized; consult Published Pages'), 'PARSE_FAILED')
        self.assertEqual(availability_status('NOT_PUBLISHED'), 'NOT_PUBLISHED')

    def test_published_quants_keep_units_and_text(self):
        rows = quant_rows({'Published Portfolio Quants': [{'PEG (x)': 0.75, 'EPS growth (%)': 32.14}]})
        self.assertEqual(rows[0]['unit'], 'x')
        self.assertEqual(rows[1]['unit'], '%')
        self.assertEqual(rows[1]['numeric'], Decimal('32.14'))

    def test_benchmark_rows_are_not_stored_as_scheme_returns(self):
        rows = performance_rows({'Scheme Performance': [
            {'Scheme': 'Fund - Dir - Growth', 'Last 6 Months (%)': '4.65%', 'PTP - Last 6 Months (INR)': '10,465'},
            {'Scheme': 'Benchmark*', 'Last 6 Months (%)': '2.00%'},
        ]})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['scheme_return'], Decimal('4.65'))
        self.assertEqual(rows[0]['benchmark_return'], Decimal('2.00'))
        self.assertEqual(rows[1]['family'], 'BENCHMARK')
        self.assertIsNone(rows[1]['scheme_return'])

    def test_monthly_performance_uses_the_published_month(self):
        rows = performance_rows({'Monthly Rolling Performance': [
            {'Months': 'Nov-25', 'Fund - Direct (G)': '1.01%', 'Benchmark (Index)': '1.11%'},
        ]})
        self.assertEqual(rows[0]['start'], date(2025, 11, 1))
        self.assertEqual(rows[0]['end'], date(2025, 11, 30))
        self.assertEqual(rows[0]['benchmark_return'], Decimal('1.11'))

    def test_idcw_record_dates_and_published_nav_are_retained(self):
        event = {'Record Date': '25-Sep-2026', 'IDCW (\u20b9 Per unit)': '0.06', 'NAV (\u20b9)': '10.67'}
        rows = idcw_rows({'IDCW History': {'Regular Plan': [event]}})
        self.assertEqual(rows[0]['date'], date(2026, 9, 25))
        self.assertEqual(rows[0]['amount'], Decimal('0.06'))
        self.assertEqual(rows[0]['attributes'], event)


class FactsheetReloadTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.base = FactsheetConfig.from_env().data_dir / 'latest'

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def source(self, name):
        return json.loads((self.base / 'extraction' / f'{name}.json').read_text(encoding='utf-8'))[name]

    def load(self, folder):
        return FactsheetIngester(self.conn).load_factsheets(
            Path(folder), self.base / 'manifest.json', self.base,
        )

    def test_reloading_preserves_values_and_does_not_duplicate_instruments(self):
        first = self.load(self.base / 'extraction')
        self.assertEqual(first.errors, [])
        before = self.conn.execute('SELECT count(*) FROM public.sif_instruments').fetchone()[0]
        second = self.load(self.base / 'extraction')
        after = self.conn.execute('SELECT count(*) FROM public.sif_instruments').fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(first.holdings_ingested, second.holdings_ingested)
        self.assertEqual(first.allocations_ingested, second.allocations_ingested)
        self.assertGreater(second.holdings_ingested, 0)
        row = self.conn.execute("""
            SELECT a.exposure_percent FROM public.sif_report_allocations a
            JOIN public.vw_sif_strategy_latest_report r USING(report_id)
            WHERE r.slug='isif-equity-long-short-fund' AND a.bucket_label='Large Cap (Net)'
        """).fetchone()
        self.assertEqual(row[0], Decimal('34.47'))
        self.assertEqual(self.conn.execute("""
            SELECT count(*) FROM public.sif_report_idcw_history i
            JOIN public.vw_sif_strategy_latest_report r USING(report_id)
            WHERE r.slug='altiva-hybrid-long-short-fund'
        """).fetchone()[0], 16)
        self.assertEqual(self.conn.execute("""
            SELECT inserted_count, updated_count FROM public.sif_ingestion_runs WHERE run_id=%s
        """, (second.run_id,)).fetchone(), (0, second.reports_ingested))

    def test_sql_failure_keeps_old_report_and_allows_the_next_file(self):
        name = 'Altiva Equity Ex-Top 100 Long-Short Fund'
        old = self.conn.execute("""
            SELECT report_id,raw_extraction_json FROM public.sif_factsheet_reports
            WHERE published_scheme_name=%s AND is_primary
        """, (name,)).fetchone()
        bad = deepcopy(self.source(name))
        bad['Portfolio'][0]['Quantity'] = 1e60  # PostgreSQL NUMERIC overflow after children are replaced.
        good_name = 'DynaSIF Active Asset Allocator Long-Short Fund'
        with TemporaryDirectory() as folder:
            Path(folder, 'a-bad.json').write_text(json.dumps({name: bad}), encoding='utf-8')
            Path(folder, 'z-good.json').write_text(json.dumps({good_name: self.source(good_name)}), encoding='utf-8')
            result = self.load(folder)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.reports_ingested, 1)
        self.assertEqual(len(result.errors), 1)
        unchanged = self.conn.execute(
            'SELECT report_id,raw_extraction_json FROM public.sif_factsheet_reports WHERE report_id=%s', (old[0],),
        ).fetchone()
        self.assertEqual(unchanged, old)
        self.assertEqual(self.conn.execute(
            'SELECT count(*) FROM public.sif_ingestion_rejections WHERE run_id=%s', (result.run_id,),
        ).fetchone()[0], 1)

    def test_every_scheme_in_a_json_file_is_loaded(self):
        names = ['DynaSIF Active Asset Allocator Long-Short Fund', 'Altiva Hybrid Long-Short Fund']
        with TemporaryDirectory() as folder:
            Path(folder, 'combined.json').write_text(json.dumps({name: self.source(name) for name in names}), encoding='utf-8')
            result = self.load(folder)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.reports_ingested, 2)

    def test_missing_pdf_hash_is_rejected_without_a_synthetic_document(self):
        name = 'Altiva Hybrid Long-Short Fund'
        bad = self.source(name)
        bad['Factsheet Source'].pop('SHA256')
        with TemporaryDirectory() as folder:
            Path(folder, 'bad.json').write_text(json.dumps({name: bad}), encoding='utf-8')
            result = self.load(folder)
        self.assertEqual(result.status, 'FAILED')
        self.assertEqual(result.documents_ingested, 0)
        self.assertIn('SHA256', result.errors[0])


if __name__ == '__main__':
    unittest.main()
