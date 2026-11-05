"""Read successful import checkpoints without advancing successful hashes."""
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg

from database.migrate import verify_target
from .config import DatabaseConfig
from .manifest import Manifest


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
