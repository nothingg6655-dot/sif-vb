"""Run-scoped PostgreSQL temporary tables loaded through COPY."""
from collections.abc import Iterable
from typing import Any

import psycopg


STAGING_DDL = """
CREATE TEMP TABLE stg_tigzig_meta (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    amc TEXT NOT NULL,
    scheme_name TEXT NOT NULL,
    scheme_type TEXT,
    category TEXT,
    category_sub TEXT,
    category_group_clean TEXT,
    category_group TEXT,
    scheme_plan TEXT,
    scheme_option TEXT,
    first_nav_date DATE,
    last_nav_date DATE,
    is_active BOOLEAN,
    is_stale BOOLEAN,
    latest_nav NUMERIC(24,10),
    latest_nav_date DATE,
    latest_aaum_quarter TEXT,
    latest_aaum_quarter_end DATE,
    latest_aaum_cr NUMERIC(24,6),
    txic_code TEXT,
    temp_sif_code TEXT,
    temp_plan_basis TEXT,
    temp_idcw_mode TEXT,
    temp_idcw_freq TEXT,
    temp_n_nav_days INTEGER,
    isin TEXT,
    isin2 TEXT,
    source_metadata JSONB NOT NULL
) ON COMMIT PRESERVE ROWS;

CREATE TEMP TABLE stg_tigzig_nav (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    nav_date DATE NOT NULL,
    nav NUMERIC(24,10) NOT NULL,
    upload_ts_raw TEXT,
    upload_ts_ist TIMESTAMP WITHOUT TIME ZONE
) ON COMMIT PRESERVE ROWS;

CREATE TEMP TABLE stg_tigzig_aaum (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    quarter_start DATE NOT NULL,
    quarter_end DATE NOT NULL,
    aaum_quarter TEXT NOT NULL,
    aaum_lakhs NUMERIC(26,4) NOT NULL,
    aaum_cr NUMERIC(24,6) NOT NULL
) ON COMMIT PRESERVE ROWS;

CREATE TEMP TABLE stg_tigzig_rejections (
    dataset_name TEXT NOT NULL,
    raw_record JSONB NOT NULL,
    rejection_reason TEXT NOT NULL
) ON COMMIT PRESERVE ROWS;
"""


COPY_COLUMNS = {
    'stg_tigzig_meta': (
        'source', 'scheme_code', 'amc', 'scheme_name', 'scheme_type', 'category',
        'category_sub', 'category_group_clean', 'category_group', 'scheme_plan',
        'scheme_option', 'first_nav_date', 'last_nav_date', 'is_active', 'is_stale',
        'latest_nav', 'latest_nav_date', 'latest_aaum_quarter',
        'latest_aaum_quarter_end', 'latest_aaum_cr', 'txic_code', 'temp_sif_code',
        'temp_plan_basis', 'temp_idcw_mode', 'temp_idcw_freq', 'temp_n_nav_days',
        'isin', 'isin2', 'source_metadata',
    ),
    'stg_tigzig_nav': (
        'source', 'scheme_code', 'nav_date', 'nav', 'upload_ts_raw', 'upload_ts_ist',
    ),
    'stg_tigzig_aaum': (
        'source', 'scheme_code', 'quarter_start', 'quarter_end', 'aaum_quarter',
        'aaum_lakhs', 'aaum_cr',
    ),
    'stg_tigzig_rejections': ('dataset_name', 'raw_record', 'rejection_reason'),
}


def create_staging_tables(connection: psycopg.Connection) -> None:
    for statement in STAGING_DDL.split(';'):
        if statement.strip():
            connection.execute(statement, prepare=False)


def copy_rows(
    connection: psycopg.Connection,
    table: str,
    rows: Iterable[tuple[Any, ...]],
) -> int:
    columns = COPY_COLUMNS.get(table)
    if columns is None:
        raise ValueError(f'Unsupported staging table: {table}')
    count = 0
    statement = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    with connection.cursor().copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def staging_counts(connection: psycopg.Connection) -> dict[str, int]:
    return {
        dataset: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        for dataset, table in {
            'meta': 'stg_tigzig_meta',
            'nav': 'stg_tigzig_nav',
            'aaum': 'stg_tigzig_aaum',
            'rejections': 'stg_tigzig_rejections',
        }.items()
    }
