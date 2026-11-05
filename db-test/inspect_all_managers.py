import psycopg
import sys
from database.migrate import DatabaseConfig

sys.stdout.reconfigure(encoding='utf-8')

conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
cur = conn.cursor()

cur.execute("SELECT * FROM sif_fund_managers")
print("=== sif_fund_managers ===")
for r in cur.fetchall():
    print(r)

cur.execute("""
    SELECT rfm.report_id, st.strategy_id, st.strategy_name, fm.manager_name, rfm.manager_role, rfm.managing_since
    FROM sif_report_fund_managers rfm
    JOIN sif_fund_managers fm ON fm.manager_id = rfm.manager_id
    JOIN sif_factsheet_reports r ON r.report_id = rfm.report_id
    JOIN sif_strategies st ON st.strategy_id = r.strategy_id
""")
print("\n=== sif_report_fund_managers linked to strategies ===")
for r in cur.fetchall():
    print(r)

