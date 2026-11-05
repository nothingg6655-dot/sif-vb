from pathlib import Path

import psycopg


def config() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path('.env').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    return values


settings = config()
with psycopg.connect(
    host=settings['PGHOST'], port=settings['PGPORT'], dbname=settings['PGDATABASE'],
    user=settings['PGUSER'], password=settings['PGPASSWORD'],
) as connection:
    with connection.cursor() as cursor:
        cursor.execute('''
            SELECT dataset_name,rejection_reason,raw_record
            FROM public.tigzig_ingestion_rejections ORDER BY created_at
        ''')
        print('rejections', cursor.rowcount)
        for row in cursor.fetchall():
            print(row)

        cursor.execute('''
            SELECT count(*),min(month_end),max(month_end)
            FROM public.app_monthly_returns
        ''')
        print('monthly', cursor.fetchone())

        cursor.execute('''
            SELECT c.reference_fund_id,c.preferred_scheme_code,s.scheme_name,
                   min(n.nav_date),max(n.nav_date),count(DISTINCT r.month_end)
            FROM public.app_performance_config c
            JOIN public.tigzig_schemes s
              ON (s.source,s.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
            LEFT JOIN public.app_monthly_returns r
              ON (r.source,r.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
            JOIN public.tigzig_scheme_nav n
              ON (n.source,n.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
            GROUP BY 1,2,3 ORDER BY 1
        ''')
        for row in cursor.fetchall():
            print(row)
