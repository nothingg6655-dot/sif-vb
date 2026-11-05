"""Milestone 4 snapshot synchronization with set-based conditional UPSERTs."""
import argparse
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from .config import DatabaseConfig, PROJECT_ROOT
from .importer import (
    PreparedSource, _aaum_rows, _json_safe, _mark_failed, _meta_rows,
    _nav_rows, _rejection_rows, _sources_by_code, _write_report,
    prepare_source, reconcile,
)
from .staging import copy_rows, create_staging_tables, staging_counts
from .analytics import create_affected_from_staging, refresh_affected_analytics


class SyncError(RuntimeError):
    """A validated snapshot could not be synchronized consistently."""


def stage_prepared(connection: psycopg.Connection, prepared: PreparedSource) -> dict[str, int]:
    """COPY validated source rows outside the source mutation transaction."""
    sources = _sources_by_code(prepared)
    create_staging_tables(connection)
    copied = {
        'meta': copy_rows(connection, 'stg_tigzig_meta', _meta_rows(prepared)),
        'nav': copy_rows(connection, 'stg_tigzig_nav', _nav_rows(prepared, sources)),
        'aaum': copy_rows(connection, 'stg_tigzig_aaum', _aaum_rows(prepared, sources)),
        'rejections': copy_rows(
            connection, 'stg_tigzig_rejections', _rejection_rows(prepared)
        ),
    }
    expected = {
        'meta': len(prepared.validation.meta.valid_rows),
        'nav': len(prepared.validation.nav.valid_rows),
        'aaum': len(prepared.validation.aaum.valid_rows),
        'rejections': prepared.validation.rejected_rows,
    }
    if copied != expected or copied != staging_counts(connection):
        raise SyncError('validated and staged row counts differ')
    return copied


def _delta_counts(connection: psycopg.Connection, statement: str) -> dict[str, int]:
    row = connection.execute(statement, prepare=False).fetchone()
    return {'NEW': row[0], 'CHANGED': row[1], 'UNCHANGED': row[2], 'INVALID': 0}


def classify_staged(
    connection: psycopg.Connection,
    invalid: dict[str, int] | None = None,
) -> dict[str, dict[str, int]]:
    """Classify every staged row by complete business value, never by latest date."""
    invalid = invalid or {}
    result = {
        'meta': _delta_counts(connection, """
            SELECT
              count(*) FILTER (WHERE t.scheme_code IS NULL)::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  h.original_amc_name, t.scheme_name, t.scheme_type, t.category,
                  t.category_sub, t.category_group_clean, t.category_group,
                  t.scheme_plan, t.scheme_option, t.first_nav_date, t.last_nav_date,
                  t.is_active, t.is_stale, t.latest_nav, t.latest_nav_date,
                  t.latest_aaum_quarter, t.latest_aaum_quarter_end,
                  t.latest_aaum_cr, t.latest_aaum_lakhs, t.txic_code,
                  t.temp_sif_code, t.temp_plan_basis, t.temp_idcw_mode,
                  t.temp_idcw_freq, t.temp_n_nav_days, t.source_metadata,
                  ip.isin, isec.isin
                ) IS DISTINCT FROM ROW(
                  m.amc, m.scheme_name, m.scheme_type, m.category,
                  m.category_sub, m.category_group_clean, m.category_group,
                  m.scheme_plan, m.scheme_option, m.first_nav_date, m.last_nav_date,
                  m.is_active, m.is_stale, m.latest_nav, m.latest_nav_date,
                  m.latest_aaum_quarter, m.latest_aaum_quarter_end,
                  m.latest_aaum_cr, a.aaum_lakhs, m.txic_code,
                  m.temp_sif_code, m.temp_plan_basis, m.temp_idcw_mode,
                  m.temp_idcw_freq, m.temp_n_nav_days, m.source_metadata,
                  nullif(btrim(m.isin), ''), nullif(btrim(m.isin2), '')
                )
              )::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  h.original_amc_name, t.scheme_name, t.scheme_type, t.category,
                  t.category_sub, t.category_group_clean, t.category_group,
                  t.scheme_plan, t.scheme_option, t.first_nav_date, t.last_nav_date,
                  t.is_active, t.is_stale, t.latest_nav, t.latest_nav_date,
                  t.latest_aaum_quarter, t.latest_aaum_quarter_end,
                  t.latest_aaum_cr, t.latest_aaum_lakhs, t.txic_code,
                  t.temp_sif_code, t.temp_plan_basis, t.temp_idcw_mode,
                  t.temp_idcw_freq, t.temp_n_nav_days, t.source_metadata,
                  ip.isin, isec.isin
                ) IS NOT DISTINCT FROM ROW(
                  m.amc, m.scheme_name, m.scheme_type, m.category,
                  m.category_sub, m.category_group_clean, m.category_group,
                  m.scheme_plan, m.scheme_option, m.first_nav_date, m.last_nav_date,
                  m.is_active, m.is_stale, m.latest_nav, m.latest_nav_date,
                  m.latest_aaum_quarter, m.latest_aaum_quarter_end,
                  m.latest_aaum_cr, a.aaum_lakhs, m.txic_code,
                  m.temp_sif_code, m.temp_plan_basis, m.temp_idcw_mode,
                  m.temp_idcw_freq, m.temp_n_nav_days, m.source_metadata,
                  nullif(btrim(m.isin), ''), nullif(btrim(m.isin2), '')
                )
              )::integer
            FROM stg_tigzig_meta m
            LEFT JOIN public.tigzig_schemes t
              ON t.source=m.source AND t.scheme_code=m.scheme_code
            LEFT JOIN public.tigzig_fund_houses h ON h.fund_house_id=t.fund_house_id
            LEFT JOIN public.tigzig_scheme_isins ip
              ON ip.source=m.source AND ip.scheme_code=m.scheme_code
             AND ip.identifier_type='PRIMARY'
            LEFT JOIN public.tigzig_scheme_isins isec
              ON isec.source=m.source AND isec.scheme_code=m.scheme_code
             AND isec.identifier_type='SECONDARY'
            LEFT JOIN stg_tigzig_aaum a
              ON a.source=m.source AND a.scheme_code=m.scheme_code
             AND a.quarter_end=m.latest_aaum_quarter_end
        """),
        'nav': _delta_counts(connection, """
            SELECT
              count(*) FILTER (WHERE t.scheme_code IS NULL)::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  t.nav, t.upload_ts_raw, t.upload_ts_ist
                ) IS DISTINCT FROM ROW(s.nav, s.upload_ts_raw, s.upload_ts_ist)
              )::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  t.nav, t.upload_ts_raw, t.upload_ts_ist
                ) IS NOT DISTINCT FROM ROW(s.nav, s.upload_ts_raw, s.upload_ts_ist)
              )::integer
            FROM stg_tigzig_nav s
            LEFT JOIN public.tigzig_scheme_nav t
              ON t.source=s.source AND t.scheme_code=s.scheme_code
             AND t.nav_date=s.nav_date
        """),
        'aaum': _delta_counts(connection, """
            SELECT
              count(*) FILTER (WHERE t.scheme_code IS NULL)::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  t.quarter_start, t.aaum_quarter, t.aaum_lakhs, t.aaum_cr
                ) IS DISTINCT FROM ROW(
                  s.quarter_start, s.aaum_quarter, s.aaum_lakhs, s.aaum_cr
                )
              )::integer,
              count(*) FILTER (
                WHERE t.scheme_code IS NOT NULL AND ROW(
                  t.quarter_start, t.aaum_quarter, t.aaum_lakhs, t.aaum_cr
                ) IS NOT DISTINCT FROM ROW(
                  s.quarter_start, s.aaum_quarter, s.aaum_lakhs, s.aaum_cr
                )
              )::integer
            FROM stg_tigzig_aaum s
            LEFT JOIN public.tigzig_scheme_aaum t
              ON t.source=s.source AND t.scheme_code=s.scheme_code
             AND t.quarter_end=s.quarter_end
        """),
        'isins': _delta_counts(connection, """
            WITH desired AS (
              SELECT source, scheme_code, 'PRIMARY'::text identifier_type,
                     nullif(btrim(isin), '') isin
              FROM stg_tigzig_meta
              UNION ALL
              SELECT source, scheme_code, 'SECONDARY', nullif(btrim(isin2), '')
              FROM stg_tigzig_meta
            ), compared AS (
              SELECT d.*, t.scheme_code existing_scheme_code, t.isin existing_isin
              FROM desired d
              LEFT JOIN public.tigzig_scheme_isins t
                ON t.source=d.source AND t.scheme_code=d.scheme_code
               AND t.identifier_type=d.identifier_type
              WHERE d.isin IS NOT NULL OR t.scheme_code IS NOT NULL
            )
            SELECT
              count(*) FILTER (
                WHERE existing_scheme_code IS NULL AND isin IS NOT NULL
              )::integer,
              count(*) FILTER (
                WHERE existing_scheme_code IS NOT NULL
                  AND existing_isin IS DISTINCT FROM isin
              )::integer,
              count(*) FILTER (
                WHERE existing_scheme_code IS NOT NULL
                  AND existing_isin IS NOT DISTINCT FROM isin
              )::integer
            FROM compared
        """),
    }
    for dataset in ('meta', 'nav', 'aaum'):
        result[dataset]['INVALID'] = invalid.get(dataset, 0)
    return result


def _affected(connection: psycopg.Connection, statement: str) -> int:
    return connection.execute(statement, prepare=False).fetchone()[0]


def apply_staged_changes(
    connection: psycopg.Connection,
    deltas: dict[str, dict[str, int]],
) -> dict[str, int]:
    """Apply NEW and CHANGED rows; identical rows never receive UPDATEs."""
    fund_houses = _affected(connection, """
        WITH changed AS (
          INSERT INTO public.tigzig_fund_houses(original_amc_name)
          SELECT DISTINCT amc FROM stg_tigzig_meta
          ON CONFLICT (original_amc_name) DO NOTHING RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    schemes_affected = _affected(connection, """
        WITH changed AS (
          INSERT INTO public.tigzig_schemes AS t(
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
            m.latest_nav_date, m.latest_aaum_quarter, m.latest_aaum_quarter_end,
            m.latest_aaum_cr, a.aaum_lakhs, m.txic_code, m.temp_sif_code,
            m.temp_plan_basis, m.temp_idcw_mode, m.temp_idcw_freq,
            m.temp_n_nav_days, m.source_metadata
          FROM stg_tigzig_meta m
          JOIN public.tigzig_fund_houses f ON f.original_amc_name=m.amc
          LEFT JOIN stg_tigzig_aaum a
            ON a.source=m.source AND a.scheme_code=m.scheme_code
           AND a.quarter_end=m.latest_aaum_quarter_end
          ON CONFLICT (source, scheme_code) DO UPDATE SET
            fund_house_id=EXCLUDED.fund_house_id,
            scheme_name=EXCLUDED.scheme_name, scheme_type=EXCLUDED.scheme_type,
            category=EXCLUDED.category, category_sub=EXCLUDED.category_sub,
            category_group_clean=EXCLUDED.category_group_clean,
            category_group=EXCLUDED.category_group,
            scheme_plan=EXCLUDED.scheme_plan, scheme_option=EXCLUDED.scheme_option,
            first_nav_date=EXCLUDED.first_nav_date, last_nav_date=EXCLUDED.last_nav_date,
            is_active=EXCLUDED.is_active, is_stale=EXCLUDED.is_stale,
            latest_nav=EXCLUDED.latest_nav, latest_nav_date=EXCLUDED.latest_nav_date,
            latest_aaum_quarter=EXCLUDED.latest_aaum_quarter,
            latest_aaum_quarter_end=EXCLUDED.latest_aaum_quarter_end,
            latest_aaum_cr=EXCLUDED.latest_aaum_cr,
            latest_aaum_lakhs=EXCLUDED.latest_aaum_lakhs,
            txic_code=EXCLUDED.txic_code, temp_sif_code=EXCLUDED.temp_sif_code,
            temp_plan_basis=EXCLUDED.temp_plan_basis,
            temp_idcw_mode=EXCLUDED.temp_idcw_mode,
            temp_idcw_freq=EXCLUDED.temp_idcw_freq,
            temp_n_nav_days=EXCLUDED.temp_n_nav_days,
            source_metadata=EXCLUDED.source_metadata, updated_at=now()
          WHERE ROW(
            t.fund_house_id, t.scheme_name, t.scheme_type, t.category,
            t.category_sub, t.category_group_clean, t.category_group,
            t.scheme_plan, t.scheme_option, t.first_nav_date, t.last_nav_date,
            t.is_active, t.is_stale, t.latest_nav, t.latest_nav_date,
            t.latest_aaum_quarter, t.latest_aaum_quarter_end,
            t.latest_aaum_cr, t.latest_aaum_lakhs, t.txic_code,
            t.temp_sif_code, t.temp_plan_basis, t.temp_idcw_mode,
            t.temp_idcw_freq, t.temp_n_nav_days, t.source_metadata
          ) IS DISTINCT FROM ROW(
            EXCLUDED.fund_house_id, EXCLUDED.scheme_name, EXCLUDED.scheme_type,
            EXCLUDED.category, EXCLUDED.category_sub,
            EXCLUDED.category_group_clean, EXCLUDED.category_group,
            EXCLUDED.scheme_plan, EXCLUDED.scheme_option,
            EXCLUDED.first_nav_date, EXCLUDED.last_nav_date,
            EXCLUDED.is_active, EXCLUDED.is_stale, EXCLUDED.latest_nav,
            EXCLUDED.latest_nav_date, EXCLUDED.latest_aaum_quarter,
            EXCLUDED.latest_aaum_quarter_end, EXCLUDED.latest_aaum_cr,
            EXCLUDED.latest_aaum_lakhs, EXCLUDED.txic_code,
            EXCLUDED.temp_sif_code, EXCLUDED.temp_plan_basis,
            EXCLUDED.temp_idcw_mode, EXCLUDED.temp_idcw_freq,
            EXCLUDED.temp_n_nav_days, EXCLUDED.source_metadata
          ) RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    duplicate_isins = connection.execute("""
        WITH desired AS (
          SELECT source, btrim(isin) isin
          FROM stg_tigzig_meta WHERE isin IS NOT NULL AND btrim(isin) <> ''
          UNION ALL
          SELECT source, btrim(isin2)
          FROM stg_tigzig_meta WHERE isin2 IS NOT NULL AND btrim(isin2) <> ''
        )
        SELECT count(*) FROM (
          SELECT source, isin FROM desired GROUP BY source, isin HAVING count(*) > 1
        ) duplicates
    """).fetchone()[0]
    if duplicate_isins:
        raise SyncError('staged metadata assigns one ISIN to multiple relationships')
    isins_removed = _affected(connection, """
        WITH desired AS (
          SELECT source, scheme_code, 'PRIMARY'::text identifier_type,
                 nullif(btrim(isin), '') isin
          FROM stg_tigzig_meta
          UNION ALL
          SELECT source, scheme_code, 'SECONDARY', nullif(btrim(isin2), '')
          FROM stg_tigzig_meta
        ), changed AS (
          DELETE FROM public.tigzig_scheme_isins t USING desired d
          WHERE t.source=d.source AND t.scheme_code=d.scheme_code
            AND t.identifier_type=d.identifier_type AND d.isin IS NULL
          RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    _affected(connection, """
        WITH desired AS (
          SELECT source, scheme_code, 'PRIMARY'::text identifier_type, btrim(isin) isin
          FROM stg_tigzig_meta WHERE isin IS NOT NULL AND btrim(isin) <> ''
          UNION ALL
          SELECT source, scheme_code, 'SECONDARY', btrim(isin2)
          FROM stg_tigzig_meta WHERE isin2 IS NOT NULL AND btrim(isin2) <> ''
        ), changed AS (
          UPDATE public.tigzig_scheme_isins t SET
            isin=concat('__vb_sif_sync__', md5(
              t.source || ':' || t.scheme_code::text || ':' || t.identifier_type
            ))
          FROM desired d
          WHERE t.source=d.source AND t.scheme_code=d.scheme_code
            AND t.identifier_type=d.identifier_type
            AND t.isin IS DISTINCT FROM d.isin
          RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    isins_written = _affected(connection, """
        WITH desired AS (
          SELECT source, scheme_code, 'PRIMARY'::text identifier_type, btrim(isin) isin
          FROM stg_tigzig_meta WHERE isin IS NOT NULL AND btrim(isin) <> ''
          UNION ALL
          SELECT source, scheme_code, 'SECONDARY', btrim(isin2)
          FROM stg_tigzig_meta WHERE isin2 IS NOT NULL AND btrim(isin2) <> ''
        ), changed AS (
          INSERT INTO public.tigzig_scheme_isins AS t(
            source, scheme_code, identifier_type, isin
          ) SELECT source, scheme_code, identifier_type, isin FROM desired
          ON CONFLICT (source, scheme_code, identifier_type) DO UPDATE SET
            isin=EXCLUDED.isin, updated_at=now()
          WHERE t.isin IS DISTINCT FROM EXCLUDED.isin
          RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    nav_affected = _affected(connection, """
        WITH changed AS (
          INSERT INTO public.tigzig_scheme_nav AS t(
            source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
          ) SELECT source, scheme_code, nav_date, nav, upload_ts_raw, upload_ts_ist
            FROM stg_tigzig_nav
          ON CONFLICT (source, scheme_code, nav_date) DO UPDATE SET
            nav=EXCLUDED.nav, upload_ts_raw=EXCLUDED.upload_ts_raw,
            upload_ts_ist=EXCLUDED.upload_ts_ist, updated_at=now()
          WHERE ROW(t.nav, t.upload_ts_raw, t.upload_ts_ist)
            IS DISTINCT FROM ROW(
              EXCLUDED.nav, EXCLUDED.upload_ts_raw, EXCLUDED.upload_ts_ist
            ) RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    aaum_affected = _affected(connection, """
        WITH changed AS (
          INSERT INTO public.tigzig_scheme_aaum AS t(
            source, scheme_code, quarter_start, quarter_end, aaum_quarter,
            aaum_lakhs, aaum_cr
          ) SELECT source, scheme_code, quarter_start, quarter_end, aaum_quarter,
                   aaum_lakhs, aaum_cr FROM stg_tigzig_aaum
          ON CONFLICT (source, scheme_code, quarter_end) DO UPDATE SET
            quarter_start=EXCLUDED.quarter_start,
            aaum_quarter=EXCLUDED.aaum_quarter,
            aaum_lakhs=EXCLUDED.aaum_lakhs, aaum_cr=EXCLUDED.aaum_cr,
            updated_at=now()
          WHERE ROW(t.quarter_start, t.aaum_quarter, t.aaum_lakhs, t.aaum_cr)
            IS DISTINCT FROM ROW(
              EXCLUDED.quarter_start, EXCLUDED.aaum_quarter,
              EXCLUDED.aaum_lakhs, EXCLUDED.aaum_cr
            ) RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """)
    expected = {
        'isins': deltas['isins']['NEW'] + deltas['isins']['CHANGED'],
        'nav': deltas['nav']['NEW'] + deltas['nav']['CHANGED'],
        'aaum': deltas['aaum']['NEW'] + deltas['aaum']['CHANGED'],
    }
    actual = {
        'isins': isins_written + isins_removed,
        'nav': nav_affected, 'aaum': aaum_affected,
    }
    if actual != expected:
        raise SyncError(f'classification/mutation mismatch: expected {expected}, got {actual}')
    if not deltas['meta']['NEW'] <= schemes_affected <= (
        deltas['meta']['NEW'] + deltas['meta']['CHANGED']
    ):
        raise SyncError('scheme mutation count is inconsistent with metadata deltas')
    return {
        'fund_houses_inserted': fund_houses,
        'schemes_inserted': deltas['meta']['NEW'],
        'schemes_updated': schemes_affected - deltas['meta']['NEW'],
        'isins_inserted': deltas['isins']['NEW'],
        'isins_updated': deltas['isins']['CHANGED'] - isins_removed,
        'isins_removed': isins_removed,
        'nav_inserted': deltas['nav']['NEW'],
        'nav_updated': deltas['nav']['CHANGED'],
        'aaum_inserted': deltas['aaum']['NEW'],
        'aaum_updated': deltas['aaum']['CHANGED'],
    }


def _create_sync_run(
    connection: psycopg.Connection,
    prepared: PreparedSource,
    trigger_type: str,
    job_type: str,
    job_identifier: str,
) -> UUID:
    metadata = dict(prepared.manifest.raw)
    metadata['_vb_sif'] = {'job_identifier': job_identifier}
    return connection.execute("""
        INSERT INTO public.tigzig_sync_runs(
          trigger_type, job_type, manifest_generated_at, manifest_metadata, files_processed
        ) VALUES (%s, %s, %s, %s, %s) RETURNING run_id
    """, (
        trigger_type,
        job_type,
        prepared.manifest.generated_at,
        Jsonb(_json_safe(metadata)),
        Jsonb(prepared.files_processed),
    )).fetchone()[0]


def _advance_checkpoints(
    connection: psycopg.Connection,
    prepared: PreparedSource,
    run_id: UUID,
) -> None:
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


def execute_sync_transaction(
    connection: psycopg.Connection,
    prepared: PreparedSource,
    run_id: UUID,
    deltas: dict[str, dict[str, int]],
    started: float,
    *,
    failpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Mutate sources, audit the run, and advance hashes in one transaction."""
    create_affected_from_staging(connection, only_new=False)
    mutations = apply_staged_changes(connection, deltas)
    analytics = refresh_affected_analytics(connection)
    rejection_count = prepared.validation.rejected_rows
    database_rejections = connection.execute("""
        WITH changed AS (
          INSERT INTO public.tigzig_ingestion_rejections(
            run_id, dataset_name, raw_record, rejection_reason
          ) SELECT %s, dataset_name, raw_record, rejection_reason
            FROM stg_tigzig_rejections RETURNING 1
        ) SELECT count(*)::integer FROM changed
    """, (run_id,), prepare=False).fetchone()[0]
    inserted = sum(deltas[name]['NEW'] for name in ('meta', 'nav', 'aaum'))
    updated = sum(deltas[name]['CHANGED'] for name in ('meta', 'nav', 'aaum'))
    skipped = sum(deltas[name]['UNCHANGED'] for name in ('meta', 'nav', 'aaum'))
    records_read = inserted + updated + skipped + rejection_count
    duration = Decimal(str(round(time.monotonic() - started, 3)))
    connection.execute("""
        UPDATE public.tigzig_sync_runs SET
          completed_at=now(), status='succeeded', records_read=%s,
          inserted_count=%s, updated_count=%s, skipped_count=%s,
          rejected_count=%s, execution_duration_seconds=%s, error_message=NULL
        WHERE run_id=%s AND status='running'
    """, (
        records_read, inserted, updated, skipped, rejection_count, duration, run_id,
    ))
    _advance_checkpoints(connection, prepared, run_id)
    if failpoint is not None:
        # Tests inject interruption here to prove source writes, run completion,
        # and successful hashes are one atomic unit.
        failpoint()
    return {
        'run_id': str(run_id), 'records_read': records_read,
        'inserted': inserted, 'updated': updated, 'skipped': skipped,
        'rejected': rejection_count,
        'database_rejections_inserted': database_rejections,
        'deltas': deltas, 'mutations': mutations, 'analytics': analytics,
    }


def sync_prepared(
    prepared: PreparedSource,
    *,
    trigger_type: str = 'manual',
    job_type: str = 'sync',
    job_identifier: str = 'manual-sync',
    failpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    invalid = {
        'meta': prepared.validation.meta.rejected_rows,
        'nav': prepared.validation.nav.rejected_rows,
        'aaum': prepared.validation.aaum.rejected_rows,
    }
    with psycopg.connect(
        **DatabaseConfig.from_env().connection_kwargs(), autocommit=True
    ) as connection:
        verify_target(connection)
        run_id = _create_sync_run(
            connection, prepared, trigger_type, job_type, job_identifier
        )
        try:
            copied = stage_prepared(connection, prepared)
            deltas = classify_staged(connection, invalid)
            with connection.transaction():
                result = execute_sync_transaction(
                    connection, prepared, run_id, deltas, started,
                    failpoint=failpoint,
                )
            result['staged'] = copied
            return result
        except Exception as error:
            _mark_failed(connection, run_id, started, error)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--report', type=Path,
        default=PROJECT_ROOT / 'docs' / 'reports' / 'milestone-4-sync-report.json',
    )
    arguments = parser.parse_args()
    try:
        prepared = prepare_source()
        run = sync_prepared(prepared)
        reconciliation = reconcile(prepared, run['run_id'])
        report = {
            'manifest_generated_at': prepared.manifest.generated_at,
            'completed_at': datetime.now().astimezone(),
            'source_files': prepared.files_processed,
            'run': run,
            'reconciliation': reconciliation,
        }
        _write_report(arguments.report.resolve(), report)
        print(json.dumps(_json_safe({
            'report': arguments.report.resolve(),
            'run': run,
            'reconciliation': reconciliation,
        }), indent=2, allow_nan=False))
        return 0
    except Exception as error:
        print(f'Incremental sync failed ({type(error).__name__}): {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
