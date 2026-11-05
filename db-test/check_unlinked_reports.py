import psycopg
import sys
from database.migrate import DatabaseConfig

sys.stdout.reconfigure(encoding='utf-8')

conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
cur = conn.cursor()

cur.execute("""
    SELECT st.strategy_id, st.strategy_name, r.report_id, r.validation_status
    FROM sif_strategies st
    LEFT JOIN sif_factsheet_reports r ON r.strategy_id = st.strategy_id
    WHERE st.strategy_id IN (14, 31, 32, 33, 34, 35, 36)
    ORDER BY st.strategy_id
""")
for r in cur.fetchall():
    print(r)

