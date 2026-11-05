from datetime import timezone
import json
from pathlib import Path
import unittest

from ingestion.tigzig.manifest import ManifestError, parse_manifest
from ingestion.tigzig.source_versions import classify_changes


FIXTURE = Path(__file__).resolve().parents[2] / 'docs' / 'sources' / 'manifest.inspected.json'


class ManifestTests(unittest.TestCase):
    def manifest_dict(self):
        return json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_valid_actual_manifest_shape(self):
        manifest = parse_manifest(self.manifest_dict())
        self.assertEqual(set(manifest.files), {'meta', 'nav', 'aaum'})
        self.assertEqual(manifest.files['meta'].name, 'sif_meta.parquet')
        self.assertEqual(manifest.files['nav'].rows, 15500)
        self.assertIsNotNone(manifest.generated_at.tzinfo)

    def test_invalid_manifest_cases(self):
        cases = []
        root = self.manifest_dict()
        missing_time = dict(root); missing_time.pop('generated_at'); cases.append(missing_time)
        files_object = dict(root); files_object['files'] = {}; cases.append(files_object)
        missing_file = dict(root); missing_file['files'] = [
            entry for entry in root['files'] if entry['name'] != 'sif_nav.parquet'
        ]; cases.append(missing_file)
        bad_hash = json.loads(json.dumps(root))
        next(entry for entry in bad_hash['files'] if entry['name'] == 'sif_nav.parquet')['sha256'] = 'BAD'
        cases.append(bad_hash)
        bad_columns = json.loads(json.dumps(root))
        next(entry for entry in bad_columns['files'] if entry['name'] == 'sif_meta.parquet')['columns'] = []
        cases.append(bad_columns)
        duplicate = json.loads(json.dumps(root)); duplicate['files'].append(duplicate['files'][0]); cases.append(duplicate)
        for index, value in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ManifestError):
                parse_manifest(value)

    def test_change_detection_uses_only_successful_hashes(self):
        manifest = parse_manifest(self.manifest_dict())
        stored = {
            'meta': manifest.files['meta'].sha256,
            'nav': '0' * 64,
        }
        result = {item.dataset: item for item in classify_changes(manifest, stored)}
        self.assertFalse(result['meta'].changed)
        self.assertTrue(result['nav'].changed)
        self.assertTrue(result['aaum'].changed)
        self.assertIsNone(result['aaum'].successful_sha256)

