"""Lock-aware TIGZIG worker and Milestone 5 manual command entry point."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import sys
import time
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from .config import DatabaseConfig, PROJECT_ROOT, SchedulerConfig, TigzigConfig
from .importer import _json_safe, import_prepared, prepare_source
from .manifest import fetch_manifest, find_manifest_file
from .source_versions import changed_source_files
from .sync import sync_prepared
from .verify_source import verify_current_source


LOCK_NAME = 'VB-SIF:TIGZIG:INGESTION'
ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(LOCK_NAME.encode()).digest()[:8], byteorder='big', signed=True
)
IST = ZoneInfo('Asia/Kolkata')


def log_event(event: str, level: str = 'info', **fields: Any) -> None:
    payload = {
        'timestamp': datetime.now(IST).isoformat(),
        'level': level,
        'event': event,
        **_json_safe(fields),
    }
    print(json.dumps(payload, allow_nan=False, separators=(',', ':')), flush=True)


@contextmanager
def ingestion_lock(connection: psycopg.Connection | None = None):
    """Hold the stable session advisory lock for the entire worker operation."""
    owned = connection is None
    if connection is None:
        connection = psycopg.connect(
            **DatabaseConfig.from_env().connection_kwargs(), autocommit=True
        )
    verify_target(connection)
    acquired = connection.execute(
        'SELECT pg_try_advisory_lock(%s)', (ADVISORY_LOCK_KEY,)
    ).fetchone()[0]
    try:
        yield acquired, connection
    finally:
        if acquired:
            connection.execute('SELECT pg_advisory_unlock(%s)', (ADVISORY_LOCK_KEY,))
        if owned:
            connection.close()


def mark_abandoned_runs(connection: psycopg.Connection) -> int:
    """Finalize runs left open by a terminated lock-owning worker."""
    return connection.execute("""
        WITH changed AS (
          UPDATE public.tigzig_sync_runs SET
            status='interrupted', completed_at=now(),
            execution_duration_seconds=greatest(
              0, extract(epoch FROM (now() - started_at))
            ),
            error_message='Worker stopped before completing the run; recovered on restart'
          WHERE status='running'
          RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """).fetchone()[0]


def _terminal_run(
    *,
    trigger_type: str,
    job_type: str,
    job_identifier: str,
    status: str,
    started: float,
    error_message: str | None = None,
    manifest_generated_at: Any = None,
    manifest_metadata: dict[str, Any] | None = None,
    files_processed: list[dict[str, Any]] | None = None,
    records_read: int = 0,
    rejected_count: int = 0,
) -> str:
    duration = Decimal(str(round(time.monotonic() - started, 3)))
    metadata = dict(manifest_metadata or {})
    metadata['_vb_sif'] = {'job_identifier': job_identifier}
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        run_id = connection.execute("""
            INSERT INTO public.tigzig_sync_runs(
              trigger_type, job_type, completed_at, status, manifest_generated_at,
              manifest_metadata, files_processed, records_read, rejected_count,
              execution_duration_seconds, error_message
            ) VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING run_id
        """, (
            trigger_type, job_type, status, manifest_generated_at, Jsonb(metadata),
            Jsonb(files_processed or []), records_read, rejected_count, duration,
            error_message[:1800] if error_message else None,
        )).fetchone()[0]
    return str(run_id)


def retry_delays(
    config: SchedulerConfig,
    *,
    jitter: Callable[[], float] = random.random,
) -> list[float]:
    return [
        min(config.max_backoff_seconds, config.backoff_seconds * (2 ** attempt))
        + jitter() * config.jitter_seconds
        for attempt in range(config.max_retries)
    ]


def run_sync_job(
    job_identifier: str,
    trigger_type: str = 'scheduled',
    *,
    job_type: str = 'sync',
    recover_abandoned: bool = False,
    config: SchedulerConfig | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    config = config or SchedulerConfig.from_env()
    started = time.monotonic()
    log_event('job_started', job_identifier=job_identifier, trigger_type=trigger_type)
    with ingestion_lock() as (acquired, lock_connection):
        if not acquired:
            run_id = _terminal_run(
                trigger_type=trigger_type, job_type=job_type,
                job_identifier=job_identifier, status='skipped', started=started,
                error_message='Another TIGZIG importer holds the advisory lock',
            )
            result = {'status': 'skipped', 'run_id': run_id, 'reason': 'lock_unavailable'}
            log_event('job_skipped', job_identifier=job_identifier, **result)
            return result
        if recover_abandoned:
            interrupted = mark_abandoned_runs(lock_connection)
            log_event(
                'abandoned_runs_recovered', job_identifier=job_identifier,
                interrupted_count=interrupted,
            )

        delays = retry_delays(config, jitter=jitter)
        for attempt in range(1, config.max_retries + 2):
            prepared = None
            attempt_started = time.monotonic()
            try:
                source_config = TigzigConfig.from_env()
                manifest = fetch_manifest(
                    source_config.base_url, timeout_seconds=source_config.timeout_seconds,
                )
                if job_type != 'revalidate':
                    changed = changed_source_files(manifest, lock_connection)
                    if not changed:
                        files = [*manifest.files.values(), find_manifest_file(
                            manifest, 'sif_nav.csv', dataset='nav_raw_timestamp',
                        )]
                        run_id = _terminal_run(
                            trigger_type=trigger_type, job_type=job_type,
                            job_identifier=job_identifier, status='succeeded', started=started,
                            manifest_generated_at=manifest.generated_at,
                            manifest_metadata={**manifest.raw, '_source_unchanged': True},
                            files_processed=[{
                                'dataset': file.dataset, 'filename': file.name,
                                'sha256': file.sha256, 'rows': file.rows,
                                'downloaded': False,
                            } for file in files],
                        )
                        result = {
                            'status': 'unchanged', 'run_id': run_id,
                            'reason': 'source_unchanged', 'inserted': 0, 'updated': 0,
                            'attempt': attempt,
                        }
                        log_event('job_unchanged', job_identifier=job_identifier, **result)
                        return result
                    log_event('source_changed', job_identifier=job_identifier, files=changed)
                prepared = prepare_source(manifest=manifest)
                result = sync_prepared(
                    prepared, trigger_type=trigger_type, job_type=job_type,
                    job_identifier=job_identifier,
                )
                result['status'] = 'succeeded'
                result['attempt'] = attempt
                log_event(
                    'job_succeeded', job_identifier=job_identifier,
                    manifest_generated_at=prepared.manifest.generated_at,
                    files=prepared.files_processed, **result,
                )
                return result
            except Exception as error:
                if prepared is None:
                    _terminal_run(
                        trigger_type=trigger_type, job_type=job_type,
                        job_identifier=job_identifier, status='failed',
                        started=attempt_started,
                        error_message=f'{type(error).__name__}: {error}',
                    )
                log_event(
                    'job_attempt_failed', level='error', job_identifier=job_identifier,
                    attempt=attempt, max_attempts=config.max_retries + 1,
                    error_type=type(error).__name__, error=str(error)[:1800],
                )
                if attempt > config.max_retries:
                    log_event(
                        'job_failed', level='error', job_identifier=job_identifier,
                        attempts=attempt,
                    )
                    raise
                delay = delays[attempt - 1]
                log_event(
                    'job_retry_scheduled', level='warning',
                    job_identifier=job_identifier, next_attempt=attempt + 1,
                    delay_seconds=round(delay, 3),
                )
                sleep(delay)
    raise AssertionError('unreachable worker state')


def run_backfill(config: SchedulerConfig | None = None) -> dict[str, Any]:
    config = config or SchedulerConfig.from_env()
    with ingestion_lock() as (acquired, _connection):
        if not acquired:
            return {'status': 'skipped', 'reason': 'lock_unavailable'}
        prepared = prepare_source()
        result = import_prepared(prepared)
        result['status'] = 'succeeded'
        log_event('backfill_succeeded', **result)
        return result


def run_manifest_check() -> dict[str, Any]:
    started = time.monotonic()
    with ingestion_lock() as (acquired, _connection):
        if not acquired:
            run_id = _terminal_run(
                trigger_type='manual', job_type='manifest',
                job_identifier='manual-manifest', status='skipped', started=started,
                error_message='Another TIGZIG importer holds the advisory lock',
            )
            return {'status': 'skipped', 'run_id': run_id}
        report_path = PROJECT_ROOT / 'docs' / 'reports' / 'manual-manifest-check.json'
        try:
            report = verify_current_source(report_path)
            files = [
                {
                    'dataset': dataset,
                    'filename': detail['filename'],
                    'sha256': detail['sha256'],
                    'downloaded': detail['downloaded'],
                    'download_attempts': detail['attempts'],
                    'change_status': detail['change_status'],
                }
                for dataset, detail in report['files'].items()
            ]
            run_id = _terminal_run(
                trigger_type='manual', job_type='manifest',
                job_identifier='manual-manifest', status='succeeded', started=started,
                manifest_generated_at=report['generated_at'],
                manifest_metadata={'base_url': report['base_url']},
                files_processed=files,
                records_read=sum(
                    validation['input_rows'] for validation in report['validation'].values()
                ),
                rejected_count=report['total_rejected_rows'],
            )
            result = {
                'status': 'succeeded', 'run_id': run_id,
                'manifest_generated_at': report['generated_at'],
                'rejected': report['total_rejected_rows'], 'report': report_path,
            }
            log_event('manifest_check_succeeded', **result)
            return result
        except Exception as error:
            _terminal_run(
                trigger_type='manual', job_type='manifest',
                job_identifier='manual-manifest', status='failed', started=started,
                error_message=f'{type(error).__name__}: {error}',
            )
            raise


def sync_status() -> dict[str, Any]:
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        latest = connection.execute("""
            SELECT run_id, trigger_type, job_type, started_at, completed_at, status,
                   records_read, inserted_count, updated_count, skipped_count,
                   rejected_count, error_message
            FROM public.tigzig_sync_runs ORDER BY started_at DESC LIMIT 1
        """).fetchone()
        successful = connection.execute("""
            SELECT run_id, completed_at FROM public.tigzig_sync_runs
            WHERE status='succeeded' AND job_type IN ('backfill', 'sync', 'revalidate')
            ORDER BY completed_at DESC LIMIT 1
        """).fetchone()
        versions = connection.execute("""
            SELECT dataset_name, filename, manifest_generated_at,
                   last_checked_at, last_successful_import_at, row_count
            FROM public.tigzig_sync_file_versions ORDER BY dataset_name
        """).fetchall()
    return {
        'last_attempt': latest,
        'last_successful_sync': successful,
        'datasets': versions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        'command', choices=('manifest', 'backfill', 'sync', 'revalidate', 'scheduler', 'status')
    )
    arguments = parser.parse_args()
    try:
        if arguments.command == 'manifest':
            result = run_manifest_check()
        elif arguments.command == 'backfill':
            result = run_backfill()
        elif arguments.command == 'sync':
            result = run_sync_job('manual-sync', 'manual')
        elif arguments.command == 'revalidate':
            result = run_sync_job(
                'manual-revalidate', 'manual', job_type='revalidate'
            )
        elif arguments.command == 'status':
            result = sync_status()
        else:
            from .scheduler import run_scheduler
            run_scheduler()
            return 0
        print(json.dumps(_json_safe(result), indent=2, allow_nan=False))
        return 0
    except Exception as error:
        log_event(
            'command_failed', level='error', command=arguments.command,
            error_type=type(error).__name__, error=str(error)[:1800],
        )
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
