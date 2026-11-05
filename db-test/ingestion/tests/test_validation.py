from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from ingestion.tigzig.manifest import ManifestFile
from ingestion.tigzig.parquet import ParquetError, read_parquet
from ingestion.tigzig.validator import DatasetSchemaError, validate_datasets, validate_meta


def tables():
    meta = pa.table({
        'scheme_code': pa.array([1, 2], type=pa.int64()),
        'scheme_name': ['Fund Direct Growth', 'Fund Regular Growth'],
        'amc': ['Example AMC', 'Example AMC'],
        'source': ['SIF', 'SIF'],
        'temp_new_optional': ['kept', None],
    })
    nav = pa.table({
        'scheme_code': pa.array([1, 2], type=pa.int64()),
        'date': pa.array([date(2026, 1, 1), date(2026, 1, 1)], type=pa.date32()),
        'nav': pa.array([Decimal('10.1'), Decimal('10.2')], type=pa.decimal128(24, 10)),
    })
    aaum = pa.table({
        'scheme_code': pa.array([1, 2], type=pa.int64()),
        'quarter_start': pa.array([date(2026, 1, 1)] * 2, type=pa.date32()),
        'aaum_quarter_end': pa.array([date(2026, 3, 31)] * 2, type=pa.date32()),
        'aaum_lakhs': pa.array([Decimal('100.0000'), Decimal('200.0000')], type=pa.decimal128(26, 4)),
        'aaum_cr': pa.array([Decimal('1.000000'), Decimal('2.000000')], type=pa.decimal128(24, 6)),
    })
    return meta, nav, aaum


class ParquetAndValidationTests(unittest.TestCase):
    def test_valid_rows_and_additive_optional_schema(self):
        report = validate_datasets(*tables())
        self.assertEqual(report.rejected_rows, 0)
        self.assertEqual(len(report.meta.valid_rows), 2)
        self.assertIn('temp_new_optional', report.meta.valid_rows[0])

    def test_missing_required_column_is_schema_change(self):
        meta, _, _ = tables()
        with self.assertRaises(DatasetSchemaError):
            validate_meta(meta.drop(['source']))

    def test_current_aaum_column_name_is_normalized(self):
        meta, nav, aaum = tables()
        aaum = aaum.rename_columns([
            'aaum_cr_quarterly_avg' if name == 'aaum_cr' else name
            for name in aaum.column_names
        ])
        report = validate_datasets(meta, nav, aaum)
        self.assertEqual(report.aaum.rejected_rows, 0)
        self.assertEqual(report.aaum.valid_rows[0]['aaum_cr'], Decimal('1.000000'))
        self.assertIn('aaum_cr_quarterly_avg', report.aaum.valid_rows[0])

    def test_current_aaum_column_still_checks_conversion(self):
        meta, nav, aaum = tables()
        aaum = aaum.drop(['aaum_cr']).append_column(
            'aaum_cr_quarterly_avg', pa.array([Decimal('9'), Decimal('2')]),
        )
        report = validate_datasets(meta, nav, aaum)
        self.assertEqual(report.aaum.rejected_rows, 1)
        self.assertEqual(report.aaum.issues[0].code, 'AAUM_CONVERSION')

    def test_aaum_requires_a_crore_column(self):
        meta, nav, aaum = tables()
        with self.assertRaises(DatasetSchemaError):
            validate_datasets(meta, nav, aaum.drop(['aaum_cr']))

    def test_invalid_numeric_and_unknown_scheme_are_retained_as_rejections(self):
        meta, _, aaum = tables()
        nav = pa.table({
            'scheme_code': pa.array([1, 1, 999, 2], type=pa.int64()),
            'date': pa.array([date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 2)]),
            'nav': pa.array([10.0, 11.0, 10.0, float('nan')]),
        })
        report = validate_datasets(meta, nav, aaum)
        self.assertEqual(report.nav.rejected_rows, 4)
        self.assertEqual(report.nav.duplicate_keys, 1)
        self.assertEqual(
            {issue.code for issue in report.nav.issues},
            {'DUPLICATE_KEY', 'UNKNOWN_SCHEME', 'INVALID_NAV'},
        )
        self.assertTrue(all(issue.raw_record for issue in report.nav.issues))

    def test_aaum_conversion_duplicate_and_invalid_quarter(self):
        meta, nav, _ = tables()
        aaum = pa.table({
            'scheme_code': [1, 1, 2, 2],
            'quarter_start': [date(2026, 1, 1), date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1)],
            'aaum_quarter_end': [date(2026, 3, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 6, 30)],
            'aaum_lakhs': [100, 100, 100, 100],
            'aaum_cr': [1, 1, 2, 1],
        })
        report = validate_datasets(meta, nav, aaum)
        self.assertEqual(report.aaum.rejected_rows, 4)
        self.assertEqual(report.aaum.duplicate_keys, 2)
        self.assertEqual(
            {issue.code for issue in report.aaum.issues},
            {'DUPLICATE_KEY', 'AAUM_CONVERSION', 'INVALID_QUARTER'},
        )

    def test_truncated_parquet_is_rejected(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sif_meta.parquet'
            path.write_bytes(b'PAR1truncated')
            expected = ManifestFile('meta', path.name, 1, ('scheme_code',), path.stat().st_size, sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(ParquetError):
                read_parquet(path, expected)

    def test_manifest_row_and_column_mismatch_are_rejected(self):
        meta, _, _ = tables()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sif_meta.parquet'
            pq.write_table(meta, path)
            bad_rows = ManifestFile('meta', path.name, 99, tuple(meta.column_names), path.stat().st_size, '0' * 64)
            with self.assertRaisesRegex(ParquetError, 'rows'):
                read_parquet(path, bad_rows)
            bad_columns = ManifestFile('meta', path.name, 2, ('scheme_code',), path.stat().st_size, '0' * 64)
            with self.assertRaisesRegex(ParquetError, 'columns'):
                read_parquet(path, bad_columns)

