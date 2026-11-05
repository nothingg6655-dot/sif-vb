"""Refresh calculated NAV read models for only the affected source schemes."""
import argparse
import json

import psycopg

from database.migrate import verify_target
from .config import DatabaseConfig


AFFECTED_TABLE = 'stg_affected_nav_schemes'


def create_affected_from_staging(connection: psycopg.Connection, *, only_new: bool) -> int:
    comparison = 't.scheme_code IS NULL' if only_new else """
      t.scheme_code IS NULL OR ROW(t.nav,t.upload_ts_raw,t.upload_ts_ist)
        IS DISTINCT FROM ROW(s.nav,s.upload_ts_raw,s.upload_ts_ist)
    """
    connection.execute(f"""
      CREATE TEMP TABLE {AFFECTED_TABLE} ON COMMIT DROP AS
      SELECT DISTINCT s.source,s.scheme_code
      FROM stg_tigzig_nav s
      LEFT JOIN public.tigzig_scheme_nav t
        ON (t.source,t.scheme_code,t.nav_date)=(s.source,s.scheme_code,s.nav_date)
      WHERE {comparison}
    """, prepare=False)
    connection.execute(
        f'CREATE UNIQUE INDEX ON {AFFECTED_TABLE}(source,scheme_code)', prepare=False
    )
    return connection.execute(
        f'SELECT count(*)::integer FROM {AFFECTED_TABLE}', prepare=False
    ).fetchone()[0]


def refresh_affected_analytics(
    connection: psycopg.Connection,
    affected_table: str = AFFECTED_TABLE,
) -> dict[str, int]:
    """Replace derived rows for keys in a connection-local affected-key table."""
    affected = connection.execute(
        f'SELECT count(*)::integer FROM {affected_table}', prepare=False
    ).fetchone()[0]
    connection.execute(f"""
      DELETE FROM public.app_monthly_returns r
      USING {affected_table} a
      WHERE (r.source,r.scheme_code)=(a.source,a.scheme_code)
    """, prepare=False)
    monthly = connection.execute(f"""
      WITH months AS (
        SELECT DISTINCT n.source,n.scheme_code,date_trunc('month',n.nav_date)::date month_start
        FROM public.tigzig_scheme_nav n JOIN {affected_table} a USING(source,scheme_code)
      ), observations AS (
        SELECT m.*,
               e.nav_date end_date,e.nav end_nav,
               COALESCE(p.nav_date,f.nav_date) start_date,
               COALESCE(p.nav,f.nav) start_nav,
               p.nav_date IS NULL no_prior,
               x.latest_date
        FROM months m
        CROSS JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(m.source,m.scheme_code)
            AND nav_date >= m.month_start
            AND nav_date < (m.month_start + interval '1 month')
          ORDER BY nav_date DESC LIMIT 1
        ) e
        LEFT JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(m.source,m.scheme_code)
            AND nav_date < m.month_start
          ORDER BY nav_date DESC LIMIT 1
        ) p ON true
        LEFT JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(m.source,m.scheme_code)
            AND nav_date >= m.month_start
            AND nav_date < (m.month_start + interval '1 month')
          ORDER BY nav_date LIMIT 1
        ) f ON true
        CROSS JOIN LATERAL (
          SELECT max(nav_date) latest_date FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(m.source,m.scheme_code)
        ) x
      ), inserted AS (
        INSERT INTO public.app_monthly_returns(
          source,scheme_code,month_end,start_nav_date,end_nav_date,
          start_nav,end_nav,absolute_return_pct,is_partial_month,calculated_at
        )
        SELECT source,scheme_code,
               (month_start + interval '1 month - 1 day')::date,
               start_date,end_date,start_nav,end_nav,
               round(((end_nav/start_nav)-1)*100,8),
               no_prior OR (
                 date_trunc('month',latest_date)::date=month_start
                 AND latest_date < (month_start + interval '1 month - 1 day')::date
               ), now()
        FROM observations
        WHERE start_date < end_date
        RETURNING 1
      ) SELECT count(*)::integer FROM inserted
    """, prepare=False).fetchone()[0]

    connection.execute(f"""
      DELETE FROM public.app_performance_cache p
      USING {affected_table} a
      WHERE (p.source,p.scheme_code)=(a.source,a.scheme_code)
    """, prepare=False)
    performance = connection.execute(f"""
      WITH observations AS (
        SELECT a.source,a.scheme_code,
               e.nav_date end_date,e.nav end_nav,
               prev.nav_date previous_date,prev.nav previous_nav,
               first.nav_date inception_date,first.nav inception_nav,
               COALESCE(one.nav_date,first.nav_date) one_date,
               COALESCE(one.nav,first.nav) one_nav,
               COALESCE(three.nav_date,first.nav_date) three_date,
               COALESCE(three.nav,first.nav) three_nav
        FROM {affected_table} a
        CROSS JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(a.source,a.scheme_code)
          ORDER BY nav_date DESC LIMIT 1
        ) e
        LEFT JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(a.source,a.scheme_code) AND nav_date < e.nav_date
          ORDER BY nav_date DESC LIMIT 1
        ) prev ON true
        CROSS JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(a.source,a.scheme_code)
          ORDER BY nav_date LIMIT 1
        ) first
        LEFT JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(a.source,a.scheme_code)
            AND nav_date <= (e.nav_date - interval '1 month')::date
          ORDER BY nav_date DESC LIMIT 1
        ) one ON true
        LEFT JOIN LATERAL (
          SELECT nav_date,nav FROM public.tigzig_scheme_nav
          WHERE (source,scheme_code)=(a.source,a.scheme_code)
            AND nav_date <= (e.nav_date - interval '3 months')::date
          ORDER BY nav_date DESC LIMIT 1
        ) three ON true
      ), inserted AS (
        INSERT INTO public.app_performance_cache(
          source,scheme_code,as_of_date,latest_nav,daily_return_pct,
          one_month_return_pct,three_month_return_pct,
          since_inception_return_pct,calculation_metadata,calculated_at
        )
        SELECT source,scheme_code,end_date,end_nav,
               CASE WHEN previous_nav IS NULL THEN NULL
                    ELSE round(((end_nav/previous_nav)-1)*100,8) END,
               CASE WHEN one_date=end_date THEN NULL
                    ELSE round(((end_nav/one_nav)-1)*100,8) END,
               CASE WHEN three_date=end_date THEN NULL
                    ELSE round(((end_nav/three_nav)-1)*100,8) END,
               CASE WHEN inception_date=end_date THEN NULL
                    ELSE round(((end_nav/inception_nav)-1)*100,8) END,
               jsonb_build_object(
                 'method','absolute_return','previous_date',previous_date,
                 'one_month_start_date',one_date,'three_month_start_date',three_date,
                 'inception_date',inception_date,
                 'one_month_partial',one_date=inception_date
                   AND inception_date > (end_date-interval '1 month')::date,
                 'three_month_partial',three_date=inception_date
                   AND inception_date > (end_date-interval '3 months')::date
               ),now()
        FROM observations RETURNING 1
      ) SELECT count(*)::integer FROM inserted
    """, prepare=False).fetchone()[0]
    return {'affected_schemes': affected, 'monthly_rows': monthly,
            'performance_rows': performance}


def refresh_all() -> dict[str, int]:
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        connection.execute(f"""
          CREATE TEMP TABLE {AFFECTED_TABLE} ON COMMIT DROP AS
          SELECT DISTINCT source,scheme_code FROM public.tigzig_scheme_nav
        """, prepare=False)
        return refresh_affected_analytics(connection)


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        print(json.dumps(refresh_all(), indent=2))
        return 0
    except Exception as error:
        print(f'Analytics refresh failed ({type(error).__name__})')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
