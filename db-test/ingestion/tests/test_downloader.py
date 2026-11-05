from email.message import Message
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ingestion.tigzig.downloader import DownloadError, IntegrityError, download_file
from ingestion.tigzig.manifest import ManifestFile


class Response(BytesIO):
    def __init__(self, content: bytes, content_length: int | None = None):
        super().__init__(content)
        self.headers = Message()
        self.headers['Content-Type'] = 'application/octet-stream'
        self.headers['Content-Length'] = str(len(content) if content_length is None else content_length)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def expected(content: bytes) -> ManifestFile:
    return ManifestFile('nav', 'sif_nav.parquet', 1, ('scheme_code',), len(content), sha256(content).hexdigest())


class DownloaderTests(unittest.TestCase):
    def test_streams_and_atomically_promotes_verified_file(self):
        content = b'valid parquet stand-in'
        with TemporaryDirectory() as temporary:
            result = download_file(
                'https://example.test/', expected(content), Path(temporary),
                opener=lambda *_args, **_kwargs: Response(content), retries=0,
            )
            self.assertTrue(result.downloaded)
            self.assertEqual(result.path.read_bytes(), content)
            self.assertEqual(list(Path(temporary).glob('*.part')), [])

    def test_valid_cached_file_skips_network(self):
        content = b'cached'
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'sif_nav.parquet'
            target.write_bytes(content)
            result = download_file(
                'https://example.test/', expected(content), Path(temporary),
                opener=lambda *_args, **_kwargs: self.fail('network must not be used'),
            )
            self.assertFalse(result.downloaded)
            self.assertEqual(result.attempts, 0)

    def test_timeout_retries_with_exponential_backoff(self):
        attempts = 0
        sleeps = []
        def timeout(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise TimeoutError('timed out')
        with TemporaryDirectory() as temporary, self.assertRaises(DownloadError):
            download_file(
                'https://example.test/', expected(b'x'), Path(temporary), opener=timeout,
                retries=2, backoff_seconds=1, sleep=sleeps.append, jitter=lambda: 0,
            )
        self.assertEqual(attempts, 3)
        self.assertEqual(sleeps, [1, 2])

    def test_sha_mismatch_never_replaces_existing_file_and_cleans_temporary(self):
        good = b'expected bytes'
        bad = b'bad bytes!!!!!'
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / 'sif_nav.parquet'
            target.write_bytes(b'old cache')
            with self.assertRaises(IntegrityError):
                download_file(
                    'https://example.test/', expected(good), Path(temporary),
                    opener=lambda *_args, **_kwargs: Response(bad), retries=1,
                    backoff_seconds=0, sleep=lambda _: None, jitter=lambda: 0,
                )
            self.assertEqual(target.read_bytes(), b'old cache')
            self.assertEqual(list(Path(temporary).glob('*.part')), [])

    def test_truncated_body_is_rejected(self):
        content = b'complete file'
        with TemporaryDirectory() as temporary, self.assertRaises(IntegrityError):
            download_file(
                'https://example.test/', expected(content), Path(temporary),
                opener=lambda *_args, **_kwargs: Response(content[:-2], len(content)),
                retries=0,
            )

