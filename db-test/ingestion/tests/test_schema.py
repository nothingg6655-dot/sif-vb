"""Milestone 1 integration tests. Every inserted row is rolled back."""
from decimal import Decimal
import unittest
from uuid import uuid4

import psycopg

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig

TABLES = {
    'tigzig_fund_houses', 'tigzig_schemes', 'tigzig_scheme_isins',
    'tigzig_scheme_nav', 'tigzig_scheme_aaum', 'tigzig_sync_runs',
    'tigzig_sync_file_versions', 'tigzig_ingestion_rejections',
    'app_funds', 'app_tigzig_fund_mapping', 'app_schema_migrations',
    'app_benchmarks', 'app_benchmark_history',
}

SIF_DOMAIN_TABLES = {
    'sif_amcs', 'sif_schemes', 'sif_scheme_isins', 'sif_nav_history',
    'sif_aaum_history', 'sif_amc_aliases', 'sif_strategies',
    'sif_strategy_scheme_links', 'sif_strategy_external_identifiers',
    'sif_data_sources', 'sif_ingestion_runs', 'sif_ingestion_files',
    'sif_ingestion_rejections', 'sif_data_quality_issues', 'sif_benchmarks',
    'sif_benchmark_history', 'sif_factsheet_documents',
    'sif_factsheet_reports', 'sif_factsheet_pages',
    'sif_report_strategy_profile', 'sif_fund_managers',
    'sif_report_fund_managers', 'sif_report_aum', 'sif_report_nav',
    'sif_report_expense_ratios', 'sif_instruments', 'sif_report_holdings',
    'sif_report_allocations', 'sif_report_statistical_indicators',
    'sif_report_risk_bands', 'sif_report_published_performance',
    'sif_report_idcw_history', 'sif_report_notes',
    'sif_report_external_field_links', 'sif_report_field_availability',
    'sif_report_extension_data', 'sif_scheme_idcw_events',
    'sif_scheme_return_cache', 'sif_scheme_risk_cache', 'sif_site_users',
    'sif_user_watchlists', 'sif_user_watchlist_items',
    'sif_user_comparisons', 'sif_user_comparison_items',
}

SIF_DOMAIN_VIEWS = {
    'vw_sif_scheme_latest', 'vw_sif_verified_strategy_schemes',
    'vw_sif_strategy_latest_report', 'vw_sif_strategy_directory',
    'vw_sif_source_freshness',
}


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        verify_target(self.connection)  # Opens the outer transaction.
        self.source = 'TEST-' + uuid4().hex
        self.house = self.connection.execute(
            'INSERT INTO public.tigzig_fund_houses(original_amc_name) VALUES (%s) RETURNING fund_house_id',
            ('Test AMC ' + uuid4().hex,),
        ).fetchone()[0]

    def scheme(self, code=1):
        self.connection.execute(
            'INSERT INTO public.tigzig_schemes(source, scheme_code, fund_house_id, scheme_name) VALUES (%s, %s, %s, %s)',
            (self.source, code, self.house, f'Test Scheme {code}'),
        )
        return code

    def nav(self, code=1, value='10.1234567890'):
        self.connection.execute(
            'INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav) VALUES (%s, %s, %s, %s)',
            (self.source, code, '2026-01-02', value),
        )

    def expect_error(self, error_type, statement, parameters):
        with self.assertRaises(error_type):
            with self.connection.transaction():  # Savepoint, not a commit.
                self.connection.execute(statement, parameters)

    def run_record(self):
        return self.connection.execute(
            "INSERT INTO public.tigzig_sync_runs(trigger_type, job_type) VALUES ('test', 'sync') RETURNING run_id"
        ).fetchone()[0]

    def fund(self):
        return self.connection.execute(
            'INSERT INTO public.app_funds(canonical_name, slug) VALUES (%s, %s) RETURNING fund_id',
            ('Test Fund', 'test-' + uuid4().hex),
        ).fetchone()[0]

    def sif_identity(self):
        token = uuid4().hex
        amc_id = self.connection.execute(
            'INSERT INTO public.sif_amcs(amc_name) VALUES (%s) RETURNING amc_id',
            ('Test SIF AMC ' + token,),
        ).fetchone()[0]
        scheme_code = int(token[:7], 16)
        self.connection.execute("""
            INSERT INTO public.sif_schemes(
              scheme_code,temp_sif_code,amc_id,scheme_name,scheme_plan,scheme_option
            ) VALUES (%s,%s,%s,%s,'Direct','Growth')
        """, (scheme_code, 'TEST-' + token[:20], amc_id, 'Test SIF Scheme ' + token))
        strategy_id = self.connection.execute("""
            INSERT INTO public.sif_strategies(amc_id,strategy_name,slug)
            VALUES (%s,%s,%s) RETURNING strategy_id
        """, (amc_id, 'Test SIF Strategy ' + token, 'test-' + token)).fetchone()[0]
        return token, amc_id, scheme_code, strategy_id

    def test_required_tables_are_only_in_public_and_have_primary_keys(self):
        rows = self.connection.execute("""
            SELECT n.nspname, c.relname, count(p.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_constraint p ON p.conrelid = c.oid AND p.contype = 'p'
            WHERE c.relname = ANY(%s) AND c.relkind IN ('r', 'p')
            GROUP BY n.nspname, c.relname
        """, (sorted(TABLES),)).fetchall()
        self.assertEqual({r[1] for r in rows}, TABLES)
        self.assertTrue(all(r[0] == 'public' and r[2] == 1 for r in rows))

    def test_expected_foreign_keys(self):
        rows = self.connection.execute("""
            SELECT t.relname, pg_get_constraintdef(k.oid)
            FROM pg_constraint k JOIN pg_class t ON t.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public' AND k.contype = 'f' AND t.relname = ANY(%s)
        """, (sorted(TABLES),)).fetchall()
        self.assertEqual(len(rows), 9)
        composite = [r for r in rows if r[0] in {
            'tigzig_scheme_isins', 'tigzig_scheme_nav', 'tigzig_scheme_aaum', 'app_tigzig_fund_mapping'
        }]
        self.assertEqual(len([r for r in composite if 'FOREIGN KEY (source, scheme_code)' in r[1]]), 4)

    def test_duplicate_scheme_rejected(self):
        self.scheme()
        self.expect_error(psycopg.errors.UniqueViolation,
            'INSERT INTO public.tigzig_schemes(source, scheme_code, fund_house_id, scheme_name) VALUES (%s, 1, %s, %s)',
            (self.source, self.house, 'Duplicate'))

    def test_source_is_part_of_permanent_scheme_identity(self):
        self.scheme()
        self.connection.execute(
            'INSERT INTO public.tigzig_schemes(source, scheme_code, fund_house_id, scheme_name) VALUES (%s, 1, %s, %s)',
            (self.source + '-other', self.house, 'Other source'))
        count = self.connection.execute('SELECT count(*) FROM public.tigzig_schemes WHERE fund_house_id = %s', (self.house,)).fetchone()[0]
        self.assertEqual(count, 2)

    def test_duplicate_nav_rejected(self):
        self.scheme()
        self.nav()
        self.expect_error(psycopg.errors.UniqueViolation,
            'INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav) VALUES (%s, 1, %s, 11)',
            (self.source, '2026-01-02'))

    def test_nav_requires_positive_finite_numeric(self):
        self.scheme()
        for value in ('0', '-1', 'NaN'):
            with self.subTest(value=value):
                self.expect_error(psycopg.errors.CheckViolation,
                    'INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav) VALUES (%s, 1, %s, %s)',
                    (self.source, '2026-01-02', value))

    def test_numeric_nav_and_raw_ist_publication_preserved(self):
        self.scheme()
        self.nav()
        raw = '2026-01-02T20:15:00Z'
        self.connection.execute(
            'UPDATE public.tigzig_scheme_nav SET upload_ts_raw=%s, upload_ts_ist=%s WHERE source=%s',
            (raw, '2026-01-02 20:15:00', self.source))
        value, timestamp, parsed = self.connection.execute(
            'SELECT nav, upload_ts_raw, upload_ts_ist FROM public.tigzig_scheme_nav WHERE source=%s', (self.source,)).fetchone()
        self.assertEqual(value, Decimal('10.1234567890'))
        self.assertEqual(timestamp, raw)
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(parsed.hour, 20)

    def test_child_tables_reject_unknown_scheme_or_wrong_source(self):
        self.scheme()
        statements = [
            ('INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav) VALUES (%s, 1, %s, 10)', ('2026-01-02',)),
            ("INSERT INTO public.tigzig_scheme_isins(source, scheme_code, identifier_type, isin) VALUES (%s, 1, 'PRIMARY', %s)", ('TEST-ISIN',)),
            ('INSERT INTO public.tigzig_scheme_aaum(source, scheme_code, quarter_start, quarter_end, aaum_quarter, aaum_lakhs, aaum_cr) VALUES (%s, 1, %s, %s, %s, 100, 1)', ('2026-01-01', '2026-03-31', 'March-2026')),
        ]
        for statement, rest in statements:
            with self.subTest(statement=statement):
                self.expect_error(psycopg.errors.ForeignKeyViolation, statement, (self.source + '-unknown', *rest))

    def test_scheme_requires_existing_fund_house(self):
        self.expect_error(psycopg.errors.ForeignKeyViolation,
            'INSERT INTO public.tigzig_schemes(source, scheme_code, fund_house_id, scheme_name) VALUES (%s, 1, -1, %s)',
            (self.source, 'Orphan Scheme'))

    def test_both_isins_belong_to_one_scheme(self):
        self.scheme()
        for kind, isin in [('PRIMARY', 'PRIMARY-' + self.source), ('SECONDARY', 'SECONDARY-' + self.source)]:
            self.connection.execute(
                'INSERT INTO public.tigzig_scheme_isins(source, scheme_code, identifier_type, isin) VALUES (%s, 1, %s, %s)',
                (self.source, kind, isin))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM public.tigzig_scheme_isins WHERE source=%s', (self.source,)).fetchone()[0], 2)
        self.expect_error(psycopg.errors.UniqueViolation,
            "INSERT INTO public.tigzig_scheme_isins(source, scheme_code, identifier_type, isin) VALUES (%s, 1, 'PRIMARY', %s)",
            (self.source, 'THIRD-' + self.source))

    def test_isin_cannot_map_two_schemes(self):
        self.scheme(1)
        self.scheme(2)
        isin = 'UNIQUE-' + self.source
        self.connection.execute(
            "INSERT INTO public.tigzig_scheme_isins(source, scheme_code, identifier_type, isin) VALUES (%s, 1, 'PRIMARY', %s)", (self.source, isin))
        self.expect_error(psycopg.errors.UniqueViolation,
            "INSERT INTO public.tigzig_scheme_isins(source, scheme_code, identifier_type, isin) VALUES (%s, 2, 'PRIMARY', %s)", (self.source, isin))

    def test_duplicate_aaum_rejected_and_units_checked(self):
        self.scheme()
        statement = 'INSERT INTO public.tigzig_scheme_aaum(source, scheme_code, quarter_start, quarter_end, aaum_quarter, aaum_lakhs, aaum_cr) VALUES (%s, 1, %s, %s, %s, %s, %s)'
        valid = (self.source, '2026-01-01', '2026-03-31', 'March-2026', '100.1234', '1.001234')
        self.connection.execute(statement, valid)
        self.expect_error(psycopg.errors.UniqueViolation, statement, valid)
        for start, lakhs, cr in [('2026-07-01', '100', '1'), ('2026-04-01', '100', '2'), ('2026-04-01', '-100', '-1'), ('2026-04-01', 'NaN', 'NaN')]:
            with self.subTest(start=start, lakhs=lakhs, cr=cr):
                self.expect_error(psycopg.errors.CheckViolation, statement,
                    (self.source, start, '2026-06-30', 'June-2026', lakhs, cr))

    def test_optional_temp_fields_are_not_keys(self):
        self.scheme(1)
        self.scheme(2)
        self.connection.execute('UPDATE public.tigzig_schemes SET temp_sif_code=%s, source_metadata=%s::jsonb WHERE source=%s',
            ('temporary-shared', '{"optional_future_column": "kept"}', self.source))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM public.tigzig_schemes WHERE source=%s', (self.source,)).fetchone()[0], 2)

    def test_fund_variants_map_to_one_verified_fund(self):
        fund = self.fund()
        statement = "INSERT INTO public.app_tigzig_fund_mapping(fund_id, source, scheme_code, verification_method, verification_evidence, verified_by, verified_at) VALUES (%s, %s, %s, 'manual', %s::jsonb, 'test', now())"
        for code, plan, option in [(1, 'Direct', 'Growth'), (2, 'Regular', 'Growth'), (3, 'Direct', 'IDCW'), (4, 'Regular', 'IDCW')]:
            self.scheme(code)
            self.connection.execute('UPDATE public.tigzig_schemes SET scheme_plan=%s, scheme_option=%s WHERE source=%s AND scheme_code=%s',
                (plan, option, self.source, code))
            self.connection.execute(statement, (fund, self.source, code, '{"review": "M1 constraint test"}'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM public.app_tigzig_fund_mapping WHERE fund_id=%s', (fund,)).fetchone()[0], 4)
        self.expect_error(psycopg.errors.UniqueViolation, statement, (self.fund(), self.source, 1, '{"review": "duplicate"}'))

    def test_mapping_requires_valid_fund_scheme_and_verification(self):
        self.scheme()
        fund = self.fund()
        statement = "INSERT INTO public.app_tigzig_fund_mapping(fund_id, source, scheme_code, verification_method, verification_evidence, verified_by, verified_at) VALUES (%s, %s, %s, 'manual', %s::jsonb, 'test', now())"
        self.expect_error(psycopg.errors.ForeignKeyViolation, statement, (-1, self.source, 1, '{"review": "test"}'))
        self.expect_error(psycopg.errors.ForeignKeyViolation, statement, (fund, self.source, 999, '{"review": "test"}'))
        self.expect_error(psycopg.errors.CheckViolation, statement, (fund, self.source, 1, '{}'))

    def test_slug_unique_and_valid(self):
        fund = self.fund()
        slug = self.connection.execute('SELECT slug FROM public.app_funds WHERE fund_id=%s', (fund,)).fetchone()[0]
        self.expect_error(psycopg.errors.UniqueViolation,
            'INSERT INTO public.app_funds(canonical_name, slug) VALUES (%s, %s)', ('Duplicate', slug))
        self.expect_error(psycopg.errors.CheckViolation,
            'INSERT INTO public.app_funds(canonical_name, slug) VALUES (%s, %s)', ('Bad slug', 'Not a slug'))

    def test_audit_foreign_keys_and_success_checkpoint(self):
        run = self.run_record()
        self.connection.execute('INSERT INTO public.tigzig_ingestion_rejections(run_id, dataset_name, raw_record, rejection_reason) VALUES (%s, %s, %s::jsonb, %s)',
            (run, 'nav', '{"nav": "invalid"}', 'Invalid numeric value'))
        self.expect_error(psycopg.errors.ForeignKeyViolation,
            'INSERT INTO public.tigzig_ingestion_rejections(run_id, dataset_name, raw_record, rejection_reason) VALUES (%s, %s, %s::jsonb, %s)',
            (uuid4(), 'nav', '{}', 'Unknown run'))
        self.expect_error(psycopg.errors.ForeignKeyViolation,
            'UPDATE public.tigzig_sync_file_versions SET sha256=%s, manifest_generated_at=now(), last_successful_import_at=now(), row_count=1, last_import_run_id=%s WHERE dataset_name=%s',
            ('a' * 64, uuid4(), 'nav'))
        self.expect_error(psycopg.errors.CheckViolation,
            "UPDATE public.tigzig_sync_runs SET status='succeeded' WHERE run_id=%s", (run,))
        self.connection.execute("UPDATE public.tigzig_sync_runs SET status='succeeded', completed_at=now(), execution_duration_seconds=0 WHERE run_id=%s", (run,))
        # Use a savepoint and reset only this test row if a real checkpoint exists.
        # Tests must also be repeatable after later milestones populate datasets.
        self.connection.execute('INSERT INTO public.tigzig_sync_file_versions(dataset_name, filename, sha256, manifest_generated_at, last_successful_import_at, row_count, last_import_run_id) VALUES (%s, %s, %s, now(), now(), 1, %s) ON CONFLICT (dataset_name) DO UPDATE SET sha256=EXCLUDED.sha256, manifest_generated_at=EXCLUDED.manifest_generated_at, last_successful_import_at=EXCLUDED.last_successful_import_at, row_count=EXCLUDED.row_count, last_import_run_id=EXCLUDED.last_import_run_id',
            ('nav', 'sif_nav.parquet', 'b' * 64, run))
        self.expect_error(psycopg.errors.CheckViolation,
            "UPDATE public.tigzig_sync_file_versions SET sha256='bad-hash' WHERE dataset_name='nav'", ())

    def test_complete_sif_domain_schema_is_installed(self):
        rows = self.connection.execute("""
            SELECT n.nspname, c.relname, count(p.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_constraint p ON p.conrelid = c.oid AND p.contype = 'p'
            WHERE c.relname = ANY(%s) AND c.relkind IN ('r', 'p')
            GROUP BY n.nspname, c.relname
        """, (sorted(SIF_DOMAIN_TABLES),)).fetchall()
        self.assertEqual({row[1] for row in rows}, SIF_DOMAIN_TABLES)
        self.assertTrue(all(row[0] == 'public' and row[2] == 1 for row in rows))
        views = self.connection.execute("""
            SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='v' AND c.relname=ANY(%s)
        """, (sorted(SIF_DOMAIN_VIEWS),)).fetchall()
        self.assertEqual({row[0] for row in views}, SIF_DOMAIN_VIEWS)

    def test_factsheet_schema_preserves_raw_json_and_one_primary_report_per_month(self):
        raw_type = self.connection.execute("""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema='public' AND table_name='sif_factsheet_reports'
              AND column_name='raw_extraction_json'
        """).fetchone()
        self.assertEqual(raw_type, ('jsonb',))
        index = self.connection.execute("""
            SELECT indexdef FROM pg_indexes
            WHERE schemaname='public' AND indexname='uq_sif_primary_strategy_month'
        """).fetchone()
        self.assertIsNotNone(index)
        self.assertIn('WHERE is_primary', index[0])

    def test_sif_source_registry_contains_declared_authorities(self):
        sources = self.connection.execute(
            'SELECT source_code FROM public.sif_data_sources ORDER BY source_code'
        ).fetchall()
        self.assertTrue({'BENCHMARK', 'DERIVED', 'OFFICIAL_FACTSHEET', 'TIGZIG'}
                        <= {row[0] for row in sources})

    def test_strategy_scheme_link_rejects_cross_amc_mapping(self):
        _, _, scheme_code, _ = self.sif_identity()
        token = uuid4().hex
        other_amc = self.connection.execute(
            'INSERT INTO public.sif_amcs(amc_name) VALUES (%s) RETURNING amc_id',
            ('Other SIF AMC ' + token,),
        ).fetchone()[0]
        other_strategy = self.connection.execute("""
            INSERT INTO public.sif_strategies(amc_id,strategy_name,slug)
            VALUES (%s,%s,%s) RETURNING strategy_id
        """, (other_amc, 'Other Strategy ' + token, 'other-' + token)).fetchone()[0]
        self.expect_error(psycopg.errors.RaiseException, """
            INSERT INTO public.sif_strategy_scheme_links(scheme_code,strategy_id)
            VALUES (%s,%s)
        """, (scheme_code, other_strategy))

    def test_primary_factsheet_and_normalized_row_constraints(self):
        token, amc_id, _, strategy_id = self.sif_identity()
        reports = []
        for suffix in ('a', 'b'):
            document_id = self.connection.execute("""
                INSERT INTO public.sif_factsheet_documents(
                  amc_id,sha256,reporting_month,official_only
                ) VALUES (%s,%s,'2026-09-01',TRUE) RETURNING document_id
            """, (amc_id, (token + suffix * 32)[:64])).fetchone()[0]
            if not reports:
                reports.append(self.connection.execute("""
                    INSERT INTO public.sif_factsheet_reports(
                      document_id,strategy_id,report_month,published_scheme_name,
                      is_primary,validation_status,raw_extraction_json
                    ) VALUES (%s,%s,'2026-09-01',%s,TRUE,'PASSED',%s::jsonb)
                    RETURNING report_id
                """, (document_id, strategy_id, 'Published ' + token,
                       '{"Portfolio": []}')).fetchone()[0])
            else:
                self.expect_error(psycopg.errors.UniqueViolation, """
                    INSERT INTO public.sif_factsheet_reports(
                      document_id,strategy_id,report_month,published_scheme_name,
                      is_primary,validation_status
                    ) VALUES (%s,%s,'2026-09-01',%s,TRUE,'PASSED')
                """, (document_id, strategy_id, 'Second ' + token))
        report_id = reports[0]
        self.expect_error(psycopg.errors.CheckViolation, """
            INSERT INTO public.sif_report_holdings(
              report_id,row_number,published_instrument_name
            ) VALUES (%s,0,'Invalid row')
        """, (report_id,))
        self.expect_error(psycopg.errors.CheckViolation, """
            INSERT INTO public.sif_report_allocations(
              report_id,allocation_family,bucket_label,exposure_percent
            ) VALUES (%s,'UNDECLARED','Invalid allocation',100)
        """, (report_id,))

    def test_benchmark_history_requires_verified_positive_index_levels(self):
        benchmark_id = 'test' + uuid4().hex
        self.connection.execute("""
            INSERT INTO public.app_benchmarks(
              benchmark_id,label,subtitle,index_variant,source,source_identifier,
              coverage_status,metadata)
            VALUES (%s,'Test Index','Test','TRI','TEST','TEST-TRI','available',
                    '{"evidence":"schema test"}'::jsonb)
        """, (benchmark_id,))
        self.connection.execute("""
            INSERT INTO public.app_benchmark_history(
              benchmark_id,observation_date,index_level,source_version)
            VALUES (%s,'2026-01-02',123.4567890123,'fixture-v1')
        """, (benchmark_id,))
        value = self.connection.execute(
            'SELECT index_level FROM public.app_benchmark_history WHERE benchmark_id=%s',
            (benchmark_id,),
        ).fetchone()[0]
        self.assertEqual(value, Decimal('123.4567890123'))
        self.expect_error(psycopg.errors.UniqueViolation,
            'INSERT INTO public.app_benchmark_history(benchmark_id,observation_date,index_level) VALUES (%s,%s,1)',
            (benchmark_id, '2026-01-02'))
        self.expect_error(psycopg.errors.CheckViolation,
            'INSERT INTO public.app_benchmark_history(benchmark_id,observation_date,index_level) VALUES (%s,%s,0)',
            (benchmark_id, '2026-01-03'))

    def test_available_benchmark_requires_source_and_declared_index_variant(self):
        self.expect_error(psycopg.errors.CheckViolation, """
            INSERT INTO public.app_benchmarks(
              benchmark_id,label,subtitle,index_variant,coverage_status)
            VALUES (%s,'Unknown Index','Test','UNKNOWN','available')
        """, ('test' + uuid4().hex,))

    def test_live_nav_rows_not_committed_by_constraint_checks(self):
        self.scheme()
        self.nav()
        self.expect_error(psycopg.errors.UniqueViolation,
            'INSERT INTO public.tigzig_scheme_nav(source, scheme_code, nav_date, nav) VALUES (%s, 1, %s, 12)',
            (self.source, '2026-01-02'))
        self.assertEqual(self.connection.execute('SELECT count(*) FROM public.tigzig_scheme_nav WHERE source=%s', (self.source,)).fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
