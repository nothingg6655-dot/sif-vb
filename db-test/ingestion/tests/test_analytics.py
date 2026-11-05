"""Milestone 7 affected-scheme analytics refresh tests."""
from decimal import Decimal
import unittest
from uuid import uuid4

import psycopg

from database.migrate import verify_target
from ingestion.tigzig.analytics import AFFECTED_TABLE, refresh_affected_analytics
from ingestion.tigzig.config import DatabaseConfig


class AnalyticsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        verify_target(self.connection)
        self.source = 'ANALYTICS-' + uuid4().hex
        house = self.connection.execute(
            'INSERT INTO public.tigzig_fund_houses(original_amc_name) VALUES (%s) RETURNING fund_house_id',
            ('Analytics AMC ' + uuid4().hex,),
        ).fetchone()[0]
        self.connection.execute("""
          INSERT INTO public.tigzig_schemes(source,scheme_code,fund_house_id,scheme_name)
          VALUES (%s,1,%s,'Analytics Test Fund')
        """, (self.source, house))
        self.connection.cursor().executemany("""
          INSERT INTO public.tigzig_scheme_nav(source,scheme_code,nav_date,nav)
          VALUES (%s,1,%s,%s)
        """, [
            (self.source, '2026-01-31', '100'),
            (self.source, '2026-02-28', '110'),
            (self.source, '2026-03-15', '121'),
        ])
        self.connection.execute(f"""
          CREATE TEMP TABLE {AFFECTED_TABLE} ON COMMIT DROP AS
          SELECT %s::text source,1::bigint scheme_code
        """, (self.source,), prepare=False)

    def test_refresh_replaces_only_affected_scheme_read_models(self):
        result = refresh_affected_analytics(self.connection)
        self.assertEqual(result, {
            'affected_schemes': 1, 'monthly_rows': 2, 'performance_rows': 1,
        })
        monthly = self.connection.execute("""
          SELECT month_end,absolute_return_pct,is_partial_month
          FROM public.app_monthly_returns WHERE source=%s ORDER BY month_end
        """, (self.source,)).fetchall()
        self.assertEqual(monthly[0][1], Decimal('10.00000000'))
        self.assertFalse(monthly[0][2])
        self.assertEqual(monthly[1][1], Decimal('10.00000000'))
        self.assertTrue(monthly[1][2])
        performance = self.connection.execute("""
          SELECT latest_nav,daily_return_pct,one_month_return_pct,
                 three_month_return_pct,since_inception_return_pct
          FROM public.app_performance_cache WHERE source=%s
        """, (self.source,)).fetchone()
        self.assertEqual(performance, (
            Decimal('121.0000000000'), Decimal('10.00000000'),
            Decimal('21.00000000'), Decimal('21.00000000'), Decimal('21.00000000'),
        ))


if __name__ == '__main__':
    unittest.main()
