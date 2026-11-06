"""Append verified PRICE index closes from official NSE daily reports and BSE history.

Usage: python scripts/refresh_official_benchmarks.py --from-date 2026-10-13 --to-date 2026-11-05
Raw responses and the import summary are retained under data/benchmark-refresh.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import sys

import psycopg
from psycopg.types.json import Jsonb
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.tigzig.config import DatabaseConfig  # noqa: E402
from database.migrate import verify_target  # noqa: E402

CACHE = ROOT / 'data' / 'benchmark-refresh'
NSE_NAMES = {'NIFTY 50': 'nifty50', 'NIFTY MIDSMALLCAP 400': 'niftymidsml'}
BSE_URL = 'https://www.bseindices.com/AsiaIndexAPI/api/AsiaIndicesGraphData/w'


def positive(value):
    number = Decimal(str(value).replace(',', ''))
    if not number.is_finite() or number <= 0:
        raise ValueError('Invalid official closing level')
    return number


def parse_nse(text, expected_date):
    values = {}
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        benchmark = NSE_NAMES.get(row['Index Name'].strip().upper())
        if benchmark is None:
            continue
        observed = datetime.strptime(row['Index Date'], '%d-%m-%Y').date()
        if observed != expected_date:
            raise ValueError('NSE report date differs from requested date')
        if benchmark in values:
            raise ValueError('Duplicate index in official NSE report')
        values[benchmark] = positive(row['Closing Index Value'])
    if set(values) != set(NSE_NAMES.values()):
        raise ValueError('Official NSE report omits a required index')
    return values


def parse_bse(text, first, last):
    payload = json.loads(text)
    header = json.loads(payload.split('#@#')[0])
    if not header or header[0].get('Scrip', '').strip().upper() != 'BSE 500':
        raise ValueError('Official BSE response is not the BSE 500 price index')
    series = json.loads(payload.split('#@#')[1])
    values = {}
    for row in series:
        observed = datetime.strptime(row['date'], '%a %b %d %Y %H:%M:%S').date()
        if not first <= observed <= last:
            continue
        value = positive(row['value'])
        if observed in values and values[observed] != value:
            raise ValueError('Conflicting official BSE closes on the same date')
        values[observed] = value
    if not values:
        raise ValueError('Official BSE response contains no closes in the requested range')
    return values


def download(url, filename, allow_missing=False):
    path = CACHE / filename
    if path.exists():
        text = path.read_text(encoding='utf-8')
    else:
        response = requests.get(url, timeout=(8, 30))
        if response.status_code == 404 and allow_missing:
            return None
        response.raise_for_status()
        text = response.text
        path.write_text(text, encoding='utf-8')
    return text, hashlib.sha256(text.encode('utf-8')).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-date', required=True, type=date.fromisoformat)
    parser.add_argument('--to-date', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error('from-date must precede to-date')
    CACHE.mkdir(parents=True, exist_ok=True)
    days = [args.from_date + timedelta(days=i)
            for i in range((args.to_date - args.from_date).days + 1)]
    days = [day for day in days if day.weekday() < 5]

    def daily(day):
        url = f'https://archives.nseindia.com/content/indices/ind_close_all_{day:%d%m%Y}.csv'
        downloaded = download(url, f'nse-{day}.csv', allow_missing=True)
        if downloaded is None:
            return day, {}, None
        text, digest = downloaded
        return day, parse_nse(text, day), {'url': url, 'sha256': digest}

    observations = {benchmark: [] for benchmark in [*NSE_NAMES.values(), 'bse500']}
    missing = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        for day, values, source in executor.map(daily, days):
            if not values:
                missing.append(day.isoformat())
            for benchmark, level in values.items():
                observations[benchmark].append((day, level, source))
    bse_url = (f'{BSE_URL}?index=17&flag=1&sector=&seriesid=DT'
               f'&frd={args.from_date:%Y%m%d}&tod={args.to_date:%Y%m%d}')
    text, digest = download(bse_url, f'bse500-{args.from_date}-{args.to_date}.json')
    observations['bse500'] = [(day, level, {'url': bse_url, 'sha256': digest})
                             for day, level in sorted(parse_bse(text, args.from_date, args.to_date).items())]
    if any(not values for values in observations.values()):
        raise ValueError('Cannot import an empty official benchmark series')
    summary = {'generatedAt': datetime.now(timezone.utc).isoformat(),
               'fromDate': str(args.from_date), 'toDate': str(args.to_date),
               'missingNseReports': missing, 'benchmarks': {}}
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        for benchmark, values in observations.items():
            for day, level, source in values:
                connection.execute('''
                    INSERT INTO public.app_benchmark_history
                        (benchmark_id,observation_date,index_level,source_version)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (benchmark_id,observation_date) DO UPDATE SET
                        index_level=EXCLUDED.index_level,source_version=EXCLUDED.source_version,
                        updated_at=now()
                    WHERE app_benchmark_history.index_level IS DISTINCT FROM EXCLUDED.index_level
                ''', (benchmark, day, level, 'official-price-index:sha256:' + source['sha256']))
            first, last, count = connection.execute('''
                SELECT min(observation_date),max(observation_date),count(*)
                FROM public.app_benchmark_history WHERE benchmark_id=%s
            ''', (benchmark,)).fetchone()
            connection.execute('''
                UPDATE public.app_benchmarks SET index_variant='PRICE',coverage_status='available',
                    metadata=metadata || %s,updated_at=now() WHERE benchmark_id=%s
            ''', (Jsonb({'firstObservationDate': str(first), 'latestObservationDate': str(last),
                         'lastRefreshAt': summary['generatedAt'],
                         'officialSourceUrl': BSE_URL if benchmark == 'bse500' else
                         'https://archives.nseindia.com/content/indices/'}), benchmark))
            summary['benchmarks'][benchmark] = {'importedCloses': len(values),
                'latestDate': str(last), 'historyCount': count, 'variant': 'PRICE',
                'sources': [source for _, _, source in values]}
    (CACHE / 'latest-import.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps({**summary, 'benchmarks': {key: {k: v for k, v in value.items() if k != 'sources'}
          for key, value in summary['benchmarks'].items()}}, indent=2))


if __name__ == '__main__':
    main()
