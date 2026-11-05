import calendar
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg


def env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path('.env').read_text(encoding='utf-8').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = value
    return values


def returned(start, end):
    if start is None or end is None or start[0] >= end[0]:
        return None
    return (((end[1] / start[1]) - 1) * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


reference = json.loads(Path(
    'backend/tests/fixtures/sif360-heatmap-12m-2026-10-14.json'
).read_text(encoding='utf-8'))
settings = env()
series: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
with psycopg.connect(
    host=settings['PGHOST'], port=settings['PGPORT'], dbname=settings['PGDATABASE'],
    user=settings['PGUSER'], password=settings['PGPASSWORD'],
) as connection:
    with connection.cursor() as cursor:
        cursor.execute('''
          SELECT c.reference_fund_id,n.nav_date,n.nav
          FROM app_performance_config c
          JOIN tigzig_scheme_nav n
            ON (n.source,n.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
          WHERE n.nav_date <= DATE '2026-10-14'
          ORDER BY 1,2
        ''')
        for fund_id, observation_date, nav in cursor.fetchall():
            series[fund_id].append((observation_date, nav))


def last(rows, predicate):
    matches = [row for row in rows if predicate(row[0])]
    return matches[-1] if matches else None


def first(rows, predicate):
    return next((row for row in rows if predicate(row[0])), None)


methods = ('on_or_before', 'strict_before', 'first_in_month', 'closest', 'nearest_boundaries')
counts = {method: Counter() for method in methods}
unmatched = []
month_matches = {method: Counter() for method in methods}
for month in reference['months']:
    begin = date.fromisoformat(month['fromDate'])
    requested_end = date.fromisoformat(month['toDate'])
    end_boundary = min(requested_end, date(2026, 10, 14))
    for fund_key, expected_map in reference['monthlyMap'].items():
        fund_id = int(fund_key)
        rows = series[fund_id]
        end = last(rows, lambda value: value <= end_boundary)
        nearest_end = min(
            (row for row in rows if row[0] <= date(2026, 10, 14)),
            key=lambda row: (abs((row[0] - requested_end).days), row[0] > requested_end),
            default=None,
        )
        candidates = {
            'on_or_before': last(rows, lambda value: value <= begin),
            'strict_before': last(rows, lambda value: value < begin),
            'first_in_month': first(rows, lambda value: begin <= value <= end_boundary),
        }
        before = candidates['on_or_before']
        after = candidates['first_in_month']
        candidates['closest'] = min(
            (candidate for candidate in (before, after) if candidate is not None),
            key=lambda row: (abs((row[0] - begin).days), row[0] > begin), default=None,
        )
        candidate_ends = {method: end for method in candidates}
        candidates['nearest_boundaries'] = candidates['closest']
        candidate_ends['nearest_boundaries'] = nearest_end
        expected_value = expected_map[month['label']]
        expected = None if expected_value is None else Decimal(str(expected_value))
        matched = []
        for method, start in candidates.items():
            calculated = returned(start, candidate_ends[method])
            status = 'exact' if calculated == expected else (
                'expected_null' if expected is None else
                'calculated_null' if calculated is None else 'mismatch'
            )
            counts[method][status] += 1
            if status == 'exact':
                month_matches[method][month['label']] += 1
                matched.append(method)
        if expected is not None and not matched:
            unmatched.append((fund_id, month['label'], expected_value, {
                method: float(returned(start, candidate_ends[method]))
                if returned(start, candidate_ends[method]) is not None else None
                for method, start in candidates.items()
            }))

print(json.dumps({method: dict(value) for method, value in counts.items()}, indent=2))
print('month exact matches')
for method in methods:
    print(method, dict(month_matches[method]))
print('expected numeric unmatched by all methods', len(unmatched))
for row in unmatched:
    print(row)
