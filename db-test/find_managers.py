import psycopg
import sys
import json
from database.migrate import DatabaseConfig

sys.stdout.reconfigure(encoding='utf-8')

conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
cur = conn.cursor()

# Search tables with manager in column name
cur.execute("""
    SELECT table_name, column_name 
    FROM information_schema.columns 
    WHERE column_name ILIKE '%manager%' OR table_name ILIKE '%manager%'
""")
print("=== Tables/Columns with manager ===")
for r in cur.fetchall():
    print(r)

# Check sif_report_strategy_profile supplementary_fields
cur.execute("SELECT report_id, supplementary_fields FROM sif_report_strategy_profile WHERE supplementary_fields IS NOT NULL LIMIT 10")
print("\n=== Sample supplementary_fields ===")
for r in cur.fetchall():
    print(r[0], "->", list(r[1].keys()) if isinstance(r[1], dict) else r[1])

