"""Safety tests execute real migrations against VB-SIF without persisting probes."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg

from database import migrate as migration_module
from ingestion.tigzig.config import DatabaseConfig


class MigrationTests(unittest.TestCase):
    def files_in(self, directory):
        destination = Path(directory)
        for source in migration_module.MIGRATIONS.glob('*.sql'):
            (destination / source.name).write_bytes(source.read_bytes())
        return destination

    def test_repeated_migration_has_no_pending_changes(self):
        result = migration_module.migrate()
        self.assertEqual(result['applied'], [])
        self.assertIn('0001', result['already_applied'])
        self.assertIn('0006', result['already_applied'])

    def test_changed_applied_migration_is_refused(self):
        with TemporaryDirectory() as temporary:
            directory = self.files_in(temporary)
            path = directory / '0001_tigzig_foundation.sql'
            path.write_bytes(path.read_bytes() + b'\n-- Edited applied migration\n')
            with patch.object(migration_module, 'MIGRATIONS', directory):
                with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
                    migration_module.migrate()

    def test_failed_migration_rolls_back_ddl_and_ledger(self):
        probe = 'app_m1_rollback_probe_' + uuid4().hex
        with TemporaryDirectory() as temporary:
            directory = self.files_in(temporary)
            (directory / '9999_test_rollback.sql').write_text(
                f'CREATE TABLE public.{probe} (id INTEGER PRIMARY KEY); SELECT 1 / 0;', encoding='utf-8')
            with patch.object(migration_module, 'MIGRATIONS', directory):
                with self.assertRaises(psycopg.errors.DivisionByZero):
                    migration_module.migrate()
        with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
            self.assertIsNone(connection.execute('SELECT to_regclass(%s)', (f'public.{probe}',)).fetchone()[0])
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM public.app_schema_migrations WHERE version='9999'"
            ).fetchone()[0], 0)

    def test_wrong_database_configuration_is_refused_before_connection(self):
        with patch.dict('os.environ', {'PGDATABASE': 'postgres'}):
            with self.assertRaisesRegex(ValueError, 'PGDATABASE must be VB-SIF'):
                migration_module.migrate()
