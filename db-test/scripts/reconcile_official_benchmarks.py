import json
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


REPORTS = Path('docs/reports')
REFERENCE = json.loads(Path(
    'backend/tests/fixtures/sif360-heatmap-12m-2026-10-14.json'
).read_text(encoding='utf-8'))


def observations(*names: str):
    values = []
    for name in names:
        for row in json.loads((REPORTS / name).read_text(encoding='utf-8-sig')):
            values.append((
                datetime.strptime(row['HistoricalDate'], '%d %b %Y').date(),
                Decimal(row['CLOSE']),
            ))
    return sorted(set(values))


def bse_observations(name: str):
    payload = json.loads((REPORTS / name).read_text(encoding='utf-8-sig'))
    series = json.loads(payload.split('#@#')[1])
    values = {
        (datetime.strptime(row['date'], '%a %b %d %Y %H:%M:%S').date(), Decimal(row['value']))
        for row in series
    }
    return sorted(values)


def closest(rows, target: date):
    return min(rows, key=lambda row: (abs((row[0] - target).days), row[0] > target))


def last_before(rows, target: date):
    return [row for row in rows if row[0] < target][-1]


def last_on_or_before(rows, target: date):
    return [row for row in rows if row[0] <= target][-1]


def percentage(start: Decimal, end: Decimal):
    return (((end / start) - 1) * 100).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


sources = {
    'bse500': bse_observations('bse500-official.txt'),
    'nifty50': observations('nifty50-pre.json', 'nifty50-part1.json', 'nifty50-part2.json'),
    'niftymidsml': observations(
        'niftymidsml-pre.json', 'niftymidsml-part1.json', 'niftymidsml-part2.json',
    ),
}
for benchmark_id, rows in sources.items():
    print(benchmark_id, len(rows), rows[0][0], rows[-1][0])
    for month in REFERENCE['months']:
        begin = date.fromisoformat(month['fromDate'])
        end = date.fromisoformat(month['toDate'])
        start_observation = last_before(rows, begin)
        end_observation = last_on_or_before(rows, end)
        calculated = percentage(start_observation[1], end_observation[1])
        expected = Decimal(str(REFERENCE['benchmarkMap'][benchmark_id][month['label']]))
        print(
            month['label'], expected, calculated, expected == calculated,
            start_observation, end_observation,
        )
