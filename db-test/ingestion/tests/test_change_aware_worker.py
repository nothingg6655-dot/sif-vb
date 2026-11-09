"""Prove that unchanged snapshots bypass imports and corrections remain detectable."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from ingestion.tigzig.config import SchedulerConfig
from ingestion.tigzig.manifest import parse_manifest
from ingestion.tigzig.source_versions import changed_source_files
from ingestion.tigzig.worker import run_sync_job

FIXTURE = Path(__file__).resolve().parents[2] / 'docs/sources/manifest.inspected.json'


class SourceCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.manifest = parse_manifest(json.loads(FIXTURE.read_text(encoding='utf-8')))
        self.files = [{
            'filename': item['name'], 'sha256': item['sha256'],
        } for item in self.manifest.raw['files']]
        self.connection = MagicMock()
        self.rows = [(dataset, file.sha256, self.files)
                     for dataset, file in self.manifest.files.items()]
        self.connection.execute.return_value.fetchall.return_value = self.rows

    def test_identical_files_have_no_changes(self):
        self.assertEqual(changed_source_files(self.manifest, self.connection), [])
        self.assertTrue(self.connection.execute.call_args.args[0].strip().startswith('SELECT'))

    def test_changed_nav_is_detected(self):
        self.connection.execute.return_value.fetchall.return_value = [
            (dataset, '0' * 64 if dataset == 'nav' else digest, files)
            for dataset, digest, files in self.rows
        ]
        self.assertEqual(changed_source_files(self.manifest, self.connection), ['sif_nav.parquet'])

    def test_csv_timestamp_correction_is_detected_independently(self):
        next(item for item in self.files if item['filename'] == 'sif_nav.csv')['sha256'] = '0' * 64
        self.assertEqual(changed_source_files(self.manifest, self.connection), ['sif_nav.csv'])

    def test_missing_checkpoint_requires_initial_import(self):
        self.connection.execute.return_value.fetchall.return_value = []
        self.assertCountEqual(changed_source_files(self.manifest, self.connection), [
            'sif_meta.parquet', 'sif_nav.parquet', 'sif_aaum.parquet', 'sif_nav.csv',
        ])


class ChangeAwareWorkerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = parse_manifest(json.loads(FIXTURE.read_text(encoding='utf-8')))
        self.config = replace(SchedulerConfig.from_env(), max_retries=0)
        self.lock = self.enterContext(patch('ingestion.tigzig.worker.ingestion_lock'))
        self.connection = MagicMock()
        self.lock.return_value.__enter__.return_value = (True, self.connection)
        self.fetch = self.enterContext(patch('ingestion.tigzig.worker.fetch_manifest', return_value=self.manifest))
        self.enterContext(patch('ingestion.tigzig.worker.TigzigConfig.from_env', return_value=SimpleNamespace(
            base_url='https://example.test/', timeout_seconds=10,
        )))
        self.changes = self.enterContext(patch('ingestion.tigzig.worker.changed_source_files', return_value=[]))
        self.prepare = self.enterContext(patch('ingestion.tigzig.worker.prepare_source', return_value=SimpleNamespace(
            manifest=self.manifest, files_processed=[],
        )))
        self.sync = self.enterContext(patch('ingestion.tigzig.worker.sync_prepared', return_value={'inserted': 1, 'updated': 0}))
        self.audit = self.enterContext(patch('ingestion.tigzig.worker._terminal_run', return_value='audit-id'))
        self.enterContext(patch('ingestion.tigzig.worker.log_event'))

    def test_unchanged_source_never_prepares_or_imports(self):
        result = run_sync_job('scheduled-test', config=self.config)
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual((result['inserted'], result['updated']), (0, 0))
        self.prepare.assert_not_called()
        self.sync.assert_not_called()
        self.audit.assert_called_once()
        self.assertEqual(self.audit.call_args.kwargs['status'], 'succeeded')
        self.assertEqual(len(self.audit.call_args.kwargs['files_processed']), 4)
        self.assertTrue(all(not item['downloaded'] for item in self.audit.call_args.kwargs['files_processed']))

    def test_changed_source_imports_the_exact_manifest_checked(self):
        self.changes.return_value = ['sif_nav.parquet']
        result = run_sync_job('scheduled-test', config=self.config)
        self.assertEqual(result['status'], 'succeeded')
        self.fetch.assert_called_once()
        self.prepare.assert_called_once_with(manifest=self.manifest)
        self.sync.assert_called_once()

    def test_revalidation_still_imports_an_unchanged_snapshot(self):
        result = run_sync_job('revalidate-test', job_type='revalidate', config=self.config)
        self.assertEqual(result['status'], 'succeeded')
        self.changes.assert_not_called()
        self.sync.assert_called_once()

    def test_manifest_failure_never_imports_or_advances_a_checkpoint(self):
        self.fetch.side_effect = RuntimeError('source unavailable')
        with self.assertRaisesRegex(RuntimeError, 'source unavailable'):
            run_sync_job('scheduled-test', config=self.config)
        self.prepare.assert_not_called()
        self.sync.assert_not_called()
        self.assertEqual(self.audit.call_args.kwargs['status'], 'failed')

    def test_lock_contention_skips_the_source_check(self):
        self.lock.return_value.__enter__.return_value = (False, self.connection)
        result = run_sync_job('scheduled-test', config=self.config)
        self.assertEqual(result['reason'], 'lock_unavailable')
        self.fetch.assert_not_called()
        self.sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
