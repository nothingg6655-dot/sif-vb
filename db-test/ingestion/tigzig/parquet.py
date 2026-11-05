"""Typed Parquet parsing plus schema and metadata inspection."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .manifest import ManifestFile


class ParquetError(ValueError):
    """The file is unreadable or inconsistent with the fetched manifest."""


@dataclass(frozen=True)
class ParquetInspection:
    rows: int
    columns: tuple[str, ...]
    types: dict[str, str]
    null_counts: dict[str, int]
    date_ranges: dict[str, dict[str, Any]]


def read_parquet(path: Path, expected: ManifestFile) -> tuple[pa.Table, ParquetInspection]:
    try:
        parquet_file = pq.ParquetFile(path)
        table = parquet_file.read()
    except (OSError, pa.ArrowException) as error:
        raise ParquetError(f'{expected.name}: invalid or truncated Parquet') from error
    columns = tuple(table.column_names)
    if table.num_rows != expected.rows:
        raise ParquetError(
            f'{expected.name}: manifest has {expected.rows} rows, Parquet has {table.num_rows}'
        )
    if columns != expected.columns:
        raise ParquetError(f'{expected.name}: Parquet columns differ from manifest')
    date_ranges = {}
    for field in table.schema:
        if pa.types.is_date(field.type) or pa.types.is_timestamp(field.type):
            values = pc.min_max(table[field.name]).as_py()
            date_ranges[field.name] = {'min': values['min'], 'max': values['max']}
    inspection = ParquetInspection(
        rows=table.num_rows,
        columns=columns,
        types={field.name: str(field.type) for field in table.schema},
        null_counts={name: table[name].null_count for name in columns},
        date_ranges=date_ranges,
    )
    return table, inspection
