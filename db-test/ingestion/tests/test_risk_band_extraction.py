"""Risk bands come from selected captions, never scale ticks or other schemes."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pymupdf

from factsheetDataIngestion.risk_bands import (
    BENCHMARK, FIELD, STRATEGY, caption_candidates, enrich_risk_bands, paired_levels,
    raster_candidates,
)

ROOT = Path(__file__).resolve().parents[2]


def fund(pages):
    return {'AMC / Fund House': 'Example', 'Source Pages': pages,
            'Extraction Validation': {'Status': 'passed'}, 'Portfolio': [{'Instrument': 'Keep me'}]}


def label(page, x, y, level):
    page.insert_text((x, y), f'Risk Band Level {level}', fontsize=10)


class RiskBandTests(unittest.TestCase):
    def test_left_and_right_captions_override_pdf_reading_order(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            label(page, 330, 100, 3)
            label(page, 30, 100, 5)
            data = fund([1])
            before = copy.deepcopy(data['Portfolio'])
            enrich_risk_bands(doc, {'Example': data})
            self.assertEqual(data[FIELD][STRATEGY], 'Level 5')
            self.assertEqual(data[FIELD][BENCHMARK], 'Level 3')
            self.assertEqual(data['Portfolio'], before)
            self.assertEqual(data['Field Availability'][FIELD], 'PUBLISHED')

    def test_bands_are_not_borrowed_from_other_schemes(self):
        with pymupdf.open() as doc:
            doc.new_page()
            page = doc.new_page()
            label(page, 30, 100, 5)
            label(page, 330, 100, 3)
            data = fund([1])
            enrich_risk_bands(doc, {'Example': data})
            self.assertFalse(data[FIELD])
            self.assertEqual(data['Field Availability'][FIELD], 'PARSE_FAILED')

    def test_scale_ticks_and_monthly_history_do_not_become_a_pair(self):
        words = [{'text': 'Risk Band 1 2 3 4 5 Previous month Level 2 Current month Level 3',
                  'rect': [0, 0, 300, 10]}]
        self.assertFalse(caption_candidates(words, 1, 'pdf-explicit-caption'))
        with pymupdf.open() as doc:
            page = doc.new_page()
            label(page, 30, 100, 2)
            label(page, 30, 140, 3)
            data = fund([1])
            enrich_risk_bands(doc, {'Example': data})
            self.assertFalse(data[FIELD])

    def test_conflicting_pages_require_review_and_keep_existing_verified_values(self):
        with pymupdf.open() as doc:
            for benchmark in (3, 4):
                page = doc.new_page()
                label(page, 30, 100, 5)
                label(page, 330, 100, benchmark)
            data = fund([1, 2])
            data[FIELD] = {STRATEGY: 'Level 5', BENCHMARK: 'Level 3'}
            enrich_risk_bands(doc, {'Example': data})
            self.assertEqual(data['Field Availability'][FIELD], 'PARSE_FAILED')
            self.assertEqual(data[FIELD][BENCHMARK], 'Level 3')
            self.assertIn('Conflicting', data['Supplementary Fields']['Risk Band Evidence']['Errors'][0])

    def test_separate_risk_date_is_preserved(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            label(page, 30, 100, 1)
            label(page, 330, 100, 2)
            page.insert_text((30, 140), 'Risk band as on August 31, 2026')
            data = fund([1])
            enrich_risk_bands(doc, {'Example': data})
            self.assertEqual(data[FIELD]['Risk Band As Of'], '2026-08-31')

    def test_reviewed_indicators_are_bound_to_image_checksum(self):
        class Document:
            def extract_image(self, xref):
                return {'image': b'changed image', 'ext': 'png', 'width': 100, 'height': 50}
        class Page:
            parent = Document()
            number = 0
            def get_text(self):
                return 'qsif'
        with patch('factsheetDataIngestion.risk_bands.ocr_image', return_value={'words': []}):
            self.assertEqual(raster_candidates(Page(), {'xref': 1, 'bbox': [0, 0, 100, 50]}), [])

    def test_wide_combined_chart_keeps_both_identical_levels(self):
        # One wide image includes the scheme and benchmark captions. It must
        # not be collapsed to one level merely because both are Level 5.
        class Document:
            def extract_image(self, xref):
                return {'image': b'combined chart', 'ext': 'png', 'width': 400, 'height': 100}
        class Page:
            parent = Document()
            number = 0
            def get_text(self):
                return 'qsif'
        words = [{'text': 'Risk Band Level 5', 'x': x, 'y': 60, 'width': 100, 'height': 10}
                 for x in (10, 230)]
        with patch('factsheetDataIngestion.risk_bands.ocr_image', return_value={'words': words}):
            candidates = raster_candidates(Page(), {'xref': 1, 'bbox': [0, 0, 400, 100]})
            pairs = paired_levels(candidates)
            self.assertEqual([(a['level'], b['level']) for a, b in pairs], [(5, 5)])


class OfficialRiskBandTests(unittest.TestCase):
    def test_all_checksum_bound_official_factsheets(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/risk-bands-september-2026.json').read_text())
        available = [row for row in fixture if (ROOT / 'data/factsheets/archive' / (row['sha256'] + '.pdf')).is_file()]
        if not available:
            self.skipTest('Official PDF archive not present')
        for row in available:
            with self.subTest(scheme=row['name']), pymupdf.open(
                    ROOT / 'data/factsheets/archive' / (row['sha256'] + '.pdf')) as doc:
                data = fund(row['pages'])
                data['AMC / Fund House'] = row['house']
                enrich_risk_bands(doc, {row['name']: data})
                self.assertEqual(data['Field Availability'][FIELD], 'PUBLISHED')
                self.assertEqual([data[FIELD][STRATEGY], data[FIELD][BENCHMARK]], row['bands'])
                self.assertEqual(data[FIELD]['Risk Band As Of'], row['as_of'])


if __name__ == '__main__':
    unittest.main()
