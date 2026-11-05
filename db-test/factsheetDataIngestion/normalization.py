"""Normalize published factsheet values without inventing missing observations."""
from calendar import monthrange
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .date_utils import parse_flexible_date


def clean_numeric(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    if isinstance(value, (int, float, Decimal)):
        result = Decimal(str(value))
        return result if result.is_finite() else None
    text = str(value).strip().replace(',', '').replace('\u2212', '-').replace('\u2013', '-')
    text = re.sub(r'^(?:INR|Rs\.?|\u20b9|`|â‚¹)\s*', '', text, flags=re.I)
    # A number must be the complete value, apart from a stated unit/footnote.
    match = re.fullmatch(
        r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*'
        r'(?:%|x|years?|months?|days?|cr\.?|crores?|lakhs?|lacs?)?\s*[*#]*',
        text, flags=re.I,
    )
    if not match:
        return None
    try:
        result = Decimal(match[1])
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def inr_amount(value: Any) -> Decimal | None:
    """Convert an explicitly published monetary amount to rupees."""
    if not isinstance(value, str):
        return clean_numeric(value)
    match = re.match(
        r'^\s*(?:INR|Rs\.?|\u20b9|`)?\s*([\d,]+(?:\.\d+)?)'
        r'(?:\s*(lakhs?|lacs?|crores?|cr\b))?', value, re.I,
    )
    if not match:
        return None
    result = Decimal(match[1].replace(',', ''))
    unit = (match[2] or '').lower()
    if unit.startswith(('lakh', 'lac')):
        result *= 100000
    elif unit.startswith('cr'):
        result *= 10000000
    return result


def profile_values(data: dict) -> dict:
    """Read explicit labels, including dictionaries nested by an AMC adapter."""
    details = data.get('Fund Details') or {}
    fields = {}

    def visit(record):
        for key, value in record.items():
            if isinstance(value, dict):
                visit(value)
            elif value is not None and value != '':
                fields.setdefault(re.sub(r'[^a-z0-9]', '', key.lower()), value)

    visit(details)

    def get(*names):
        for name in names:
            value = fields.get(re.sub(r'[^a-z0-9]', '', name.lower()))
            if value is not None and value != '':
                return value
        return None

    return {
        'investment_objective': data.get('Investment Objective') or get('Investment Objective'),
        'launch_date': parse_flexible_date(get('Launch Date', 'Inception Date', 'Date of Allotment')),
        'published_benchmark': get('Benchmark', 'Benchmark Name', 'Benchmark Index'),
        'load_structure': get('Load Structure', 'Exit Load'),
        'minimum_investment_inr': inr_amount(get('Minimum Investment', 'Minimum Investment Amount', 'Min. Application Amount')),
        'accredited_minimum_inr': inr_amount(get('Accredited Investor Minimum')),
        'subscription_redemption_frequency': get('Subscription / Redemption Frequency'),
        'taxation': get('Taxation'),
        'portfolio_turnover_ratio': clean_numeric(get('Portfolio Turnover Ratio')),
        'strategy_description': get('Published Investment Strategy Details'),
    }


ALLOCATION_GROUPS = {
    'Portfolio Totals (%)': 'PORTFOLIO_TOTAL',
    'Sector Allocation (%)': 'SECTOR',
    'Market Capitalization (%)': 'MARKET_CAP',
    'Asset Allocation (%)': 'ASSET',
    'Rating Profile (%)': 'RATING',
    'Portfolio Summary (%)': 'PORTFOLIO_SUMMARY',
    'Derivative Totals (%)': 'DERIVATIVE_TOTAL',
    'Published Portfolio Aggregates (%)': 'PORTFOLIO_AGGREGATE',
}
PUBLISHED_ALLOCATION_GROUPS = {
    'Published Asset Allocation Rows': 'ASSET',
    'Published Derivative and Cash Rows': 'CASH_AND_DERIVATIVES',
    'Published Debt Category Rows': 'DEBT_CATEGORY',
    'Published Other Exposure Rows': 'OTHER_EXPOSURE',
}


def allocation_rows(data: dict) -> list[dict]:
    rows = {}
    for field, family in ALLOCATION_GROUPS.items():
        values = data.get(field) or {}
        if not isinstance(values, dict):
            continue
        for order, (bucket, published) in enumerate(values.items(), 1):
            # Gross and net market-cap allocations are separate published measures.
            measures = published.items() if isinstance(published, dict) else [(None, published)]
            for basis, value in measures:
                label = f'{bucket} ({basis})' if basis is not None else str(bucket)
                rows[(family, label)] = {
                    'family': family, 'label': label, 'percent': clean_numeric(value),
                    'order': order, 'page': None,
                    'extra': {'source_field': field, 'bucket_label': bucket,
                              'basis': basis, 'published_value': value},
                }
    for field, family in PUBLISHED_ALLOCATION_GROUPS.items():
        for order, published in enumerate(data.get(field) or [], 1):
            cells = published.get('cells') if isinstance(published, dict) else published
            if not isinstance(cells, list) or len(cells) < 2 or not cells[0]:
                continue
            label = str(cells[0])
            key = (family, label)
            if key in rows:
                rows[key]['extra']['published_row'] = published
                continue
            rows[key] = {
                'family': family, 'label': label, 'percent': clean_numeric(cells[-1]),
                'order': order, 'page': published.get('page') if isinstance(published, dict) else None,
                # Intermediate columns can have different units in different AMC PDFs.
                'extra': {'source_field': field, 'published_row': published},
            }
    return list(rows.values())


def availability_status(published: Any) -> str:
    status = str(published).strip().upper()
    if status in {'PUBLISHED', 'NOT_PUBLISHED', 'MISSING', 'PARSE_FAILED', 'NOT_APPLICABLE', 'UNKNOWN'}:
        return status
    if status in {'EXTRACTED', 'AVAILABLE'}:
        return 'PUBLISHED'
    if status.startswith('NOT NORMALIZED'):
        return 'PARSE_FAILED'
    return 'UNKNOWN'


def quant_rows(data: dict) -> list[dict]:
    result = []
    for field, family in [('Statistical Debt Indicators', 'STATISTICAL_DEBT'),
                          ('Debt Portfolio Quants', 'DEBT_PORTFOLIO_QUANT'),
                          ('Published Portfolio Quants', 'PUBLISHED_PORTFOLIO_QUANT')]:
        groups = data.get(field) or {}
        groups = groups if isinstance(groups, list) else [groups]
        for group in groups:
            if not isinstance(group, dict):
                continue
            for name, value in group.items():
                unit = '%' if '%' in name or isinstance(value, str) and '%' in value else None
                if '(x)' in name.lower():
                    unit = 'x'
                elif isinstance(value, str):
                    match = re.search(r'\b(years?|months?|days?)\b', value, re.I)
                    if match:
                        unit = match[1].lower()
                result.append({'family': family, 'name': str(name), 'numeric': clean_numeric(value),
                               'text': str(value) if value is not None else None, 'unit': unit,
                               'attributes': {'source_field': field, 'published_value': value}})
    return result


def _plan(label: str) -> str | None:
    if re.search(r'\b(dir|direct)\b', label, re.I):
        return 'Direct'
    if re.search(r'\b(reg|regular)\b', label, re.I):
        return 'Regular'
    return None


def performance_rows(data: dict) -> list[dict]:
    """Normalize stated returns; preserve annualized labels and original row values."""
    result = []
    for field, family in [('Monthly Rolling Performance', 'MONTHLY_ROLLING'), ('Scheme Performance', 'SCHEME')]:
        published_rows = data.get(field) or []
        benchmark_rows = [row for row in published_rows if isinstance(row, dict)
                          and 'benchmark' in str(row.get('Scheme', '')).lower()]
        for published in published_rows:
            if not isinstance(published, dict):
                continue
            period = published.get('Months') or published.get('Period')
            scheme = str(published.get('Scheme') or '')
            is_benchmark = 'benchmark' in scheme.lower()
            benchmark = next((clean_numeric(v) for k, v in published.items()
                              if 'benchmark' in k.lower() and 'value of' not in k.lower()), None)
            for label, value in published.items():
                low = label.lower()
                if label in {'Period', 'Months', 'Scheme'} or 'benchmark' in low or 'value of' in low or low.startswith('ptp'):
                    continue
                numeric = clean_numeric(value)
                if numeric is None:
                    continue
                plan = _plan(label + ' ' + scheme)
                comparison = benchmark
                if comparison is None:
                    comparison = next((clean_numeric(row.get(label)) for row in benchmark_rows), None)
                option = 'Growth' if re.search(r'\bgrowth\b|\(G\)', label + ' ' + scheme, re.I) else None
                start = end = None
                if family == 'MONTHLY_ROLLING':
                    parsed = parse_flexible_date(str(period))
                    if parsed:
                        start = parsed.replace(day=1)
                        end = date(parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1])
                result.append({
                    'family': 'BENCHMARK' if is_benchmark else family,
                    'period': str(period or label), 'plan': plan,
                    'option': option, 'start': start, 'end': end,
                    'scheme_return': None if is_benchmark else numeric,
                    'benchmark_return': numeric if is_benchmark else comparison,
                    'extra': {'source_field': field, 'published_metric': label, 'published_row': published},
                })
    return result


def idcw_rows(data: dict) -> list[dict]:
    result = []
    for plan, events in (data.get('IDCW History') or {}).items():
        if not isinstance(events, list):
            continue
        for event in events:
            when = parse_flexible_date(event.get('Record Date') or event.get('Date'))
            amount = next((clean_numeric(value) for key, value in event.items()
                           if 'idcw' in key.lower() or 'per unit' in key.lower()), None)
            if when:
                result.append({'date': when, 'plan': plan, 'amount': amount, 'attributes': event})
    return result
