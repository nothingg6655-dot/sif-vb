"""Validate all source rows and retain every rejection with its raw record."""
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa


REQUIRED_COLUMNS = {
    'meta': {'scheme_code', 'scheme_name', 'amc', 'source'},
    'nav': {'scheme_code', 'date', 'nav'},
    'aaum': {
        'scheme_code', 'aaum_quarter_end', 'aaum_cr', 'aaum_lakhs', 'quarter_start'
    },
}


class DatasetSchemaError(ValueError):
    """A required source column disappeared."""


@dataclass(frozen=True)
class ValidationIssue:
    dataset: str
    row_number: int
    code: str
    reason: str
    raw_record: dict[str, Any]


@dataclass
class DatasetValidation:
    dataset: str
    input_rows: int
    valid_rows: list[dict[str, Any]] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    duplicate_keys: int = 0

    @property
    def rejected_rows(self) -> int:
        return len(self.issues)


@dataclass(frozen=True)
class ValidationReport:
    meta: DatasetValidation
    nav: DatasetValidation
    aaum: DatasetValidation

    @property
    def rejected_rows(self) -> int:
        return self.meta.rejected_rows + self.nav.rejected_rows + self.aaum.rejected_rows


def _records(table: pa.Table, dataset: str) -> list[dict[str, Any]]:
    missing = REQUIRED_COLUMNS[dataset] - set(table.column_names)
    if missing:
        raise DatasetSchemaError(f'{dataset}: missing required columns: {sorted(missing)}')
    return table.to_pylist()


def _scheme_code(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, Decimal) and value == value.to_integral_value() and value > 0:
        return int(value)
    return None


def _nonempty(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _issue(result: DatasetValidation, row_number: int, code: str, reason: str, row: dict[str, Any]):
    result.issues.append(ValidationIssue(result.dataset, row_number, code, reason, row))


def _duplicate_rows(
    rows: list[dict[str, Any]], key_function
) -> tuple[Counter, int]:
    keys = [key_function(row) for row in rows]
    counts = Counter(key for key in keys if key is not None)
    return counts, sum(count - 1 for count in counts.values() if count > 1)


def validate_meta(table: pa.Table) -> DatasetValidation:
    rows = _records(table, 'meta')
    result = DatasetValidation('meta', len(rows))
    counts, result.duplicate_keys = _duplicate_rows(
        rows,
        lambda row: (_nonempty(row.get('source')), _scheme_code(row.get('scheme_code')))
        if _nonempty(row.get('source')) and _scheme_code(row.get('scheme_code')) else None,
    )
    for row_number, row in enumerate(rows, 1):
        code = _scheme_code(row.get('scheme_code'))
        source = _nonempty(row.get('source'))
        if code is None:
            _issue(result, row_number, 'INVALID_SCHEME_CODE', 'scheme_code must be a positive integer', row)
        elif source is None:
            _issue(result, row_number, 'MISSING_SOURCE', 'source must be populated', row)
        elif _nonempty(row.get('scheme_name')) is None:
            _issue(result, row_number, 'MISSING_SCHEME_NAME', 'scheme_name must be populated', row)
        elif _nonempty(row.get('amc')) is None:
            _issue(result, row_number, 'MISSING_AMC', 'amc must be populated', row)
        elif counts[(source, code)] > 1:
            _issue(result, row_number, 'DUPLICATE_KEY', 'duplicate (source, scheme_code)', row)
        else:
            result.valid_rows.append(row)
    return result


def validate_nav(
    table: pa.Table,
    known_schemes: set[tuple[str, int]],
    *,
    default_source: str = 'SIF',
) -> DatasetValidation:
    rows = _records(table, 'nav')
    result = DatasetValidation('nav', len(rows))
    source_for = lambda row: _nonempty(row.get('source')) or default_source
    key_for = lambda row: (
        source_for(row), _scheme_code(row.get('scheme_code')), _date(row.get('date'))
    ) if _scheme_code(row.get('scheme_code')) and _date(row.get('date')) else None
    counts, result.duplicate_keys = _duplicate_rows(rows, key_for)
    for row_number, row in enumerate(rows, 1):
        code = _scheme_code(row.get('scheme_code'))
        nav_date = _date(row.get('date'))
        nav = _decimal(row.get('nav'))
        source = source_for(row)
        key = (source, code, nav_date) if code and nav_date else None
        if code is None:
            _issue(result, row_number, 'INVALID_SCHEME_CODE', 'scheme_code must be a positive integer', row)
        elif nav_date is None:
            _issue(result, row_number, 'INVALID_NAV_DATE', 'date must be a valid calendar date', row)
        elif nav is None or nav <= 0:
            _issue(result, row_number, 'INVALID_NAV', 'nav must be a positive finite number', row)
        elif (source, code) not in known_schemes:
            _issue(result, row_number, 'UNKNOWN_SCHEME', 'NAV scheme is absent from metadata', row)
        elif key is not None and counts[key] > 1:
            _issue(result, row_number, 'DUPLICATE_KEY', 'duplicate (source, scheme_code, date)', row)
        else:
            result.valid_rows.append(row)
    return result


def validate_aaum(
    table: pa.Table,
    known_schemes: set[tuple[str, int]],
    *,
    default_source: str = 'SIF',
) -> DatasetValidation:
    # Current snapshots use this name for the same quarterly-average crore value.
    if 'aaum_cr' not in table.column_names and 'aaum_cr_quarterly_avg' in table.column_names:
        table = table.append_column('aaum_cr', table['aaum_cr_quarterly_avg'])
    rows = _records(table, 'aaum')
    result = DatasetValidation('aaum', len(rows))
    source_for = lambda row: _nonempty(row.get('source')) or default_source
    key_for = lambda row: (
        source_for(row), _scheme_code(row.get('scheme_code')),
        _date(row.get('aaum_quarter_end')),
    ) if _scheme_code(row.get('scheme_code')) and _date(row.get('aaum_quarter_end')) else None
    counts, result.duplicate_keys = _duplicate_rows(rows, key_for)
    for row_number, row in enumerate(rows, 1):
        code = _scheme_code(row.get('scheme_code'))
        quarter_start = _date(row.get('quarter_start'))
        quarter_end = _date(row.get('aaum_quarter_end'))
        lakhs = _decimal(row.get('aaum_lakhs'))
        crores = _decimal(row.get('aaum_cr'))
        source = source_for(row)
        key = (source, code, quarter_end) if code and quarter_end else None
        if code is None:
            _issue(result, row_number, 'INVALID_SCHEME_CODE', 'scheme_code must be a positive integer', row)
        elif quarter_start is None or quarter_end is None or quarter_start > quarter_end:
            _issue(result, row_number, 'INVALID_QUARTER', 'quarter dates are invalid or reversed', row)
        elif lakhs is None or crores is None or lakhs < 0 or crores < 0:
            _issue(result, row_number, 'INVALID_AAUM', 'AAUM values must be nonnegative finite numbers', row)
        elif abs(crores - (lakhs / Decimal(100))) > Decimal('0.000001'):
            _issue(result, row_number, 'AAUM_CONVERSION', 'aaum_cr must equal aaum_lakhs / 100', row)
        elif (source, code) not in known_schemes:
            _issue(result, row_number, 'UNKNOWN_SCHEME', 'AAUM scheme is absent from metadata', row)
        elif key is not None and counts[key] > 1:
            _issue(result, row_number, 'DUPLICATE_KEY', 'duplicate (source, scheme_code, quarter_end)', row)
        else:
            result.valid_rows.append(row)
    return result


def validate_datasets(meta: pa.Table, nav: pa.Table, aaum: pa.Table) -> ValidationReport:
    meta_result = validate_meta(meta)
    known_schemes = {
        (_nonempty(row.get('source')) or '', _scheme_code(row.get('scheme_code')) or -1)
        for row in meta_result.valid_rows
    }
    sources = {source for source, _ in known_schemes}
    default_source = next(iter(sources)) if len(sources) == 1 else 'SIF'
    return ValidationReport(
        meta=meta_result,
        nav=validate_nav(nav, known_schemes, default_source=default_source),
        aaum=validate_aaum(aaum, known_schemes, default_source=default_source),
    )
