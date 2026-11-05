"""Import official NSE/BSE price-index snapshots into VB-SIF."""
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.tigzig.config import DatabaseConfig  # noqa: E402


REPORTS = ROOT / 'docs' / 'reports'


def nse_rows(*filenames: str) -> list[tuple[datetime.date, Decimal]]:
    observations = set()
    for filename in filenames:
        payload = json.loads((REPORTS / filename).read_text(encoding='utf-8-sig'))
        for row in payload:
            observations.add((
                datetime.strptime(row['HistoricalDate'], '%d %b %Y').date(),
                Decimal(row['CLOSE']),
            ))
    return sorted(observations)


def bse_rows(filename: str) -> list[tuple[datetime.date, Decimal]]:
    payload = json.loads((REPORTS / filename).read_text(encoding='utf-8-sig'))
    series = json.loads(payload.split('#@#')[1])
    return sorted({
        (datetime.strptime(row['date'], '%a %b %d %Y %H:%M:%S').date(), Decimal(row['value']))
        for row in series
    })


BENCHMARKS = {
    'bse500': {
        'source': 'BSE Indices',
        'identifier': 'BSE 500 (code 17)',
        'url': ('https://www.bseindices.com/AsiaIndexAPI/api/AsiaIndicesGraphData/w'
                '?index=17&flag=1&sector=&seriesid=DT'),
        'rows': lambda: bse_rows('bse500-official.txt'),
    },
    'nifty50': {
        'source': 'NSE Indices',
        'identifier': 'NIFTY 50',
        'url': 'https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString',
        'rows': lambda: nse_rows('nifty50-pre.json', 'nifty50-part1.json', 'nifty50-part2.json'),
    },
    'niftymidsml': {
        'source': 'NSE Indices',
        'identifier': 'NIFTY MIDSMALLCAP 400',
        'url': 'https://www.niftyindices.com/BackPage/getHistoricaldatatabletoString',
        'rows': lambda: nse_rows(
            'niftymidsml-pre.json', 'niftymidsml-part1.json', 'niftymidsml-part2.json',
        ),
    },
}


def main() -> int:
    config = DatabaseConfig.from_env()
    imported = {}
    with psycopg.connect(**config.connection_kwargs()) as connection:
        target = connection.execute('SELECT current_database(), current_schema()').fetchone()
        if target != ('VB-SIF', 'public'):
            raise RuntimeError('Refusing import outside VB-SIF/public')
        with connection.transaction():
            for benchmark_id, definition in BENCHMARKS.items():
                rows = definition['rows']()
                if not rows:
                    raise RuntimeError(f'Official snapshot is empty: {benchmark_id}')
                source_version = f"official-price-index:{rows[0][0]}:{rows[-1][0]}"
                connection.execute("""
                    UPDATE public.app_benchmarks
                    SET index_variant='PRICE', source=%s, source_identifier=%s,
                        coverage_status='available', metadata=%s::jsonb, updated_at=now()
                    WHERE benchmark_id=%s
                """, (
                    definition['source'], definition['identifier'],
                    json.dumps({
                        'officialSourceUrl': definition['url'],
                        'firstObservationDate': rows[0][0].isoformat(),
                        'latestObservationDate': rows[-1][0].isoformat(),
                    }), benchmark_id,
                ))
                with connection.cursor() as cursor:
                    cursor.executemany("""
                        INSERT INTO public.app_benchmark_history(
                            benchmark_id, observation_date, index_level, source_version
                        ) VALUES (%s,%s,%s,%s)
                        ON CONFLICT (benchmark_id, observation_date) DO UPDATE
                        SET index_level=EXCLUDED.index_level,
                            source_version=EXCLUDED.source_version, updated_at=now()
                    """, [
                        (benchmark_id, observation_date, value, source_version)
                        for observation_date, value in rows
                    ])
                imported[benchmark_id] = {
                    'rows': len(rows), 'firstDate': rows[0][0].isoformat(),
                    'latestDate': rows[-1][0].isoformat(), 'variant': 'PRICE',
                }
    print(json.dumps({'database': 'VB-SIF', 'imported': imported}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
