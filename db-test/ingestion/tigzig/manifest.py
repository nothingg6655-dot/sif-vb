"""Fetch and validate the provider manifest without assuming its property layout."""
from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


PARQUET_FILES = {
    'meta': 'sif_meta.parquet',
    'nav': 'sif_nav.parquet',
    'aaum': 'sif_aaum.parquet',
}
SHA256_PATTERN = re.compile(r'^[0-9a-f]{64}$')
MAX_MANIFEST_BYTES = 1_000_000


class ManifestError(ValueError):
    """The remote manifest is missing required, trustworthy metadata."""


class ManifestFetchError(RuntimeError):
    """The manifest could not be fetched safely."""


@dataclass(frozen=True)
class ManifestFile:
    dataset: str
    name: str
    rows: int
    columns: tuple[str, ...]
    bytes: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    generated_at: datetime
    files: dict[str, ManifestFile]
    raw: dict[str, Any]


def _positive_integer(value: Any, path: str, *, allow_zero: bool = True) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ManifestError(f'{path} must be an integer >= {minimum}')
    return value


def parse_manifest(value: bytes | str | dict[str, Any]) -> Manifest:
    try:
        raw = json.loads(value) if isinstance(value, (bytes, str)) else value
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ManifestError('manifest is not valid UTF-8 JSON') from error
    if not isinstance(raw, dict):
        raise ManifestError('manifest root must be an object')

    generated_value = raw.get('generated_at')
    if not isinstance(generated_value, str):
        raise ManifestError('generated_at must be an ISO-8601 string')
    try:
        generated_at = datetime.fromisoformat(generated_value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ManifestError('generated_at must be a valid ISO-8601 timestamp') from error
    if generated_at.tzinfo is None:
        raise ManifestError('generated_at must include a timezone offset')

    file_entries = raw.get('files')
    if not isinstance(file_entries, list):
        raise ManifestError('files must be an array')
    entries_by_name: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(file_entries):
        if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
            raise ManifestError(f'files[{index}] must be an object with a name')
        name = entry['name']
        if name in entries_by_name:
            raise ManifestError(f'duplicate manifest filename: {name}')
        entries_by_name[name] = entry

    parsed: dict[str, ManifestFile] = {}
    for dataset, name in PARQUET_FILES.items():
        entry = entries_by_name.get(name)
        if entry is None:
            raise ManifestError(f'missing required file entry: {name}')
        columns = entry.get('columns')
        if not isinstance(columns, list) or not columns or any(
            not isinstance(column, str) or not column for column in columns
        ):
            raise ManifestError(f'{name}.columns must be a nonempty string array')
        if len(columns) != len(set(columns)):
            raise ManifestError(f'{name}.columns contains duplicates')
        digest = entry.get('sha256')
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ManifestError(f'{name}.sha256 must be 64 lowercase hexadecimal characters')
        parsed[dataset] = ManifestFile(
            dataset=dataset,
            name=name,
            rows=_positive_integer(entry.get('rows'), f'{name}.rows'),
            columns=tuple(columns),
            bytes=_positive_integer(entry.get('bytes'), f'{name}.bytes', allow_zero=False),
            sha256=digest,
        )
    return Manifest(generated_at=generated_at, files=parsed, raw=raw)


def find_manifest_file(manifest: Manifest, name: str, *, dataset: str) -> ManifestFile:
    """Return a validated auxiliary file entry from the inspected files array."""
    entries = manifest.raw.get('files')
    if not isinstance(entries, list):
        raise ManifestError('files must be an array')
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('name') == name]
    if len(matches) != 1:
        raise ManifestError(f'expected exactly one manifest entry for {name}')
    entry = matches[0]
    columns = entry.get('columns')
    digest = entry.get('sha256')
    if not isinstance(columns, list) or not columns or any(
        not isinstance(column, str) or not column for column in columns
    ):
        raise ManifestError(f'{name}.columns must be a nonempty string array')
    if len(columns) != len(set(columns)):
        raise ManifestError(f'{name}.columns contains duplicates')
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ManifestError(f'{name}.sha256 must be 64 lowercase hexadecimal characters')
    return ManifestFile(
        dataset=dataset,
        name=name,
        rows=_positive_integer(entry.get('rows'), f'{name}.rows'),
        columns=tuple(columns),
        bytes=_positive_integer(entry.get('bytes'), f'{name}.bytes', allow_zero=False),
        sha256=digest,
    )


def fetch_manifest(
    base_url: str,
    *,
    timeout_seconds: int = 30,
    opener: Callable[..., Any] = urlopen,
) -> Manifest:
    url = urljoin(base_url, 'manifest.json')
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'VB-SIF/0.2'})
    try:
        with opener(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ('application/json', 'text/json', 'text/plain'):
                raise ManifestFetchError(f'unexpected manifest content type: {content_type}')
            raw = response.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES:
                raise ManifestFetchError('manifest exceeds the 1 MB safety limit')
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise ManifestFetchError('manifest request failed') from error
    return parse_manifest(raw)
