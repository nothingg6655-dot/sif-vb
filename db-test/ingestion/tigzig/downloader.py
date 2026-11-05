"""Stream files into same-directory temporaries and atomically promote verified data."""
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import random
import tempfile
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .manifest import ManifestFile


class DownloadError(RuntimeError):
    """A download failed after all attempts."""


class IntegrityError(DownloadError):
    """Downloaded bytes did not match signed manifest metadata."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    bytes: int
    sha256: str
    downloaded: bool
    attempts: int


def file_digest(path: Path, chunk_size: int = 1024 * 1024) -> tuple[int, str]:
    digest = sha256()
    size = 0
    with path.open('rb') as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def verify_file(path: Path, expected: ManifestFile) -> tuple[int, str]:
    size, digest = file_digest(path)
    if size != expected.bytes:
        raise IntegrityError(f'{expected.name}: expected {expected.bytes} bytes, received {size}')
    if digest != expected.sha256:
        raise IntegrityError(f'{expected.name}: SHA-256 mismatch')
    return size, digest


def download_file(
    base_url: str,
    expected: ManifestFile,
    destination_dir: Path,
    *,
    timeout_seconds: int = 30,
    retries: int = 3,
    backoff_seconds: float = 1,
    opener: Callable[..., Any] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    chunk_size: int = 1024 * 1024,
) -> DownloadResult:
    if Path(expected.name).name != expected.name:
        raise DownloadError('manifest filename must not contain a path')
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / expected.name
    if target.exists():
        try:
            size, digest = verify_file(target, expected)
            return DownloadResult(target, size, digest, False, 0)
        except IntegrityError:
            pass  # Keep it until a valid replacement is ready for atomic promotion.

    last_error: BaseException | None = None
    for attempt in range(1, retries + 2):
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f'.{expected.name}.', suffix='.part', dir=destination_dir
        )
        temporary = Path(temporary_name)
        digest = sha256()
        received = 0
        try:
            request = Request(
                urljoin(base_url, expected.name),
                headers={'Accept': 'application/octet-stream', 'User-Agent': 'VB-SIF/0.2'},
            )
            with os.fdopen(descriptor, 'wb') as output:
                descriptor = -1
                with opener(request, timeout=timeout_seconds) as response:
                    content_length = response.headers.get('Content-Length')
                    if content_length is not None and int(content_length) != expected.bytes:
                        raise IntegrityError(
                            f'{expected.name}: Content-Length differs from manifest'
                        )
                    while chunk := response.read(chunk_size):
                        received += len(chunk)
                        if received > expected.bytes:
                            raise IntegrityError(f'{expected.name}: download exceeds expected size')
                        digest.update(chunk)
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != expected.bytes:
                raise IntegrityError(
                    f'{expected.name}: expected {expected.bytes} bytes, received {received}'
                )
            hexadecimal = digest.hexdigest()
            if hexadecimal != expected.sha256:
                raise IntegrityError(f'{expected.name}: SHA-256 mismatch')
            os.replace(temporary, target)
            return DownloadResult(target, received, hexadecimal, True, attempt)
        except (HTTPError, URLError, TimeoutError, OSError, IntegrityError, ValueError) as error:
            last_error = error
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            if attempt <= retries:
                sleep(backoff_seconds * (2 ** (attempt - 1)) + jitter() * backoff_seconds)
        finally:
            temporary.unlink(missing_ok=True)
    if isinstance(last_error, IntegrityError):
        raise last_error
    raise DownloadError(f'{expected.name}: download failed after {retries + 1} attempts') from last_error
