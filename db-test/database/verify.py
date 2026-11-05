"""Read-only database inventory for milestone verification and schema preservation."""
import argparse
import json

import psycopg
from psycopg import sql

from database.migrate import verify_target
from ingestion.tigzig.config import DatabaseConfig


def database_snapshot():
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs(), autocommit=True) as connection:
        verify_target(connection)
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            relations = connection.execute("""
                SELECT c.relname, c.relkind::text
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') ORDER BY c.relname
            """).fetchall()
            objects = {}
            for name, kind in relations:
                row_count = connection.execute(
                    sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(name))
                ).fetchone()[0]
                columns = connection.execute("""
                    SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                           pg_get_expr(d.adbin, d.adrelid)
                    FROM pg_attribute a
                    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                    WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
                """, (f'public.{name}',)).fetchall()
                constraints = connection.execute("""
                    SELECT conname, contype::text, pg_get_constraintdef(oid)
                    FROM pg_constraint WHERE conrelid = %s::regclass ORDER BY conname
                """, (f'public.{name}',)).fetchall()
                indexes = connection.execute("""
                    SELECT indexname, indexdef FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = %s ORDER BY indexname
                """, (name,)).fetchall()
                objects[name] = {'kind': kind, 'rows': row_count, 'columns': columns,
                                 'constraints': constraints, 'indexes': indexes}
                if kind == 'v':
                    objects[name]['definition'] = connection.execute(
                        'SELECT pg_get_viewdef(%s::regclass, true)', (f'public.{name}',)
                    ).fetchone()[0]
            return {'database': 'VB-SIF', 'schema': 'public', 'objects': objects}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare-legacy', help='Check old objects against a saved pre-migration JSON snapshot')
    arguments = parser.parse_args()
    try:
        snapshot = database_snapshot()
        if arguments.compare_legacy:
            with open(arguments.compare_legacy, encoding='utf-8-sig') as file:
                baseline = json.load(file)
            for name, before in baseline['objects'].items():
                # Normalize tuples to JSON arrays for a stable comparison.
                after = json.loads(json.dumps(snapshot['objects'].get(name)))
                if after != before:
                    raise RuntimeError(f'Pre-existing object changed: {name}')
            snapshot['legacy_preserved'] = True
        print(json.dumps(snapshot, indent=2))
        return 0
    except Exception as error:
        if isinstance(error, (ValueError, RuntimeError)):
            print(str(error))
        else:
            print(f'Database verification failed ({type(error).__name__})')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
