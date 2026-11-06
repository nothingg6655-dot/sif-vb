"""Transactional, checksum-checked migrations scoped strictly to VB-SIF/public."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

import psycopg

from ingestion.tigzig.config import DatabaseConfig

MIGRATIONS = Path(__file__).resolve().parent / 'migrations'
LEDGER_BOOTSTRAP = Path(__file__).resolve().parent / 'bootstrap' / '0000_migration_ledger.sql'
LEGACY_OBJECTS = {
    ('sif_amcs', 'r'), ('sif_schemes', 'r'), ('sif_scheme_isins', 'r'),
    ('sif_nav_history', 'r'), ('sif_aaum_history', 'r'), ('vw_sif_scheme_latest', 'v'),
}


def verify_target(connection):
    target = connection.execute('SELECT current_database(), current_schema()').fetchone()
    if target != (DatabaseConfig.from_env().dbname, 'public'):
        raise RuntimeError('Refusing migration: target must match the configured database / public')


def inventory(connection):
    return connection.execute("""
        SELECT c.relname, c.relkind::text
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY c.relname
    """).fetchall()


def migrate(*, preserve_legacy=False):
    files = sorted(MIGRATIONS.glob('[0-9][0-9][0-9][0-9]_*.sql'))
    if not files:
        raise RuntimeError('No migration files found')
    versions = [path.name[:4] for path in files]
    if len(versions) != len(set(versions)):
        raise RuntimeError('Duplicate migration versions')
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs(), autocommit=True) as connection:
        verify_target(connection)
        # One transaction for ledger creation and all pending DDL. Concurrent
        # migrators serialize; this lock is distinct from future ingestion locks.
        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout = '5s'")
            connection.execute('SELECT pg_advisory_xact_lock(%s, %s)', (861943, 1))
            before = set(inventory(connection))
            has_ledger = ('app_schema_migrations', 'r') in before
            if not has_ledger and before:
                if not preserve_legacy or before != LEGACY_OBJECTS:
                    raise RuntimeError('Unexpected existing public objects; inspect before migrating. '
                                       'Use --preserve-legacy only for the six documented empty legacy objects.')
                for table, kind in sorted(LEGACY_OBJECTS):
                    if kind == 'r':
                        count = connection.execute(
                            psycopg.sql.SQL('SELECT count(*) FROM public.{}').format(psycopg.sql.Identifier(table))
                        ).fetchone()[0]
                        if count != 0:
                            raise RuntimeError('Legacy tables are not empty; review before migrating')
            connection.execute(LEDGER_BOOTSTRAP.read_text(encoding='utf-8'), prepare=False)
            applied = {
                row[0]: row[1:] for row in connection.execute(
                    'SELECT version, filename, sha256 FROM public.app_schema_migrations'
                ).fetchall()
            }
            if set(applied) - set(versions):
                raise RuntimeError('Database has migrations missing from this checkout')
            # Validate all previously applied files before applying any new one.
            for path in files:
                prior = applied.get(path.name[:4])
                if prior and prior != (path.name, sha256(path.read_bytes()).hexdigest()):
                    raise RuntimeError(f'Applied migration checksum mismatch: {path.name}')
            pending = []
            for path in files:
                version = path.name[:4]
                if version in applied:
                    continue
                if any(previous > version for previous in applied):
                    raise RuntimeError('Out-of-order migration refused')
                raw = path.read_bytes()
                connection.execute(raw.decode('utf-8'), prepare=False)
                connection.execute(
                    'INSERT INTO public.app_schema_migrations(version, filename, sha256) VALUES (%s, %s, %s)',
                    (version, path.name, sha256(raw).hexdigest()),
                )
                pending.append(path.name)
        # Report only after commit.
        return {'database': DatabaseConfig.from_env().dbname, 'schema': 'public', 'applied': pending,
                'already_applied': sorted(applied), 'public_objects': inventory(connection)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preserve-legacy', action='store_true',
                        help='Preserve the exact six empty sif_* objects created before Milestone 1')
    arguments = parser.parse_args()
    try:
        print(json.dumps(migrate(preserve_legacy=arguments.preserve_legacy), indent=2))
    except Exception as error:
        # Do not print database errors containing connection strings or credentials.
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error), file=sys.stderr)
        else:
            print(f'Migration failed ({type(error).__name__}); transaction rolled back.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
