"""Read successful import checkpoints without advancing successful hashes."""
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from database.migrate import verify_target
from .config import DatabaseConfig
from .manifest import Manifest, find_manifest_file


@dataclass(frozen=True)
class ChangeStatus:
    dataset: str
    filename: str
    changed: bool
    successful_sha256: str | None


def classify_changes(manifest: Manifest, stored: dict[str, str | None]) -> list[ChangeStatus]:
    return [
        ChangeStatus(dataset, file.name, stored.get(dataset) != file.sha256, stored.get(dataset))
        for dataset, file in manifest.files.items()
    ]


def changed_source_files(manifest: Manifest, connection: psycopg.Connection) -> list[str]:
    """Compare all imported files with committed checkpoints while holding the ingestion lock."""
    rows = connection.execute("""
        SELECT versions.dataset_name, versions.sha256, runs.files_processed
        FROM public.tigzig_sync_file_versions AS versions
        LEFT JOIN public.tigzig_sync_runs AS runs
          ON runs.run_id = versions.last_import_run_id AND runs.status = 'succeeded'
    """).fetchall()
    stored = {dataset: digest for dataset, digest, _files in rows}
    changed = [item.filename for item in classify_changes(manifest, stored) if item.changed]
    # The CSV preserves exact publication timestamp text and can change even if
    # the three Parquet checksums do not. Its committed hash lives in the run audit.
    nav_csv = find_manifest_file(manifest, 'sif_nav.csv', dataset='nav_raw_timestamp')
    nav_files = next((files for dataset, _digest, files in rows if dataset == 'nav'), None)
    imported_csv_hash = next((
        item.get('sha256') for item in nav_files or []
        if isinstance(item, dict) and item.get('filename') == nav_csv.name
    ), None)
    if imported_csv_hash != nav_csv.sha256:
        changed.append(nav_csv.name)
    return changed


def detect_changes(manifest: Manifest, *, checked_at: datetime | None = None) -> list[ChangeStatus]:
    checked_at = checked_at or datetime.now(timezone.utc)
    config = DatabaseConfig.from_env()
    with psycopg.connect(**config.connection_kwargs()) as connection:
        verify_target(connection)
        stored = dict(connection.execute(
            'SELECT dataset_name, sha256 FROM public.tigzig_sync_file_versions'
        ).fetchall())
        statuses = classify_changes(manifest, stored)
        for dataset, file in manifest.files.items():
            # This records only that the remote manifest was checked. It never
            # advances the successful SHA, row count, manifest time, or import run.
            connection.execute("""
                INSERT INTO public.tigzig_sync_file_versions(dataset_name, filename, last_checked_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (dataset_name) DO UPDATE
                SET filename = EXCLUDED.filename, last_checked_at = EXCLUDED.last_checked_at
            """, (dataset, file.name, checked_at))
        connection.commit()
    return statuses
