"""Milestone 3 historical importer using COPY staging and set-based inserts."""
import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import sys
import time
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from .config import DatabaseConfig, PROJECT_ROOT, TigzigConfig
from .downloader import download_file
from .manifest import Manifest, ManifestFile, fetch_manifest, find_manifest_file
from .parquet import read_parquet
from .staging import copy_rows, create_staging_tables, staging_counts
from .analytics import create_affected_from_staging, refresh_affected_analytics
from .validator import ValidationReport, validate_datasets


class ImportError(RuntimeError):
    """The verified snapshots could not be imported consistently."""


@dataclass(frozen=True)
class PreparedSource:
    manifest: Manifest
    validation: ValidationReport
    tables: dict[str, Any]
    nav_upload_text: dict[tuple[int, date], str | None]
    files_processed: list[dict[str, Any]]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime, UUID, Path, Decimal)):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _boolean(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    raise ImportError(f'{field} must be true, false, or null')


def read_nav_upload_text(path: Path, expected: ManifestFile) -> dict[tuple[int, date], str | None]:
    result: dict[tuple[int, date], str | None] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != expected.columns:
            raise ImportError(f'{expected.name}: CSV columns differ from manifest')
        row_count = 0
        for row_count, row in enumerate(reader, 1):
            try:
                key = (int(row['scheme_code']), date.fromisoformat(row['date']))
            except (TypeError, ValueError) as error:
                raise ImportError(f'{expected.name}: invalid key at row {row_count}') from error
            if key in result:
                raise ImportError(f'{expected.name}: duplicate scheme/date key')
            result[key] = row.get('upload_ts') or None
    if row_count != expected.rows:
        raise ImportError(
            f'{expected.name}: manifest has {expected.rows} rows, CSV has {row_count}'
        )
    return result


def prepare_source(manifest: Manifest | None = None) -> PreparedSource:
    config = TigzigConfig.from_env()
    if manifest is None:
        manifest = fetch_manifest(config.base_url, timeout_seconds=config.timeout_seconds)
    tables = {}
    files_processed = []
    for dataset, expected in manifest.files.items():
        result = download_file(
            config.base_url, expected, config.download_dir,
            timeout_seconds=config.timeout_seconds, retries=config.retries,
            backoff_seconds=config.backoff_seconds,
        )
        table, _ = read_parquet(result.path, expected)
        tables[dataset] = table
        files_processed.append({
            'dataset': dataset, 'filename': expected.name, 'rows': expected.rows,
            'bytes': expected.bytes, 'sha256': expected.sha256,
            'downloaded': result.downloaded, 'download_attempts': result.attempts,
        })

    nav_csv = find_manifest_file(manifest, 'sif_nav.csv', dataset='nav_raw_timestamp')
    csv_download = download_file(
        config.base_url, nav_csv, config.download_dir,
        timeout_seconds=config.timeout_seconds, retries=config.retries,
        backoff_seconds=config.backoff_seconds,
    )
    files_processed.append({
        'dataset': nav_csv.dataset, 'filename': nav_csv.name, 'rows': nav_csv.rows,
        'bytes': nav_csv.bytes, 'sha256': nav_csv.sha256,
        'downloaded': csv_download.downloaded,
        'download_attempts': csv_download.attempts,
        'purpose': 'preserve exact provider publication timestamp text',
    })
    upload_text = read_nav_upload_text(csv_download.path, nav_csv)
    validation = validate_datasets(tables['meta'], tables['nav'], tables['aaum'])
    return PreparedSource(manifest, validation, tables, upload_text, files_processed)


def _sources_by_code(prepared: PreparedSource) -> dict[int, str]:
    values: dict[int, set[str]] = {}
    for row in prepared.validation.meta.valid_rows:
        values.setdefault(int(row['scheme_code']), set()).add(row['source'].strip())
    ambiguous = [code for code, sources in values.items() if len(sources) != 1]
    if ambiguous:
        raise ImportError(f'NAV/AAUM source cannot be resolved for scheme codes: {ambiguous[:10]}')
    return {code: next(iter(sources)) for code, sources in values.items()}


META_FIELDS = {
    'scheme_code', 'isin', 'isin2', 'scheme_name', 'amc', 'scheme_type', 'category',
    'category_sub', 'category_group_clean', 'category_group', 'scheme_plan',
    'scheme_option', 'first_date', 'last_date', 'is_active', 'is_stale', 'txic_code',
    'aaum_cr', 'aaum_quarter', 'aaum_quarter_end', 'nav_date', 'nav', 'source',
    'temp_sif_code', 'temp_plan_basis', 'temp_idcw_mode', 'temp_idcw_freq',
    'temp_n_nav_days',
}


def _meta_rows(prepared: PreparedSource):
    for row in prepared.validation.meta.valid_rows:
        extra = {key: _json_safe(value) for key, value in row.items() if key not in META_FIELDS}
        yield (
            row['source'].strip(), int(row['scheme_code']), row['amc'].strip(),
            row['scheme_name'].strip(), row.get('scheme_type'), row.get('category'),
            row.get('category_sub'), row.get('category_group_clean'), row.get('category_group'),
            row.get('scheme_plan'), row.get('scheme_option'), row.get('first_date'),
            row.get('last_date'), _boolean(row.get('is_active'), 'is_active'),
            _boolean(row.get('is_stale'), 'is_stale'), _decimal(row.get('nav')),
            row.get('nav_date'), row.get('aaum_quarter'), row.get('aaum_quarter_end'),
            _decimal(row.get('aaum_cr')), None if row.get('txic_code') is None else str(row['txic_code']),
            row.get('temp_sif_code'), row.get('temp_plan_basis'), row.get('temp_idcw_mode'),
            row.get('temp_idcw_freq'), row.get('temp_n_nav_days'), row.get('isin'),
            row.get('isin2'), Jsonb(extra),
        )


def _nav_rows(prepared: PreparedSource, sources: dict[int, str]):
    for row in prepared.validation.nav.valid_rows:
        code = int(row['scheme_code'])
        nav_date = row['date']
        raw = prepared.nav_upload_text.get((code, nav_date))
        if (code, nav_date) not in prepared.nav_upload_text:
            raise ImportError(f'NAV CSV has no timestamp record for scheme {code} on {nav_date}')
        timestamp = row.get('upload_ts')
        if timestamp is not None and timestamp.tzinfo is not None:
            raise ImportError('Parquet upload_ts unexpectedly contains timezone information')
        yield (
            row.get('source') or sources[code], code, nav_date, _decimal(row['nav']), raw, timestamp,
        )


def _aaum_rows(prepared: PreparedSource, sources: dict[int, str]):
    for row in prepared.validation.aaum.valid_rows:
        code = int(row['scheme_code'])
        yield (
            row.get('source') or sources[code], code, row['quarter_start'],
            row['aaum_quarter_end'], row['aaum_quarter'], _decimal(row['aaum_lakhs']),
            _decimal(row['aaum_cr']),
        )


def _rejection_rows(prepared: PreparedSource):
    for result in (prepared.validation.meta, prepared.validation.nav, prepared.validation.aaum):
        for issue in result.issues:
            reason = f'{issue.code}: {issue.reason} (source row {issue.row_number})'
            yield issue.dataset, Jsonb(_json_safe(issue.raw_record)), reason


def _insert_count(
    connection: psycopg.Connection,
    statement: str,
    parameters: tuple[Any, ...] | None = None,
) -> int:
    return connection.execute(statement, parameters, prepare=False).fetchone()[0]


def _create_run(connection: psycopg.Connection, prepared: PreparedSource) -> UUID:
    return connection.execute("""
        INSERT INTO public.tigzig_sync_runs(
            trigger_type, job_type, manifest_generated_at, manifest_metadata, files_processed
        ) VALUES ('manual', 'backfill', %s, %s, %s)
        RETURNING run_id
    """, (
        prepared.manifest.generated_at,
        Jsonb(_json_safe(prepared.manifest.raw)),
        Jsonb(prepared.files_processed),
    )).fetchone()[0]


def _mark_failed(connection: psycopg.Connection, run_id: UUID, started: float, error: Exception) -> None:
    duration = Decimal(str(round(time.monotonic() - started, 3)))
    connection.execute("""
        UPDATE public.tigzig_sync_runs
        SET status='failed', completed_at=now(), execution_duration_seconds=%s,
            error_message=%s
        WHERE run_id=%s AND status='running'
    """, (duration, f'{type(error).__name__}: {str(error)[:1800]}', run_id))


def import_prepared(prepared: PreparedSource) -> dict[str, Any]:
    started = time.monotonic()
    read_count = sum(result.input_rows for result in (
        prepared.validation.meta, prepared.validation.nav, prepared.validation.aaum
    ))
    valid_count = sum(len(result.valid_rows) for result in (
        prepared.validation.meta, prepared.validation.nav, prepared.validation.aaum
    ))
    rejected_count = prepared.validation.rejected_rows
    sources = _sources_by_code(prepared)

    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs(), autocommit=True) as connection:
        verify_target(connection)
        run_id = _create_run(connection, prepared)
        try:
            create_staging_tables(connection)
            copied = {
                'meta': copy_rows(connection, 'stg_tigzig_meta', _meta_rows(prepared)),
                'nav': copy_rows(connection, 'stg_tigzig_nav', _nav_rows(prepared, sources)),
                'aaum': copy_rows(connection, 'stg_tigzig_aaum', _aaum_rows(prepared, sources)),
                'rejections': copy_rows(
                    connection, 'stg_tigzig_rejections', _rejection_rows(prepared)
                ),
            }
            if copied != staging_counts(connection):
                raise ImportError('staging row-count reconciliation failed')
            if copied != {
                'meta': len(prepared.validation.meta.valid_rows),
                'nav': len(prepared.validation.nav.valid_rows),
                'aaum': len(prepared.validation.aaum.valid_rows),
                'rejections': rejected_count,
            }:
                raise ImportError('validated and staged row counts differ')

            with connection.transaction():
                create_affected_from_staging(connection, only_new=True)
                fund_houses = _insert_count(connection, """
                    WITH inserted AS (
                        INSERT INTO public.tigzig_fund_houses(original_amc_name)
                        SELECT DISTINCT amc FROM stg_tigzig_meta
                        ON CONFLICT (original_amc_name) DO NOTHING RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """)
                schemes = _insert_count(connection, """
                    WITH inserted AS (
                        INSERT INTO public.tigzig_schemes(
                            source, scheme_code, fund_house_id, scheme_name, scheme_type,
                            category, category_sub, category_group_clean, category_group,
                            scheme_plan, scheme_option, first_nav_date, last_nav_date,
                            is_active, is_stale, latest_nav, latest_nav_date,
                            latest_aaum_quarter, latest_aaum_quarter_end, latest_aaum_cr,
                            latest_aaum_lakhs, txic_code, temp_sif_code, temp_plan_basis,
                            temp_idcw_mode, temp_idcw_freq, temp_n_nav_days, source_metadata
                        )
                        SELECT m.source, m.scheme_code, f.fund_house_id, m.scheme_name,
                            m.scheme_type, m.category, m.category_sub, m.category_group_clean,
                            m.category_group, m.scheme_plan, m.scheme_option, m.first_nav_date,
                            m.last_nav_date, m.is_active, m.is_stale, m.latest_nav,
                            m.latest_nav_date, m.latest_aaum_quarter,
                            m.latest_aaum_quarter_end, m.latest_aaum_cr, a.aaum_lakhs,
                            m.txic_code, m.temp_sif_code, m.temp_plan_basis, m.temp_idcw_mode,
                            m.temp_idcw_freq, m.temp_n_nav_days, m.source_metadata
                        FROM stg_tigzig_meta m
                        JOIN public.tigzig_fund_houses f ON f.original_amc_name = m.amc
                        LEFT JOIN stg_tigzig_aaum a ON a.source=m.source
                            AND a.scheme_code=m.scheme_code
                            AND a.quarter_end=m.latest_aaum_quarter_end
                        ON CONFLICT (source, scheme_code) DO NOTHING RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """)
                isins = _insert_count(connection, """
                    WITH identifiers AS (
                        SELECT source, scheme_code, 'PRIMARY'::text AS identifier_type, isin
                        FROM stg_tigzig_meta WHERE isin IS NOT NULL AND btrim(isin) <> ''
                        UNION ALL
                        SELECT source, scheme_code, 'SECONDARY'::text, isin2
                        FROM stg_tigzig_meta WHERE isin2 IS NOT NULL AND btrim(isin2) <> ''
                    ), inserted AS (
                        INSERT INTO public.tigzig_scheme_isins(
                            source, scheme_code, identifier_type, isin
                        ) SELECT source, scheme_code, identifier_type, isin FROM identifiers
                        ON CONFLICT DO NOTHING RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """)
                nav = _insert_count(connection, """
                    WITH inserted AS (
                        INSERT INTO public.tigzig_scheme_nav(
                            source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
                        ) SELECT source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
                        FROM stg_tigzig_nav
                        ON CONFLICT (source, scheme_code, nav_date) DO NOTHING RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """)
                aaum = _insert_count(connection, """
                    WITH inserted AS (
                        INSERT INTO public.tigzig_scheme_aaum(
                            source, scheme_code, quarter_start, quarter_end, aaum_quarter,
                            aaum_lakhs, aaum_cr
                        ) SELECT source, scheme_code, quarter_start, quarter_end,
                            aaum_quarter, aaum_lakhs, aaum_cr FROM stg_tigzig_aaum
                        ON CONFLICT (source, scheme_code, quarter_end) DO NOTHING RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """)
                analytics = refresh_affected_analytics(connection)
                database_rejections = _insert_count(connection, """
                    WITH inserted AS (
                        INSERT INTO public.tigzig_ingestion_rejections(
                            run_id, dataset_name, raw_record, rejection_reason
                        ) SELECT %s, dataset_name, raw_record, rejection_reason
                        FROM stg_tigzig_rejections RETURNING 1
                    ) SELECT count(*)::integer FROM inserted
                """, (run_id,))

                source_inserted = schemes + nav + aaum
                skipped = valid_count - source_inserted
                if skipped < 0:
                    raise ImportError('insert metrics exceed validated source rows')
                duration = Decimal(str(round(time.monotonic() - started, 3)))
                connection.execute("""
                    UPDATE public.tigzig_sync_runs SET
                        completed_at=now(), status='succeeded', records_read=%s,
                        inserted_count=%s, updated_count=0, skipped_count=%s,
                        rejected_count=%s, execution_duration_seconds=%s, error_message=NULL
                    WHERE run_id=%s
                """, (read_count, source_inserted, skipped, rejected_count, duration, run_id))
                imported_at = datetime.now(timezone.utc)
                for dataset, file in prepared.manifest.files.items():
                    connection.execute("""
                        INSERT INTO public.tigzig_sync_file_versions(
                            dataset_name, filename, sha256, manifest_generated_at,
                            last_checked_at, last_successful_import_at, row_count,
                            last_import_run_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (dataset_name) DO UPDATE SET
                            filename=EXCLUDED.filename, sha256=EXCLUDED.sha256,
                            manifest_generated_at=EXCLUDED.manifest_generated_at,
                            last_checked_at=EXCLUDED.last_checked_at,
                            last_successful_import_at=EXCLUDED.last_successful_import_at,
                            row_count=EXCLUDED.row_count,
                            last_import_run_id=EXCLUDED.last_import_run_id
                    """, (
                        dataset, file.name, file.sha256, prepared.manifest.generated_at,
                        imported_at, imported_at, file.rows, run_id,
                    ))
            return {
                'run_id': str(run_id), 'records_read': read_count,
                'source_inserted': source_inserted, 'updated': 0, 'skipped': skipped,
                'rejected': rejected_count, 'fund_houses_inserted': fund_houses,
                'isins_inserted': isins, 'schemes_inserted': schemes,
                'nav_inserted': nav, 'aaum_inserted': aaum,
                'database_rejections_inserted': database_rejections,
                'analytics': analytics,
                'staged': copied,
            }
        except Exception as error:
            _mark_failed(connection, run_id, started, error)
            raise


def reconcile(prepared: PreparedSource, latest_run_id: str) -> dict[str, Any]:
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        counts = connection.execute("""
            SELECT
                (SELECT count(*) FROM public.tigzig_fund_houses),
                (SELECT count(*) FROM public.tigzig_schemes),
                (SELECT count(*) FROM public.tigzig_scheme_isins),
                (SELECT count(*) FROM public.tigzig_scheme_nav),
                (SELECT count(*) FROM public.tigzig_scheme_aaum)
        """).fetchone()
        nav_coverage = connection.execute(
            'SELECT min(nav_date), max(nav_date) FROM public.tigzig_scheme_nav'
        ).fetchone()
        aaum_coverage = connection.execute(
            'SELECT min(quarter_end), max(quarter_end) FROM public.tigzig_scheme_aaum'
        ).fetchone()
        duplicates = {
            'schemes': connection.execute("""
                SELECT count(*) FROM (
                    SELECT source, scheme_code FROM public.tigzig_schemes
                    GROUP BY source, scheme_code HAVING count(*) > 1
                ) d
            """).fetchone()[0],
            'nav': connection.execute("""
                SELECT count(*) FROM (
                    SELECT source, scheme_code, nav_date FROM public.tigzig_scheme_nav
                    GROUP BY source, scheme_code, nav_date HAVING count(*) > 1
                ) d
            """).fetchone()[0],
            'aaum': connection.execute("""
                SELECT count(*) FROM (
                    SELECT source, scheme_code, quarter_end FROM public.tigzig_scheme_aaum
                    GROUP BY source, scheme_code, quarter_end HAVING count(*) > 1
                ) d
            """).fetchone()[0],
        }
        orphans = {
            'nav': connection.execute("""
                SELECT count(*) FROM public.tigzig_scheme_nav n
                LEFT JOIN public.tigzig_schemes s USING (source, scheme_code)
                WHERE s.scheme_code IS NULL
            """).fetchone()[0],
            'aaum': connection.execute("""
                SELECT count(*) FROM public.tigzig_scheme_aaum a
                LEFT JOIN public.tigzig_schemes s USING (source, scheme_code)
                WHERE s.scheme_code IS NULL
            """).fetchone()[0],
            'isins': connection.execute("""
                SELECT count(*) FROM public.tigzig_scheme_isins i
                LEFT JOIN public.tigzig_schemes s USING (source, scheme_code)
                WHERE s.scheme_code IS NULL
            """).fetchone()[0],
        }
        conversion_mismatches = connection.execute("""
            SELECT count(*) FROM public.tigzig_scheme_aaum
            WHERE aaum_cr IS DISTINCT FROM round(aaum_lakhs / 100, 6)
        """).fetchone()[0]
        latest_rejections = connection.execute("""
            SELECT dataset_name, count(*) FROM public.tigzig_ingestion_rejections
            WHERE run_id=%s GROUP BY dataset_name ORDER BY dataset_name
        """, (latest_run_id,)).fetchall()
        nulls = connection.execute("""
            SELECT
                count(*) FILTER (WHERE latest_aaum_cr IS NULL) AS schemes_without_aaum,
                count(*) FILTER (WHERE txic_code IS NULL) AS schemes_without_txic
            FROM public.tigzig_schemes
        """).fetchone()
        meta_rejected = prepared.validation.meta.rejected_rows
        nav_rejected = prepared.validation.nav.rejected_rows
        aaum_rejected = prepared.validation.aaum.rejected_rows
        result = {
            'database_counts': {
                'fund_houses': counts[0], 'schemes': counts[1], 'isins': counts[2],
                'nav': counts[3], 'aaum': counts[4],
            },
            'manifest_reconciliation': {
                'meta': {
                    'manifest': prepared.manifest.files['meta'].rows,
                    'database_plus_latest_rejections': counts[1] + meta_rejected,
                },
                'nav': {
                    'manifest': prepared.manifest.files['nav'].rows,
                    'database_plus_latest_rejections': counts[3] + nav_rejected,
                },
                'aaum': {
                    'manifest': prepared.manifest.files['aaum'].rows,
                    'database_plus_latest_rejections': counts[4] + aaum_rejected,
                },
            },
            'date_coverage': {
                'nav': {'min': nav_coverage[0], 'max': nav_coverage[1]},
                'aaum_quarter_end': {'min': aaum_coverage[0], 'max': aaum_coverage[1]},
            },
            'duplicate_groups': duplicates,
            'orphan_rows': orphans,
            'aaum_conversion_mismatches': conversion_mismatches,
            'latest_run_rejections': dict(latest_rejections),
            'expected_nullable_fields': {
                'schemes_without_latest_aaum': nulls[0],
                'schemes_without_txic_code': nulls[1],
            },
        }
        if any(
            item['manifest'] != item['database_plus_latest_rejections']
            for item in result['manifest_reconciliation'].values()
        ):
            raise ImportError('manifest/database row reconciliation failed')
        if any(duplicates.values()) or any(orphans.values()) or conversion_mismatches:
            raise ImportError('database integrity reconciliation failed')
        return result


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(
        json.dumps(_json_safe(report), indent=2, allow_nan=False) + '\n', encoding='utf-8'
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat-test', action='store_true', help='Run the same snapshot twice')
    parser.add_argument(
        '--report', type=Path,
        default=PROJECT_ROOT / 'docs' / 'reports' / 'milestone-3-import-report.json',
    )
    arguments = parser.parse_args()
    try:
        prepared = prepare_source()
        runs = [import_prepared(prepared)]
        if arguments.repeat_test:
            runs.append(import_prepared(prepared))
        reconciliation = reconcile(prepared, runs[-1]['run_id'])
        report = {
            'manifest_generated_at': prepared.manifest.generated_at,
            'completed_at': datetime.now().astimezone(),
            'source_files': prepared.files_processed,
            'runs': runs,
            'reconciliation': reconciliation,
            'source_rejections': {
                result.dataset: [asdict(issue) for issue in result.issues]
                for result in (
                    prepared.validation.meta, prepared.validation.nav,
                    prepared.validation.aaum,
                )
            },
        }
        _write_report(arguments.report.resolve(), report)
        print(json.dumps(_json_safe({
            'report': arguments.report.resolve(), 'runs': runs,
            'reconciliation': reconciliation,
        }), indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(f'Historical import failed ({type(error).__name__}): {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
