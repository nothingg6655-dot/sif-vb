"""Download and validate current TIGZIG snapshots without importing any rows."""
import argparse
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
from typing import Any

from .config import PROJECT_ROOT, TigzigConfig
from .downloader import DownloadResult, download_file, verify_file
from .manifest import fetch_manifest
from .parquet import read_parquet
from .source_versions import detect_changes
from .validator import validate_datasets


def _json(value: Any):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f'Cannot serialize {type(value).__name__}')


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (date, datetime, Decimal, Path)):
        return _json(value)
    return value


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + '.tmp')
    temporary.write_text(
        json.dumps(_json_safe(report), indent=2, allow_nan=False) + '\n', encoding='utf-8'
    )
    temporary.replace(report_path)


def verify_current_source(report_path: Path) -> dict[str, Any]:
    config = TigzigConfig.from_env()
    manifest = fetch_manifest(config.base_url, timeout_seconds=config.timeout_seconds)
    changes = {status.dataset: status for status in detect_changes(manifest)}
    tables = {}
    file_reports = {}
    for dataset, expected in manifest.files.items():
        status = changes[dataset]
        target = config.download_dir / expected.name
        if status.changed:
            download = download_file(
                config.base_url,
                expected,
                config.download_dir,
                timeout_seconds=config.timeout_seconds,
                retries=config.retries,
                backoff_seconds=config.backoff_seconds,
            )
        elif target.exists():
            size, digest = verify_file(target, expected)
            download = DownloadResult(target, size, digest, False, 0)
        else:
            # The successful database checkpoint is authoritative for change
            # detection. An unchanged source is never downloaded just to rebuild
            # an optional local inspection cache.
            file_reports[dataset] = {
                'filename': expected.name,
                'change_status': 'unchanged',
                'successful_sha256': status.successful_sha256,
                'downloaded': False,
                'inspection': 'skipped: unchanged successful source has no local cache',
            }
            continue
        table, inspection = read_parquet(download.path, expected)
        tables[dataset] = table
        file_reports[dataset] = {
            'filename': expected.name,
            'change_status': 'changed' if changes[dataset].changed else 'unchanged',
            'successful_sha256': changes[dataset].successful_sha256,
            'downloaded': download.downloaded,
            'attempts': download.attempts,
            'bytes': download.bytes,
            'sha256': download.sha256,
            'rows': inspection.rows,
            'columns': list(inspection.columns),
            'types': inspection.types,
            'null_counts': inspection.null_counts,
            'date_ranges': inspection.date_ranges,
        }
    if set(tables) != {'meta', 'nav', 'aaum'}:
        report = {
            'generated_at': manifest.generated_at,
            'verified_at': datetime.now().astimezone(),
            'base_url': config.base_url,
            'files': file_reports,
            'validation': {'status': 'skipped: not all snapshots were locally available'},
            'total_rejected_rows': 0,
            'database_import_performed': False,
            'successful_hashes_advanced': False,
        }
        _write_report(report_path, report)
        return report

    validation = validate_datasets(tables['meta'], tables['nav'], tables['aaum'])
    validation_report = {}
    for result in (validation.meta, validation.nav, validation.aaum):
        validation_report[result.dataset] = {
            'input_rows': result.input_rows,
            'valid_rows': len(result.valid_rows),
            'rejected_rows': result.rejected_rows,
            'duplicate_keys': result.duplicate_keys,
            'rejections': [asdict(issue) for issue in result.issues],
        }
    report = {
        'generated_at': manifest.generated_at,
        'verified_at': datetime.now().astimezone(),
        'base_url': config.base_url,
        'files': file_reports,
        'validation': validation_report,
        'total_rejected_rows': validation.rejected_rows,
        'database_import_performed': False,
        'successful_hashes_advanced': False,
    }
    _write_report(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--report', type=Path,
        default=PROJECT_ROOT / 'docs' / 'reports' / 'milestone-2-download-verification.json',
    )
    arguments = parser.parse_args()
    try:
        report = verify_current_source(arguments.report.resolve())
    except Exception as error:
        print(f'Source verification failed ({type(error).__name__}): {error}', file=sys.stderr)
        return 1
    print(json.dumps({
        'report': str(arguments.report.resolve()),
        'generated_at': report['generated_at'],
        'files': {
            key: {
                'rows': value['rows'],
                'downloaded': value['downloaded'],
                'change_status': value['change_status'],
            } for key, value in report['files'].items()
        },
        'total_rejected_rows': report['total_rejected_rows'],
    }, indent=2, default=_json))
    return 0 if report['total_rejected_rows'] == 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
