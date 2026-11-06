"""Copy the local database to an EMPTY Supabase target, then verify every table.

Reads .env (local source) and .env.supabase (target administrator).
Keeps dumps, logs, role credentials and verification reports in ignored data/.
Does not change the running application's connection settings.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

from dotenv import dotenv_values
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.tigzig.worker import ingestion_lock  # noqa: E402

PG_BIN = Path(r'C:\Program Files\PostgreSQL\18\bin')
PROJECT_REF = 'pduxixfhejkslwafxplw'


def connection_options(values):
    options = {key: values['PG' + key.upper()] for key in ['host', 'port', 'user', 'password']}
    options.update(dbname=values['PGDATABASE'], sslmode=values.get('PGSSLMODE', 'disable'),
                   connect_timeout=30, options='-c search_path=public -c timezone=Asia/Kolkata')
    if values.get('PGSSLROOTCERT'):
        options['sslrootcert'] = str((ROOT / values['PGSSLROOTCERT']).resolve())
    return options


def command_environment(values):
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update({key: str(value) for key, value in values.items()
                if key.startswith('PG') and value is not None})
    if values.get('PGSSLROOTCERT'):
        env['PGSSLROOTCERT'] = str((ROOT / values['PGSSLROOTCERT']).resolve())
    env['PGOPTIONS'] = '-c search_path=public -c timezone=Asia/Kolkata -c statement_timeout=0'
    return env


def inventory(connection):
    return connection.execute('''SELECT c.relname,c.relkind::text
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') ORDER BY 1''').fetchall()


def table_fingerprints(connection, objects):
    # Supavisor may ignore startup `options`; timestamps must render identically.
    connection.execute("SET TIME ZONE 'UTC'")
    values = {}
    for name, kind in objects:
        if kind not in ('r', 'p'):
            continue
        count, digest = connection.execute(sql.SQL('''
            SELECT count(*),md5(coalesce(string_agg(row_to_json(t)::text,E'\\n'
                ORDER BY row_to_json(t)::text COLLATE "C"),'')) FROM public.{} t
        ''').format(sql.Identifier(name))).fetchone()
        values[name] = {'rows': count, 'md5': digest}
    return values


def sequences(connection, objects):
    return {name: list(connection.execute(sql.SQL('SELECT last_value,is_called FROM public.{}')
             .format(sql.Identifier(name))).fetchone()) for name, kind in objects if kind == 'S'}


def main():
    source = dotenv_values(ROOT / '.env')
    target = dotenv_values(ROOT / '.env.supabase')
    if source['PGDATABASE'] != 'VB-SIF' or source['PGHOST'] not in ('localhost', '127.0.0.1'):
        raise RuntimeError('Source must be the original local VB-SIF database')
    if (target['PGHOST'] != 'aws-0-ap-southeast-1.pooler.supabase.com'
            or target['PGUSER'] != f'postgres.{PROJECT_REF}' or target['PGDATABASE'] != 'postgres'):
        raise RuntimeError('Supabase target differs from the explicitly supplied project')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = ROOT / 'data' / ('supabase-migration-' + stamp)
    output.mkdir(parents=True)
    passwords = {name: secrets.token_urlsafe(36) for name in ['sif_backend', 'sif_ingestion']}
    redactions = [source['PGPASSWORD'], target['PGPASSWORD'], *passwords.values()]

    def run(name, arguments, values):
        result = subprocess.run([str(PG_BIN / (name + '.exe')), *map(str, arguments)],
            env=command_environment(values), capture_output=True, encoding='utf-8', errors='replace')
        log = result.stdout + result.stderr
        for secret in redactions:
            log = log.replace(secret, '[redacted]')
        (output / (name + '.log')).write_text(log, encoding='utf-8')
        if result.returncode:
            print(log[-3500:], flush=True)
            raise RuntimeError(f'{name} failed; target restore is rolled back on SQL errors')
        return result.stdout

    with psycopg.connect(**connection_options(source), autocommit=True) as local, \
            psycopg.connect(**connection_options(target), autocommit=True) as remote:
        if remote.execute('SELECT current_database(),current_schema()').fetchone() != ('postgres', 'public'):
            raise RuntimeError('Unexpected target identity')
        if inventory(remote):
            raise RuntimeError('Refusing to overwrite existing application objects in Supabase')
        if remote.execute("SELECT 1 FROM pg_roles WHERE rolname IN ('sif_backend','sif_ingestion')").fetchone():
            raise RuntimeError('Application role names already exist; inspect before proceeding')
        source_objects = inventory(local)
        tables = [name for name, kind in source_objects if kind in ('r', 'p')]
        print(json.dumps({'phase': 'export', 'tables': len(tables), 'backupDirectory': str(output)}), flush=True)
        with ingestion_lock(local) as (acquired, _), local.transaction():
            if not acquired:
                raise RuntimeError('An ingestion job is running; retry after it finishes')
            # Hold a short write freeze while ordinary API SELECTs remain available.
            local.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(
                sql.Identifier('public', name) for name in tables)))
            before = table_fingerprints(local, source_objects)
            before_sequences = sequences(local, source_objects)
            (output / 'source-verification.json').write_text(json.dumps({
                'tables': before, 'sequences': before_sequences}, indent=2), encoding='utf-8')
            archive = output / 'sif-current.dump'
            run('pg_dump', ['--schema=public', '--format=custom', '--no-owner', '--no-privileges',
                '--no-password', '--file', archive], source)
            toc = run('pg_restore', ['--list', archive], source)
            toc = '\n'.join(line for line in toc.splitlines() if not re.match(
                r'^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s', line))
            listing = output / 'restore.list'
            listing.write_text(toc, encoding='utf-8')
            restore_sql = output / 'restore.sql'
            run('pg_restore', ['--no-owner', '--no-privileges', '--use-list', listing,
                '--file', restore_sql, archive], source)
            private_sql = output / 'private-access.sql'
            statements = [
                'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC,anon,authenticated;',
                'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC,anon,authenticated;',
                'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC,anon,authenticated;',
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon,authenticated;',
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon,authenticated;',
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC,anon,authenticated;',
            ]
            for role, password in passwords.items():
                statements.append(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER '
                    'NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(
                        sql.Identifier(role), sql.Literal(password)).as_string(remote) + ';')
                statements.append(f'GRANT USAGE ON SCHEMA public TO {role};')
                statements.extend([
                    f"ALTER ROLE {role} SET timezone='Asia/Kolkata';",
                    f"ALTER ROLE {role} SET search_path='public';",
                    f"ALTER ROLE {role} SET statement_timeout='30000';",
                ])
            statements.extend([
                'GRANT SELECT ON ALL TABLES IN SCHEMA public TO sif_backend;',
                'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO sif_ingestion;',
                'GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO sif_ingestion;',
                'GRANT TEMPORARY ON DATABASE postgres TO sif_ingestion;',
            ])
            for name in tables:
                identifier = sql.Identifier('public', name).as_string(remote)
                statements.extend([
                    f'ALTER TABLE {identifier} ENABLE ROW LEVEL SECURITY;',
                    f'CREATE POLICY sif_backend_select ON {identifier} FOR SELECT TO sif_backend USING (true);',
                    f'CREATE POLICY sif_ingestion_all ON {identifier} FOR ALL TO sif_ingestion USING (true) WITH CHECK (true);',
                ])
            private_sql.write_text('\n'.join(statements), encoding='utf-8')
            (output / 'application-roles.json').write_text(json.dumps(passwords), encoding='utf-8')
            print(json.dumps({'phase': 'restore', 'targetDatabase': 'postgres'}), flush=True)
            run('psql', ['--no-password', '--single-transaction', '--set=ON_ERROR_STOP=1',
                '--file', restore_sql, '--file', private_sql], target)
            after_objects = inventory(remote)
            after = table_fingerprints(remote, after_objects)
            after_sequences = sequences(remote, after_objects)
            mismatches = [name for name in set(before) | set(after) if before.get(name) != after.get(name)]
            if mismatches or before_sequences != after_sequences or source_objects != after_objects:
                raise RuntimeError('Restored database verification failed: ' + ','.join(mismatches))
            access = remote.execute('''SELECT count(*) FILTER (WHERE c.relrowsecurity),
                count(*) FILTER (WHERE has_table_privilege('anon',c.oid,'SELECT') OR
                    has_table_privilege('authenticated',c.oid,'SELECT')),
                count(*) FILTER (WHERE has_table_privilege('sif_backend',c.oid,'INSERT'))
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p')''').fetchone()
            if access != (len(tables), 0, 0):
                raise RuntimeError('Cloud data access verification failed')
            report = {'completedAt': datetime.now(timezone.utc).isoformat(), 'projectRef': PROJECT_REF,
                'sourceVersion': local.execute('SHOW server_version').fetchone()[0],
                'targetVersion': remote.execute('SHOW server_version').fetchone()[0],
                'tablesVerified': len(tables), 'totalRows': sum(item['rows'] for item in before.values()),
                'sequencesVerified': len(before_sequences), 'objectCount': len(source_objects),
                'tableFingerprints': after, 'contentMismatches': [], 'rowLevelSecurityTables': access[0],
                'publiclyReadableTables': access[1], 'backendWritableTables': access[2]}
            (output / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            (output / 'application-roles.json').write_text(json.dumps(passwords), encoding='utf-8')
            (ROOT / 'data' / 'supabase-migration-latest.json').write_text(
                json.dumps({'directory': str(output)}), encoding='utf-8')
            print(json.dumps({key: value for key, value in report.items() if key != 'tableFingerprints'}, indent=2))


if __name__ == '__main__':
    main()
