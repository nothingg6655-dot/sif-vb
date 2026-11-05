import psycopg
import sys
import datetime
from database.migrate import DatabaseConfig

sys.stdout.reconfigure(encoding='utf-8')

conn = psycopg.connect(**DatabaseConfig.from_env().connection_kwargs())
cur = conn.cursor()

now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))

# Check/insert managers in sif_fund_managers
mgr_names = [
    'Deepak Gupta',
    'Dwaipayan Ray',
    'Ravneet Singh',
    'Ashish Jain',
    'Ramesh S',
    'Gaurav Mehta',
    'Dhaval Joshi',
    'Harshil Suvarnkar'
]

mgr_id_map = {}
for name in mgr_names:
    cur.execute("SELECT manager_id FROM sif_fund_managers WHERE manager_name ILIKE %s", (name,))
    row = cur.fetchone()
    if row:
        mgr_id_map[name] = row[0]
    else:
        cur.execute("SELECT COALESCE(MAX(manager_id), 0) + 1 FROM sif_fund_managers")
        next_id = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO sif_fund_managers (manager_id, manager_name, created_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (manager_id) DO NOTHING
        """, (next_id, name, now))
        mgr_id_map[name] = next_id
        print(f"Inserted manager: {name} (ID: {next_id})")

print("Manager IDs:", mgr_id_map)

# Link for Report 684 (Summit Active Asset Allocator)
# Deepak Gupta
dg_id = mgr_id_map['Deepak Gupta']
cur.execute("""
    INSERT INTO sif_report_fund_managers (report_id, manager_id, manager_role, managing_since)
    VALUES (684, %s, 'Fund Manager', '2026-07-22')
    ON CONFLICT DO NOTHING
""", (dg_id,))
print("Linked Deepak Gupta to report 684")

# Link for Report 685 (Eminence Hybrid)
# Dwaipayan Ray, Ravneet Singh, Ashish Jain, Ramesh S
for name in ['Dwaipayan Ray', 'Ravneet Singh', 'Ashish Jain', 'Ramesh S']:
    m_id = mgr_id_map[name]
    cur.execute("""
        INSERT INTO sif_report_fund_managers (report_id, manager_id, manager_role, managing_since)
        VALUES (685, %s, 'Fund Manager', '2026-08-15')
        ON CONFLICT DO NOTHING
    """, (m_id,))
print("Linked AlphaGrep managers to report 685")

conn.commit()
print("Managers successfully populated and committed!")

