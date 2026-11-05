"""Compare the running local heatmap with the supplied current SIF360 snapshot."""
import json
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / 'backend/tests/fixtures/sif360-heatmap-12m-2026-10-14.json'
REPORT_PATH = ROOT / 'docs/reports/current-heatmap-reconciliation.json'
URL = 'http://127.0.0.1:3000/api/heatmap-all?window=12m&to_date=2026-10-14'


def compare_map(expected_map, actual_map):
    rows = []
    for series_id, months in expected_map.items():
        for month, expected in months.items():
            actual = actual_map[series_id][month]
            if expected == actual:
                status = 'exact'
            elif expected is None and actual is not None:
                status = 'reference-null-local-value'
            elif expected is not None and actual is None:
                status = 'reference-value-local-null'
            else:
                status = 'numeric-mismatch'
            rows.append({
                'id': series_id, 'month': month, 'reference': expected,
                'local': actual, 'status': status,
            })
    counts = {}
    for row in rows:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'counts': counts, 'differences': [row for row in rows if row['status'] != 'exact']}


def main():
    reference = json.loads(REFERENCE_PATH.read_text(encoding='utf-8'))
    with urlopen(URL, timeout=15) as response:
        actual = json.load(response)
    report = {
        'reference': str(REFERENCE_PATH.relative_to(ROOT)),
        'localUrl': URL,
        'funds': compare_map(reference['monthlyMap'], actual['monthlyMap']),
        'benchmarks': compare_map(reference['benchmarkMap'], actual['benchmarkMap']),
        'methodology': {
            'funds': 'closest observation to each calendar boundary; earlier on ties',
            'benchmarks': 'official PRICE index; previous month-end close to month-end close',
        },
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'funds': report['funds']['counts'],
        'benchmarks': report['benchmarks']['counts'],
        'report': str(REPORT_PATH.relative_to(ROOT)),
    }, indent=2))


if __name__ == '__main__':
    main()
