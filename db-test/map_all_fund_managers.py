import psycopg
import sys
import json
from database.migrate import DatabaseConfig

sys.stdout.reconfigure(encoding='utf-8')

conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
cur = conn.cursor()

# Get all strategies and their managers
cur.execute("""
    SELECT st.strategy_id, st.slug, st.strategy_name,
           COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'name', fm.manager_name,
                       'role', COALESCE(rfm.manager_role, 'Fund Manager'),
                       'since', rfm.managing_since
                   ) ORDER BY fm.manager_name
               ) FILTER (WHERE fm.manager_name IS NOT NULL),
               '[]'::jsonb
           ) AS managers
    FROM sif_strategies st
    LEFT JOIN sif_factsheet_reports r ON r.strategy_id = st.strategy_id AND r.is_primary AND r.validation_status IN ('PASSED', 'WARNING')
    LEFT JOIN sif_report_fund_managers rfm ON rfm.report_id = r.report_id
    LEFT JOIN sif_fund_managers fm ON fm.manager_id = rfm.manager_id
    GROUP BY st.strategy_id, st.slug, st.strategy_name
    ORDER BY st.strategy_id
""")

rows = cur.fetchall()
print(f"Total strategies: {len(rows)}")
for st_id, slug, name, mgrs in rows:
    names = [m['name'] for m in mgrs]
    print(f"ID {st_id}: {name} -> {', '.join(names) if names else 'NONE'}")

