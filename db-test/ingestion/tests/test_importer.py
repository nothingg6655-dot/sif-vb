from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig
from ingestion.tigzig.importer import ImportError, read_nav_upload_text
from ingestion.tigzig.manifest import ManifestFile
from ingestion.tigzig.staging import copy_rows, create_staging_tables, staging_counts


class ImporterTests(unittest.TestCase):
    def test_nav_csv_keeps_original_ist_wall_clock_text(self):
        raw = (
            'scheme_code,date,nav,scheme_name,isin,isin2,amc,upload_ts,temp_sif_code\n'
            '13,2026-01-02,10.25,Fund,,,AMC,2026-01-02T20:15:00Z,SIF-13\n'
        ).encode()
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sif_nav.csv'
            path.write_bytes(raw)
            expected = ManifestFile(
                'nav_raw_timestamp', path.name, 1,
                ('scheme_code', 'date', 'nav', 'scheme_name', 'isin', 'isin2',
                 'amc', 'upload_ts', 'temp_sif_code'),
                len(raw), sha256(raw).hexdigest(),
            )
            values = read_nav_upload_text(path, expected)
        self.assertEqual(values[(13, date(2026, 1, 2))], '2026-01-02T20:15:00Z')

    def test_nav_csv_rejects_duplicate_business_key(self):
        text = (
            'scheme_code,date,nav,scheme_name,isin,isin2,amc,upload_ts,temp_sif_code\n'
            '13,2026-01-02,10.25,Fund,,,AMC,a,SIF-13\n'
            '13,2026-01-02,10.26,Fund,,,AMC,b,SIF-13\n'
        )
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sif_nav.csv'
            path.write_text(text, encoding='utf-8')
            expected = ManifestFile(
                'nav_raw_timestamp', path.name, 2,
                ('scheme_code', 'date', 'nav', 'scheme_name', 'isin', 'isin2',
                 'amc', 'upload_ts', 'temp_sif_code'),
                len(text.encode()), sha256(text.encode()).hexdigest(),
            )
            with self.assertRaisesRegex(ImportError, 'duplicate'):
                read_nav_upload_text(path, expected)

    def test_copy_loads_run_scoped_staging_tables(self):
        with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
            verify_target(connection)
            create_staging_tables(connection)
            meta_count = copy_rows(connection, 'stg_tigzig_meta', [(
                'SIF', 1, 'AMC', 'Fund', None, None, None, None, None, 'Direct',
                'Growth', date(2026, 1, 1), date(2026, 1, 2), True, False,
                10.2, date(2026, 1, 2), None, None, None, None, 'SIF-1',
                'stated', None, None, 2, None, None, Jsonb({}),
            )])
            nav_count = copy_rows(connection, 'stg_tigzig_nav', [
                ('SIF', 1, date(2026, 1, 2), 10.2, '2026-01-02T20:15:00Z', None)
            ])
            rejection_count = copy_rows(connection, 'stg_tigzig_rejections', [
                ('aaum', Jsonb({'scheme_code': None}), 'missing scheme code')
            ])
            self.assertEqual((meta_count, nav_count, rejection_count), (1, 1, 1))
            self.assertEqual(staging_counts(connection), {
                'meta': 1, 'nav': 1, 'aaum': 0, 'rejections': 1,
            })
            connection.rollback()


if __name__ == '__main__':
    unittest.main()
