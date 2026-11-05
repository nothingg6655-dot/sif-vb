from datetime import date, datetime, timezone
from decimal import Decimal
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pyarrow as pa

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig
from ingestion.tigzig.importer import PreparedSource
from ingestion.tigzig.manifest import Manifest, ManifestFile
from ingestion.tigzig.staging import create_staging_tables
from ingestion.tigzig.sync import apply_staged_changes, classify_staged, sync_prepared
from ingestion.tigzig.validator import (
    DatasetValidation, ValidationReport, validate_datasets,
)


class SyncDeltaTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        verify_target(self.connection)
        create_staging_tables(self.connection)
        self.source = 'SYNC-TEST-' + uuid4().hex
        self.amc = 'Sync Test AMC ' + uuid4().hex
        self.house = self.connection.execute(
            'INSERT INTO public.tigzig_fund_houses(original_amc_name) VALUES (%s) RETURNING fund_house_id',
            (self.amc,),
        ).fetchone()[0]

    def add_scheme(self, code=1, name='Test Fund', category=None):
        self.connection.execute("""
            INSERT INTO public.tigzig_schemes(
              source, scheme_code, fund_house_id, scheme_name, category, source_metadata
            ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb)
        """, (self.source, code, self.house, name, category))

    def stage_scheme(
        self, code=1, name='Test Fund', category=None, isin=None, isin2=None,
    ):
        self.connection.execute("""
            INSERT INTO stg_tigzig_meta(
              source, scheme_code, amc, scheme_name, category, isin, isin2,
              source_metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
        """, (self.source, code, self.amc, name, category, isin, isin2))

    def stage_nav(self, code, nav_date, nav, raw='2026-01-01 20:00:00'):
        self.connection.execute("""
            INSERT INTO stg_tigzig_nav(
              source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
            ) VALUES (%s, %s, %s, %s, %s, %s)
        """, (self.source, code, nav_date, nav, raw, raw))

    def stage_aaum(self, code, start, end, label, lakhs, crores):
        self.connection.execute("""
            INSERT INTO stg_tigzig_aaum(
              source, scheme_code, quarter_start, quarter_end, aaum_quarter,
              aaum_lakhs, aaum_cr
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (self.source, code, start, end, label, lakhs, crores))

    def apply(self):
        deltas = classify_staged(self.connection)
        mutations = apply_staged_changes(self.connection, deltas)
        return deltas, mutations

    def test_new_scheme_and_identifier(self):
        self.stage_scheme(7, isin='TEST-NEW-' + uuid4().hex)
        deltas, mutations = self.apply()
        self.assertEqual(deltas['meta']['NEW'], 1)
        self.assertEqual(deltas['isins']['NEW'], 1)
        self.assertEqual(mutations['schemes_inserted'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM public.tigzig_schemes WHERE source=%s',
            (self.source,),
        ).fetchone()[0], 1)

    def test_new_nav_date(self):
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav)
            VALUES (%s, 1, '2026-01-10', 10)
        """, (self.source,))
        self.stage_scheme()
        self.stage_nav(1, date(2026, 1, 15), '11')
        deltas, mutations = self.apply()
        self.assertEqual(deltas['nav']['NEW'], 1)
        self.assertEqual(mutations['nav_inserted'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT array_agg(nav_date ORDER BY nav_date) FROM public.tigzig_scheme_nav WHERE source=%s',
            (self.source,),
        ).fetchone()[0], [date(2026, 1, 10), date(2026, 1, 15)])

    def test_late_nav_date_does_not_remove_existing_history(self):
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav)
            VALUES (%s, 1, '2026-01-10', 10)
        """, (self.source,))
        self.stage_scheme()
        self.stage_nav(1, date(2026, 1, 5), '9')
        deltas, mutations = self.apply()
        self.assertEqual(deltas['nav']['NEW'], 1)
        self.assertEqual(mutations['nav_inserted'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT array_agg(nav_date ORDER BY nav_date) FROM public.tigzig_scheme_nav WHERE source=%s',
            (self.source,),
        ).fetchone()[0], [date(2026, 1, 5), date(2026, 1, 10)])

    def test_old_nav_correction(self):
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_nav(
              source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
            ) VALUES (%s, 1, '2026-01-02', 10, 'old', '2026-01-02 20:00:00')
        """, (self.source,))
        self.stage_scheme()
        self.stage_nav(1, date(2026, 1, 2), '10.5', '2026-01-02 21:00:00')
        deltas, mutations = self.apply()
        self.assertEqual(deltas['nav']['CHANGED'], 1)
        self.assertEqual(mutations['nav_updated'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT nav FROM public.tigzig_scheme_nav WHERE source=%s',
            (self.source,),
        ).fetchone()[0], Decimal('10.5000000000'))

    def test_changed_scheme_metadata_and_isin(self):
        old_isin = 'TEST-OLD-' + uuid4().hex
        new_isin = 'TEST-NEW-' + uuid4().hex
        self.add_scheme(name='Old Name', category='Old Category')
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_isins(
              source, scheme_code, identifier_type, isin
            ) VALUES (%s, 1, 'PRIMARY', %s)
        """, (self.source, old_isin))
        self.stage_scheme(name='Correct Name', category='Correct Category', isin=new_isin)
        deltas, mutations = self.apply()
        self.assertEqual(deltas['meta']['CHANGED'], 1)
        self.assertEqual(deltas['isins']['CHANGED'], 1)
        self.assertEqual(mutations['schemes_updated'], 1)
        self.assertEqual(self.connection.execute("""
            SELECT s.scheme_name, s.category, i.isin
            FROM public.tigzig_schemes s
            JOIN public.tigzig_scheme_isins i USING (source, scheme_code)
            WHERE s.source=%s
        """, (self.source,)).fetchone(), ('Correct Name', 'Correct Category', new_isin))

    def test_isin_only_correction_counts_as_changed_metadata(self):
        old_isin = 'TEST-OLD-' + uuid4().hex
        new_isin = 'TEST-NEW-' + uuid4().hex
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_isins(
              source, scheme_code, identifier_type, isin
            ) VALUES (%s, 1, 'PRIMARY', %s)
        """, (self.source, old_isin))
        self.stage_scheme(isin=new_isin)
        deltas, mutations = self.apply()
        self.assertEqual(deltas['meta']['CHANGED'], 1)
        self.assertEqual(deltas['isins']['CHANGED'], 1)
        self.assertEqual(mutations['schemes_updated'], 0)
        self.assertEqual(mutations['isins_updated'], 1)

    def test_explicit_isin_removal_for_present_scheme(self):
        old_isin = 'TEST-OLD-' + uuid4().hex
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_isins(
              source, scheme_code, identifier_type, isin
            ) VALUES (%s, 1, 'PRIMARY', %s)
        """, (self.source, old_isin))
        self.stage_scheme(isin=None)
        deltas, mutations = self.apply()
        self.assertEqual(deltas['meta']['CHANGED'], 1)
        self.assertEqual(deltas['isins']['CHANGED'], 1)
        self.assertEqual(mutations['isins_removed'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM public.tigzig_scheme_isins WHERE source=%s',
            (self.source,),
        ).fetchone()[0], 0)

    def test_isin_relationship_swap_is_atomic(self):
        first = 'TEST-FIRST-' + uuid4().hex
        second = 'TEST-SECOND-' + uuid4().hex
        self.add_scheme(1, name='First Fund')
        self.add_scheme(2, name='Second Fund')
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_isins(
              source, scheme_code, identifier_type, isin
            ) VALUES (%s, 1, 'PRIMARY', %s), (%s, 2, 'PRIMARY', %s)
        """, (self.source, first, self.source, second))
        self.stage_scheme(1, name='First Fund', isin=second)
        self.stage_scheme(2, name='Second Fund', isin=first)
        deltas, mutations = self.apply()
        self.assertEqual(deltas['isins']['CHANGED'], 2)
        self.assertEqual(mutations['isins_updated'], 2)
        self.assertEqual(self.connection.execute("""
            SELECT scheme_code, isin FROM public.tigzig_scheme_isins
            WHERE source=%s ORDER BY scheme_code
        """, (self.source,)).fetchall(), [(1, second), (2, first)])

    def test_new_and_corrected_aaum(self):
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_aaum(
              source, scheme_code, quarter_start, quarter_end, aaum_quarter,
              aaum_lakhs, aaum_cr
            ) VALUES (%s, 1, '2026-01-01', '2026-03-31', 'March-2026', 100, 1)
        """, (self.source,))
        self.stage_scheme()
        self.stage_aaum(1, date(2026, 1, 1), date(2026, 3, 31), 'March-2026', '125', '1.25')
        self.stage_aaum(1, date(2026, 4, 1), date(2026, 6, 30), 'June-2026', '200', '2')
        deltas, mutations = self.apply()
        self.assertEqual(deltas['aaum']['NEW'], 1)
        self.assertEqual(deltas['aaum']['CHANGED'], 1)
        self.assertEqual(mutations['aaum_inserted'], 1)
        self.assertEqual(mutations['aaum_updated'], 1)
        self.assertEqual(self.connection.execute(
            'SELECT count(*) FROM public.tigzig_scheme_aaum WHERE source=%s',
            (self.source,),
        ).fetchone()[0], 2)

    def test_identical_reimport_leaves_timestamps_unchanged(self):
        isin = 'TEST-SAME-' + uuid4().hex
        self.add_scheme()
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_isins(
              source, scheme_code, identifier_type, isin
            ) VALUES (%s, 1, 'PRIMARY', %s)
        """, (self.source, isin))
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_nav(
              source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
            ) VALUES (%s, 1, '2026-01-02', 10, %s, %s)
        """, (self.source, '2026-01-02 20:00:00', '2026-01-02 20:00:00'))
        self.connection.execute("""
            INSERT INTO public.tigzig_scheme_aaum(
              source, scheme_code, quarter_start, quarter_end, aaum_quarter,
              aaum_lakhs, aaum_cr
            ) VALUES (%s, 1, '2026-01-01', '2026-03-31', 'March-2026', 100, 1)
        """, (self.source,))
        before = self.connection.execute("""
            SELECT s.updated_at, i.updated_at, n.updated_at, a.updated_at
            FROM public.tigzig_schemes s
            JOIN public.tigzig_scheme_isins i USING (source, scheme_code)
            JOIN public.tigzig_scheme_nav n USING (source, scheme_code)
            JOIN public.tigzig_scheme_aaum a USING (source, scheme_code)
            WHERE s.source=%s
        """, (self.source,)).fetchone()
        self.stage_scheme(isin=isin)
        self.stage_nav(1, date(2026, 1, 2), '10', '2026-01-02 20:00:00')
        self.stage_aaum(1, date(2026, 1, 1), date(2026, 3, 31), 'March-2026', '100', '1')
        deltas, mutations = self.apply()
        for dataset in ('meta', 'nav', 'aaum', 'isins'):
            self.assertEqual(deltas[dataset]['UNCHANGED'], 1)
        self.assertTrue(all(
            mutations[key] == 0 for key in (
                'schemes_inserted', 'schemes_updated', 'isins_inserted',
                'isins_updated', 'nav_inserted', 'nav_updated',
                'aaum_inserted', 'aaum_updated',
            )
        ))
        after = self.connection.execute("""
            SELECT s.updated_at, i.updated_at, n.updated_at, a.updated_at
            FROM public.tigzig_schemes s
            JOIN public.tigzig_scheme_isins i USING (source, scheme_code)
            JOIN public.tigzig_scheme_nav n USING (source, scheme_code)
            JOIN public.tigzig_scheme_aaum a USING (source, scheme_code)
            WHERE s.source=%s
        """, (self.source,)).fetchone()
        self.assertEqual(after, before)

    def test_unknown_scheme_reference_is_invalid(self):
        meta = pa.table({
            'scheme_code': [1], 'scheme_name': ['Known'],
            'amc': [self.amc], 'source': [self.source],
        })
        nav = pa.table({
            'scheme_code': [999], 'date': [date(2026, 1, 2)], 'nav': [10.0],
        })
        aaum = pa.table({
            'scheme_code': pa.array([], type=pa.int64()),
            'quarter_start': pa.array([], type=pa.date32()),
            'aaum_quarter_end': pa.array([], type=pa.date32()),
            'aaum_lakhs': pa.array([], type=pa.float64()),
            'aaum_cr': pa.array([], type=pa.float64()),
        })
        report = validate_datasets(meta, nav, aaum)
        self.assertEqual(report.nav.rejected_rows, 1)
        self.assertEqual(report.nav.issues[0].code, 'UNKNOWN_SCHEME')


class SyncRecoveryTests(unittest.TestCase):
    def prepared(self, source, amc, test_id):
        generated = datetime.now(timezone.utc)
        files = {
            'meta': ManifestFile('meta', 'sif_meta.parquet', 1, ('scheme_code',), 1, 'a' * 64),
            'nav': ManifestFile('nav', 'sif_nav.parquet', 1, ('scheme_code',), 1, 'b' * 64),
            'aaum': ManifestFile('aaum', 'sif_aaum.parquet', 0, ('scheme_code',), 1, 'c' * 64),
        }
        manifest = Manifest(generated, files, {
            'generated_at': generated.isoformat(), 'files': [], 'test_id': test_id,
        })
        meta = DatasetValidation('meta', 1, valid_rows=[{
            'source': source, 'scheme_code': 1, 'scheme_name': 'Recovery Fund',
            'amc': amc, 'isin': 'RECOVERY-' + test_id,
        }])
        nav_date = date(2026, 1, 2)
        nav = DatasetValidation('nav', 1, valid_rows=[{
            'scheme_code': 1, 'date': nav_date, 'nav': Decimal('10.5'),
            'upload_ts': datetime(2026, 1, 2, 20, 15),
        }])
        aaum = DatasetValidation('aaum', 0)
        return PreparedSource(
            manifest, ValidationReport(meta, nav, aaum), {},
            {(1, nav_date): '2026-01-02T20:15:00Z'}, [],
        )

    def test_interrupted_transaction_keeps_old_hashes_and_retries_safely(self):
        source = 'RECOVERY-TEST-' + uuid4().hex
        amc = 'Recovery Test AMC ' + uuid4().hex
        test_id = uuid4().hex
        prepared = self.prepared(source, amc, test_id)
        config = DatabaseConfig.from_env().connection_kwargs()
        with psycopg.connect(**config) as connection:
            before = {
                row[0]: row[1:] for row in connection.execute("""
                    SELECT dataset_name, filename, sha256, manifest_generated_at,
                           last_checked_at, last_successful_import_at, row_count,
                           last_import_run_id
                    FROM public.tigzig_sync_file_versions ORDER BY dataset_name
                """).fetchall()
            }

        def interrupt():
            raise RuntimeError('injected interruption after checkpoint write')

        try:
            with self.assertRaisesRegex(RuntimeError, 'injected interruption'):
                sync_prepared(prepared, failpoint=interrupt)
            with psycopg.connect(**config) as connection:
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM public.tigzig_schemes WHERE source=%s',
                    (source,),
                ).fetchone()[0], 0)
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM public.app_performance_cache WHERE source=%s',
                    (source,),
                ).fetchone()[0], 0)
                after_failure = {
                    row[0]: row[1:] for row in connection.execute("""
                        SELECT dataset_name, filename, sha256, manifest_generated_at,
                               last_checked_at, last_successful_import_at, row_count,
                               last_import_run_id
                        FROM public.tigzig_sync_file_versions ORDER BY dataset_name
                    """).fetchall()
                }
                self.assertEqual(after_failure, before)
                failed = connection.execute("""
                    SELECT status FROM public.tigzig_sync_runs
                    WHERE manifest_metadata->>'test_id'=%s
                """, (test_id,)).fetchall()
                self.assertEqual(failed, [('failed',)])

            result = sync_prepared(prepared)
            self.assertEqual((result['inserted'], result['updated']), (2, 0))
            with psycopg.connect(**config) as connection:
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM public.tigzig_schemes WHERE source=%s',
                    (source,),
                ).fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    'SELECT count(*) FROM public.app_performance_cache WHERE source=%s',
                    (source,),
                ).fetchone()[0], 1)
                hashes = dict(connection.execute(
                    'SELECT dataset_name, sha256 FROM public.tigzig_sync_file_versions'
                ).fetchall())
                self.assertEqual(hashes, {'meta': 'a' * 64, 'nav': 'b' * 64, 'aaum': 'c' * 64})
        finally:
            with psycopg.connect(**config) as connection:
                run_ids = [row[0] for row in connection.execute("""
                    SELECT run_id FROM public.tigzig_sync_runs
                    WHERE manifest_metadata->>'test_id'=%s
                """, (test_id,)).fetchall()]
                for dataset, values in before.items():
                    connection.execute("""
                        UPDATE public.tigzig_sync_file_versions SET
                          filename=%s, sha256=%s, manifest_generated_at=%s,
                          last_checked_at=%s, last_successful_import_at=%s,
                          row_count=%s, last_import_run_id=%s
                        WHERE dataset_name=%s
                    """, (*values, dataset))
                connection.execute(
                    'DELETE FROM public.tigzig_ingestion_rejections WHERE run_id=ANY(%s)',
                    (run_ids,),
                )
                connection.execute('DELETE FROM public.app_monthly_returns WHERE source=%s', (source,))
                connection.execute('DELETE FROM public.app_performance_cache WHERE source=%s', (source,))
                connection.execute('DELETE FROM public.tigzig_scheme_nav WHERE source=%s', (source,))
                connection.execute('DELETE FROM public.tigzig_scheme_aaum WHERE source=%s', (source,))
                connection.execute('DELETE FROM public.tigzig_scheme_isins WHERE source=%s', (source,))
                connection.execute('DELETE FROM public.tigzig_schemes WHERE source=%s', (source,))
                connection.execute(
                    'DELETE FROM public.tigzig_fund_houses WHERE original_amc_name=%s',
                    (amc,),
                )
                connection.execute(
                    'DELETE FROM public.tigzig_sync_runs WHERE run_id=ANY(%s)',
                    (run_ids,),
                )


if __name__ == '__main__':
    unittest.main()
