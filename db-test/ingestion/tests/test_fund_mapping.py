"""Milestone 6 deterministic identity reconciliation tests."""
import unittest
from uuid import uuid4

import psycopg

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig
from ingestion.tigzig.fund_mapping import (
    SIF360_REFERENCE_FUND_NAMES, Scheme, classify_schemes, normalize_name,
    reconcile_funds,
)


class FundMappingTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        verify_target(self.connection)

    def test_current_snapshot_is_fully_mapped_by_verified_exact_keys(self):
        schemes, mapped, funds, ticker = self.connection.execute("""
          SELECT (SELECT count(*) FROM public.tigzig_schemes),
                 (SELECT count(*) FROM public.app_tigzig_fund_mapping),
                 (SELECT count(*) FROM public.app_funds WHERE status='active'),
                 (SELECT count(*) FROM public.app_ticker_config
                  WHERE selection_verified AND is_visible)
        """).fetchone()
        self.assertEqual((schemes, mapped, funds, ticker), (126, 126, 34, 34))
        self.assertEqual(self.connection.execute("""
          SELECT count(*) FROM public.app_tigzig_fund_mapping m
          JOIN public.tigzig_schemes s USING(source,scheme_code)
          WHERE m.verification_method <> 'amc-category-isin-v1'
             OR jsonb_array_length(m.verification_evidence->'verified_identifiers') = 0
        """).fetchone()[0], 0)

    def test_ticker_uses_one_verified_direct_growth_scheme_per_fund(self):
        invalid = self.connection.execute("""
          SELECT count(*) FROM public.app_ticker_config t
          JOIN public.tigzig_schemes s
            ON (s.source,s.scheme_code)=(t.preferred_source,t.preferred_scheme_code)
          WHERE NOT t.selection_verified OR s.scheme_plan <> 'Direct'
             OR s.scheme_option <> 'Growth'
        """).fetchone()[0]
        self.assertEqual(invalid, 0)

    def test_performance_ids_use_one_verified_regular_growth_series(self):
        rows = self.connection.execute("""
          SELECT c.reference_fund_id,f.canonical_name,s.scheme_plan,s.scheme_option,
                 c.mapping_confidence,c.selection_verified,
                 count(i.isin) AS identifier_count
          FROM public.app_performance_config c
          JOIN public.app_funds f USING(fund_id)
          JOIN public.tigzig_schemes s
            ON (s.source,s.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
          LEFT JOIN public.tigzig_scheme_isins i USING(source,scheme_code)
          GROUP BY c.reference_fund_id,f.canonical_name,s.scheme_plan,s.scheme_option,
                   c.mapping_confidence,c.selection_verified
          ORDER BY c.reference_fund_id
        """).fetchall()
        self.assertEqual(len(rows), 34)
        self.assertEqual([row[0] for row in rows], list(range(1, 35)))
        for reference_id, name, plan, option, confidence, verified, identifiers in rows:
            self.assertEqual(name, SIF360_REFERENCE_FUND_NAMES[reference_id])
            self.assertEqual((plan, option), ('Regular', 'Growth'))
            self.assertEqual((confidence, verified), ('high', True))
            self.assertGreater(identifiers, 0)

    def test_reconciliation_is_idempotent(self):
        before = self.connection.execute("""
          SELECT max(updated_at), count(*) FROM public.app_tigzig_fund_mapping
        """).fetchone()
        result = reconcile_funds(self.connection)
        after = self.connection.execute("""
          SELECT max(updated_at), count(*) FROM public.app_tigzig_fund_mapping
        """).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(result['review_candidates_this_run'], 0)

    def test_missing_exact_family_is_reviewed_not_fuzzy_mapped(self):
        scheme = Scheme('TEST', 1, 1, 'Example SIF',
                        'Example Equity Long Short Fund Direct Growth', None,
                        'Direct', 'Growth', None, None, ('IN0000000001',))
        groups, reviews = classify_schemes([scheme])
        self.assertEqual(groups, {})
        self.assertEqual(reviews[0][1], 'missing_fund_family')

    def test_conflicting_exact_labels_are_reviewed(self):
        common = ('TEST', 1, 'Example SIF')
        first = Scheme(common[0], 1, common[1], common[2],
                       'Example Equity Long-Short Fund Direct Growth',
                       'Equity Long-Short Fund', 'Direct', 'Growth', None, None,
                       ('IN0000000001',))
        second = Scheme(common[0], 2, common[1], common[2],
                        'Example Equity Long Short Fund Regular Growth',
                        'Equity Long Short Fund', 'Regular', 'Growth', None, None,
                        ('IN0000000002',))
        groups, reviews = classify_schemes([first, second])
        self.assertEqual(groups, {})
        self.assertEqual({item[1] for item in reviews}, {'conflicting_fund_family_labels'})

    def test_ticker_cannot_reference_unmapped_scheme(self):
        row = self.connection.execute("""
          SELECT fund_id,preferred_source,preferred_scheme_code
          FROM public.app_ticker_config ORDER BY fund_id LIMIT 1
        """).fetchone()
        other = self.connection.execute("""
          SELECT source,scheme_code FROM public.app_tigzig_fund_mapping
          WHERE fund_id <> %s LIMIT 1
        """, (row[0],)).fetchone()
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connection.transaction():
                self.connection.execute("""
                  UPDATE public.app_ticker_config
                  SET preferred_source=%s,preferred_scheme_code=%s WHERE fund_id=%s
                """, (other[0], other[1], row[0]))

    def test_external_identifier_is_globally_unique(self):
        identifier = self.connection.execute(
            'SELECT namespace,identifier_value,fund_id,source,source_scheme_code FROM public.app_external_identifiers LIMIT 1'
        ).fetchone()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.connection.transaction():
                self.connection.execute("""
                  INSERT INTO public.app_external_identifiers(
                    fund_id,namespace,identifier_value,source,source_scheme_code)
                  VALUES (%s,%s,%s,%s,%s)
                """, (identifier[2], identifier[0], identifier[1], identifier[3], identifier[4]))

    def test_name_normalization_is_punctuation_and_case_stable(self):
        self.assertEqual(normalize_name('Equity Long-Short Fund'),
                         normalize_name('EQUITY long short fund'))


if __name__ == '__main__':
    unittest.main()
