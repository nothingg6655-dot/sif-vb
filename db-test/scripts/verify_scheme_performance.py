"""Independent Python Decimal oracle using the actual provider NAV CSV."""
import calendar
from collections import defaultdict
import csv
from datetime import date
from decimal import Decimal, getcontext, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sys
import pyarrow.parquet as pq

getcontext().prec = 60
ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / 'data/tigzig/sif_nav.csv'
META = pq.read_table(ROOT / 'data/tigzig/sif_meta.parquet').to_pylist()
PERIODS = {'1M': 1, '3M': 3, '6M': 6, '1Y': 12, 'SI': None}
REFERENCE = {7: ('qSIF', -1.03), 11: ('Altiva', .30), 13: ('Magnum', 1.27),
             29: ('Titanium', 4.73), 40: ('Arudha', .41), 35: ('iSIF', 3.06), 80: ('Apex', 1.28)}
NAV = defaultdict(dict)
SOURCES = {int(row['scheme_code']): row['source'] for row in META}
with CSV.open(encoding='utf-8-sig', newline='') as source:
    for row in csv.DictReader(source):
        value = Decimal(row['nav']) if row.get('nav') else Decimal(0)
        if value.is_finite() and value > 0:
            code = int(row['scheme_code'])
            NAV[(SOURCES[code], code)][row['date']] = value

if '--database' in sys.argv:
    sys.path.insert(0, str(ROOT))
    import psycopg
    from database.migrate import verify_target
    from ingestion.tigzig.config import DatabaseConfig
    NAV.clear()
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        connection.execute('SET TRANSACTION READ ONLY')
        verify_target(connection)
        for source, code, day, value in connection.execute(
            'SELECT source,scheme_code,nav_date::text,nav FROM public.tigzig_scheme_nav ORDER BY source,scheme_code,nav_date'
        ):
            if value.is_finite() and value > 0:
                NAV[(source, code)][day] = value


def display(value):
    return None if value is None else float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def subtract_months(iso, count):
    current = date.fromisoformat(iso)
    year, month = divmod(current.year * 12 + current.month - 1 - count, 12)
    month += 1
    return date(year, month, min(current.day, calendar.monthrange(year, month)[1])).isoformat()


def oracle(source, code, as_of):
    points = sorted((day, nav) for day, nav in NAV[(source, code)].items() if day <= as_of)
    daily = [(points[i][0], points[i][1] / points[i-1][1] - 1) for i in range(1, len(points))]
    results = {}
    for period, months in PERIODS.items():
        target = subtract_months(as_of, months) if months else None
        start = next((point for point in points if target is None or point[0] > target), None)
        absolute = (points[-1][1] / start[1] - 1) * 100 if start and start[0] < points[-1][0] else None
        samples = [value for day, value in daily if target is None or day > target]
        volatility = None
        if len(samples) >= 2:
            mean = sum(samples) / len(samples)
            volatility = (sum((value-mean)**2 for value in samples)/(len(samples)-1)).sqrt() * Decimal(252).sqrt() * 100
        results[period] = {'absoluteReturn': display(absolute), 'annualizedVolatility': display(volatility),
                           'startingNavDate': start[0] if start else None, 'observationCount': len(samples)}
    return results


if '--fixtures' in sys.argv:
    schemes = []
    for meta in META:
        code = int(meta['scheme_code'])
        if code not in REFERENCE:
            continue
        expected = oracle(meta['source'], code, '2026-11-06')
        assert expected['1M']['absoluteReturn'] == REFERENCE[code][1]
        schemes.append({'scheme': {'schemeId': f'{meta["source"]}:{code}', 'source': meta['source'],
                         'schemeCode': str(code), 'externalSchemeId': meta.get('temp_sif_code'),
                         'schemeName': meta['scheme_name'], 'plan': meta.get('scheme_plan'),
                         'option': meta.get('scheme_option'), 'amc': meta.get('amc')},
                        'observations': [{'date': day, 'nav': str(nav)} for day, nav in sorted(NAV[(meta['source'], code)].items()) if day <= '2026-11-06'],
                        'expected': expected})
    destination = ROOT / 'backend/tests/fixtures/scheme-performance-reference-2026-11-06.json'
    destination.write_text(json.dumps({'asOfDate': '2026-11-06', 'source': 'Provider sif_nav.csv',
        'sourceSha256': hashlib.sha256(CSV.read_bytes()).hexdigest(), 'oracle': 'Python Decimal, precision 60',
        'schemes': schemes}, indent=2), encoding='utf-8')
    print(json.dumps({'fixtureSchemes': len(schemes), 'destination': str(destination)}))
else:
    report_path = Path(sys.argv[1])
    report = json.loads(report_path.read_text(encoding='utf-8'))
    discrepancies = []
    checked = 0
    references = []
    for scheme in report['data']:
        code = int(scheme['schemeCode'])
        if scheme['asOfDate'] is None:
            continue
        expected = oracle(scheme['source'], code, scheme['asOfDate'])
        for period, metrics in expected.items():
            checked += 1
            for field, value in metrics.items():
                if scheme['periods'][period][field] != value:
                    discrepancies.append({'schemeId': scheme['schemeId'], 'period': period, 'field': field,
                                          'engine': scheme['periods'][period][field], 'csvOracle': value})
        if code in REFERENCE and scheme['asOfDate'] == '2026-11-06':
            label, validated = REFERENCE[code]
            actual = scheme['periods']['1M']['absoluteReturn']
            references.append({'fund': label, 'schemeId': scheme['schemeId'], 'expected1m': validated,
                               'actual1m': actual, 'matches': actual == validated})
    data_source = 'database' if '--database' in sys.argv else 'csv'
    discrepant_schemes = sorted(set(item['schemeId'] for item in discrepancies))
    result = {'dataSource': data_source, 'sourceCsv': str(CSV), 'sourceSha256': hashlib.sha256(CSV.read_bytes()).hexdigest(),
              'engineReport': str(report_path), 'schemesChecked': len(report['data']),
              'periodsChecked': checked, 'discrepantSchemes': discrepant_schemes,
              'discrepancies': discrepancies, 'referenceFunds': references}
    output = ROOT / 'docs/reports' / (report_path.stem + f'-{data_source}-validation.json')
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'dataSource': data_source, 'schemesChecked': len(report['data']),
        'periodsChecked': checked, 'discrepantSchemes': discrepant_schemes,
        'discrepancyCount': len(discrepancies), 'referenceMatches': sum(item['matches'] for item in references),
        'output': str(output)}))
    if discrepancies or any(not item['matches'] for item in references):
        raise SystemExit(1)
