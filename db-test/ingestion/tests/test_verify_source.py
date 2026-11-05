from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ingestion.tigzig.config import TigzigConfig
from ingestion.tigzig.manifest import Manifest, ManifestFile
from ingestion.tigzig.source_versions import ChangeStatus
from ingestion.tigzig.verify_source import verify_current_source


class VerifySourceTests(unittest.TestCase):
    def test_unchanged_successful_source_without_cache_skips_download(self):
        content_hash = sha256(b'example').hexdigest()
        files = {
            dataset: ManifestFile(dataset, f'sif_{dataset}.parquet', 1, ('x',), 7, content_hash)
            for dataset in ('meta', 'nav', 'aaum')
        }
        manifest = Manifest(datetime.now(timezone.utc), files, {})
        statuses = [
            ChangeStatus(dataset, file.name, False, file.sha256)
            for dataset, file in files.items()
        ]
        with TemporaryDirectory() as temporary:
            config = TigzigConfig('https://example.test/', Path(temporary), 1, 0, 0)
            report_path = Path(temporary) / 'report.json'
            with patch('ingestion.tigzig.verify_source.TigzigConfig.from_env', return_value=config), \
                 patch('ingestion.tigzig.verify_source.fetch_manifest', return_value=manifest), \
                 patch('ingestion.tigzig.verify_source.detect_changes', return_value=statuses), \
                 patch('ingestion.tigzig.verify_source.download_file') as download:
                report = verify_current_source(report_path)
            download.assert_not_called()
            self.assertIn('skipped', report['validation']['status'])
            self.assertTrue(report_path.exists())

