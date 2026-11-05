"""Market-cap regressions: document attribution, missing fields and chart labels."""
from pathlib import Path
import unittest
from unittest.mock import patch

import pymupdf

from factsheetDataIngestion.market_cap import bar_chart_values, donut_chart, enrich_market_cap, raster_chart, text_table

PDFS = Path(__file__).resolve().parents[2] / 'data/factsheets/latest'


class MarketCapTests(unittest.TestCase):
    def test_bar_labels_ignore_axis_ticks_and_preserve_non_100_total(self):
        words = [{'text': '40', 'x': 10, 'y': 20, 'width': 20, 'height': 10}]
        for x, label, value in [(100, 'Large', '22.11'), (300, 'Mid', '45.50'), (500, 'Small', '20.92')]:
            words += [{'text': value, 'x': x, 'y': 60, 'width': 50, 'height': 10},
                      {'text': label, 'x': x - 15, 'y': 200, 'width': 40, 'height': 10},
                      {'text': 'Cap', 'x': x + 30, 'y': 200, 'width': 30, 'height': 10}]
        self.assertEqual(bar_chart_values(list(reversed(words))),
                         {'Large Cap': 22.11, 'Mid Cap': 45.5, 'Small Cap': 20.92})
        words.append({'text': '9.99', 'x': 100, 'y': 100, 'width': 50, 'height': 10})
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            bar_chart_values(words)

    def test_strategy_prose_is_not_a_published_allocation(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((30, 60), 'Investments across market capitalization. Large Cap: 1st-100th company.')
            funds = {'Example': {'AMC / Fund House': 'Kotak Mahindra Mutual Fund', 'Source Pages': [1]}}
            enrich_market_cap(doc, funds)
            self.assertEqual(funds['Example']['Field Availability']['Market Capitalization (%)'], 'NOT_PUBLISHED')
            self.assertFalse(funds['Example'].get('Market Capitalization (%)'))

    def test_continuation_page_stays_with_its_scheme_and_keeps_gross_net(self):
        with pymupdf.open() as doc:
            doc.new_page()
            page = doc.new_page()
            page.insert_text((340, 80), 'Market Cap Break-Up Allocation Details', fontsize=10)
            for y, label, gross, net in [(105, 'Large', '9.69', '7.32'), (135, 'Mid', '41.09', '43.94'), (165, 'Small', '28.97', '28.97')]:
                page.insert_text((350, y), label + ' Cap Allocation', fontsize=10)
                page.insert_text((350, y+12), f'Gross : {gross}% | Net : {net}%', fontsize=10)
            funds = {'Scheme A': {'AMC / Fund House': 'ICICI Prudential Mutual Fund', 'Source Pages': [1, 2]},
                     'Scheme B': {'AMC / Fund House': 'ICICI Prudential Mutual Fund', 'Source Pages': [1]}}
            enrich_market_cap(doc, funds)
            self.assertEqual(funds['Scheme A']['Market Capitalization (%)']['Large Cap'], {'Gross': 9.69, 'Net': 7.32})
            self.assertEqual(funds['Scheme A']['Supplementary Fields']['Market Capitalization Evidence']['sources'][0]['page'], 2)
            self.assertFalse(funds['Scheme B'].get('Market Capitalization (%)'))

    def test_missing_ocr_engine_is_a_parse_failure_not_a_zero_allocation(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((25, 100), 'CONTRIBUTION BY MARKET CAP')
            funds = {'Example': {'AMC / Fund House': 'quant Mutual Fund', 'Source Pages': [1],
                                 'Extraction Validation': {'Status': 'passed'}}}
            with patch('factsheetDataIngestion.market_cap.raster_chart', side_effect=RuntimeError('OCR unavailable')):
                enrich_market_cap(doc, funds)
            self.assertEqual(funds['Example']['Field Availability']['Market Capitalization (%)'], 'PARSE_FAILED')
            self.assertEqual(funds['Example']['Extraction Validation']['Status'], 'passed_with_warnings')
            self.assertFalse(funds['Example'].get('Market Capitalization (%)'))


@unittest.skipUnless((PDFS / 'ICICI Prudential Mutual Fund.pdf').is_file(), 'Local official source PDFs not present')
class OfficialMarketCapRegressionTests(unittest.TestCase):
    def test_all_five_raster_chart_labels(self):
        # Expected labels were visually checked against the original chart images.
        with pymupdf.open(PDFS / 'quant Mutual Fund.pdf') as doc:
            for page, expected in [(15, (22.11, 45.50, 20.92)), (19, (39.81, 26.01, 16.77)),
                                   (23, (10.33, 15.94, 19.19)), (27, (6.06, 7.64, 30.12)),
                                   (31, (37.65, 34.93, 7.65))]:
                with self.subTest(page=page):
                    result = raster_chart(doc[page - 1])
                    self.assertEqual(result[0], dict(zip(('Large Cap', 'Mid Cap', 'Small Cap'), expected)))

    def test_published_tables_and_donut_labels(self):
        cases = [
            ('ICICI Prudential Mutual Fund.pdf', 11, 'ICICI Prudential Mutual Fund',
             {'Large Cap': {'Gross': 9.69, 'Net': 7.32}, 'Mid Cap': {'Gross': 41.09, 'Net': 43.94}, 'Small Cap': {'Gross': 28.97, 'Net': 28.97}}),
            ('Tata Mutual Fund.pdf', 2, 'Tata Mutual Fund', {'Large Cap': 34.5, 'Mid Cap': 8.6, 'Small Cap': 9.8}),
            ('Tata Mutual Fund.pdf', 5, 'Tata Mutual Fund', {'Large Cap': 44.7, 'Mid Cap': 21.2, 'Small Cap': 18.7}),
            ('ITI Mutual Fund.pdf', 3, 'ITI Mutual Fund', {'Large Cap': 56.13, 'Mid Cap': 8.04, 'Small Cap': 6.92}),
            ('Invesco Mutual Fund.pdf', 2, 'Invesco Mutual Fund', {'Large Cap': 39.0, 'Mid Cap': 22.4, 'Small Cap': 32.1}),
            ('SBI Mutual Fund.pdf', 2, None, {'Large Cap': 58.35, 'Mid Cap': 3.73, 'Small Cap': 3.84}),
            ('Edelweiss Mutual Fund - equity-ex-top-100 - 2026-09 - b417592b.pdf', 2, None,
             {'Large Cap': 7.0, 'Mid Cap': 27.0, 'Small Cap': 66.0}),
        ]
        for filename, page, house, expected in cases:
            with self.subTest(source=filename, page=page), pymupdf.open(PDFS / filename) as doc:
                result = text_table(doc[page-1], house) if house else donut_chart(doc[page-1])
                self.assertEqual(result[0], expected)


if __name__ == '__main__':
    unittest.main()
