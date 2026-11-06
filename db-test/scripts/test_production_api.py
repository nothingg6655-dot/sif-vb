"""Bounded, read-only production API acceptance and financial-calculation checks."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import calendar
import json
import math
import statistics
import time

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://sif-vb.onrender.com'
TODAY = datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()
OUT = ROOT / 'data' / 'production-tests'
OUT.mkdir(parents=True, exist_ok=True)
checks, requests_log, warnings = [], [], []
lock = Lock()
started = datetime.now(ZoneInfo('Asia/Kolkata')).isoformat()


def check(name, ok, category, detail='', status=None):
    row = {'name': name, 'category': category, 'status': status or ('PASS' if ok else 'FAIL'), 'detail': str(detail)}
    with lock:
        checks.append(row)
    return ok


def request(path, category, expected=(200,), method='GET', headers=None):
    begin = time.perf_counter()
    status, error, payload = None, '', None
    try:
        response = requests.request(method, BASE + path, headers=headers, timeout=(10, 50))
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error = payload.get('error', {}) if isinstance(payload, dict) else ''
        ok = status in expected
        response_headers = dict(response.headers)
    except requests.RequestException as exc:
        ok, error, response_headers = False, type(exc).__name__, {}
    elapsed = round((time.perf_counter() - begin) * 1000, 1)
    with lock:
        requests_log.append({'method': method, 'path': path, 'category': category,
            'httpStatus': status, 'elapsedMs': elapsed, 'expected': list(expected),
            'ok': ok, 'error': error if not ok else None})
    check(f'{method} {path}', ok, category,
          f'HTTP {status}; {elapsed} ms' + (f'; {error}' if not ok else ''))
    return payload, status, response_headers


def endpoint(path, **parameters):
    return path + ('?' + urlencode(parameters) if parameters else '')


def rounded(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def subtract_months(day, count):
    year, month = divmod(day.year * 12 + day.month - 1 - count, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def calculate_fund(fund, nav):
    obs = [(date.fromisoformat(row['date']), Decimal(str(row['nav']))) for row in nav]
    if not obs:
        return
    asof = date.fromisoformat(TODAY)
    targets = {'r1m': asof - timedelta(days=30), 'r3m': asof - timedelta(days=90),
        'r6m': subtract_months(asof, 6), 'r1y': subtract_months(asof, 12), 'si': obs[0][0]}
    end = obs[-1]
    for period, target in targets.items():
        start = next((point for point in obs if point[0] >= target), None) if obs[0][0] <= target else None
        expected = rounded((end[1] / start[1] - 1) * 100) if start and start[0] < end[0] else None
        actual = (fund.get('returns', {}).get(period) or {}).get('value')
        check(f"{fund['slug']} absolute return {period}", actual == expected,
            'Calculation audit', f'API={actual}; independent NAV calculation={expected}')
        window = [point for point in obs if start and start[0] <= point[0] <= end[0]]
        pairs = list(zip(window, window[1:]))
        daily = [float((now[1] / before[1] - 1) * 100) for before, now in pairs]
        gaps = any((now[0] - before[0]).days > 7 for before, now in pairs)
        volatility = rounded(statistics.stdev(daily) * math.sqrt(252)) if expected is not None and len(daily) >= 2 and not gaps else None
        actual_vol = (fund.get('volatility', {}).get(period) or {}).get('value')
        check(f"{fund['slug']} annualized volatility {period}", actual_vol == volatility,
            'Calculation audit', f'API={actual_vol}; independent sample SD x sqrt(252)={volatility}')


def main():
    health, status, _ = request('/health', 'Availability')
    if status != 200:
        raise RuntimeError('Production health check failed; no bulk tests started')
    check('Health confirms database connectivity', health == {'status': 'ok', 'database': 'connected'}, 'Availability')
    comparison, _, _ = request(endpoint('/api/analytics/category-comparison', to_date=TODAY), 'Overview')
    funds = comparison.get('funds', [])
    check('Catalog has unique scheme identities', len({f['slug'] for f in funds}) == len(funds) and bool(funds), 'Data integrity')
    check('Catalog contains Regular Growth schemes only', all(f.get('plan') == 'Regular' and f.get('option') == 'Growth' for f in funds), 'Regular-plan isolation')
    check('Platform categories include Equity, Debt and Hybrid', set(comparison.get('platformCategories', [])) == {'Equity', 'Debt', 'Hybrid'}, 'Overview')
    published = [f for f in funds if isinstance(f.get('netAumCr'), (int, float))]
    missing_aum = [f['fullName'] for f in funds if f.get('netAumCr') is None]
    check('Published AUM values are finite and non-negative', all(math.isfinite(f['netAumCr']) and f['netAumCr'] >= 0 for f in published), 'Data integrity')
    check('Category counts reconcile with catalog', sum(c['fundCount'] for c in comparison.get('categories', [])) == len(funds), 'Overview')
    ticker, _, _ = request('/api/liveTicker', 'Ticker')
    check('Ticker covers all eligible funds', len(ticker.get('data', [])) == len(funds), 'Ticker')
    monthly, _, _ = request(endpoint('/api/analytics/monthly-heatmap', range='all', to_date=TODAY), 'Monthly returns')
    monthly_funds = {f['slug']: f for f in monthly.get('funds', [])}
    check('Monthly heatmap excludes Direct plans', all(f.get('plan') == 'Regular' for f in monthly_funds.values()), 'Regular-plan isolation')
    benchmarks, _, _ = request(endpoint('/api/analytics/benchmarks', to_date=TODAY), 'Benchmarks')
    benchmark_rows = benchmarks.get('benchmarks', [])
    check('All benchmark series declare PRICE or TRI', bool(benchmark_rows) and all(b.get('indexVariant') in ('PRICE', 'TRI') for b in benchmark_rows), 'Benchmarks')
    month = next((m for m in monthly.get('months', []) if m.get('key') == TODAY[:7]), None)
    benchmark_summary = []
    if month:
        for benchmark in benchmark_rows:
            key = benchmark['id']
            meta = monthly.get('benchmarkCellMetadata', {}).get(key, {}).get(month['label'], {})
            value = monthly.get('benchmarkMap', {}).get(key, {}).get(month['label'])
            expected = rounded((Decimal(str(meta['endValue'])) / Decimal(str(meta['startValue'])) - 1) * 100) if meta.get('startValue') and meta.get('endValue') else None
            check(f'{key} current-month return', value == expected and value is not None,
                'Benchmarks', f'API={value}; independently recalculated={expected}; through {meta.get("endDate")}')
            check(f'{key} current month labelled partial', meta.get('status') == 'partial', 'Benchmarks')
            benchmark_summary.append({'name': benchmark['label'], 'return': value, 'endDate': meta.get('endDate')})

    families = ['allocation', 'market-cap', 'sector-allocation', 'rating-allocation']
    names = {'allocation': 'asset-allocation', 'market-cap': 'market-cap-allocation',
        'sector-allocation': 'sector-allocation', 'rating-allocation': 'rating-wise-allocation'}
    bulk = {}
    registry = {}
    for family in families:
        body, _, _ = request('/api/funds/allocations/' + family, 'Allocations')
        bulk[family] = {item['slug']: item for item in body.get('data', [])}
        registry.update({item['slug']: item['fullName'] for item in body.get('data', [])})
        check(f'{family} registry count', body.get('count') == len(bulk[family]), 'Allocations')
    catalog = {fund['slug']: fund for fund in funds}

    def per_fund(item):
        slug, name = item
        detail, status, _ = request('/api/funds/' + slug, 'Fund details')
        if status == 200:
            data = detail.get('data', {})
            schemes = data.get('schemes', [])
            check(slug + ' details exclude Direct plans', all(s.get('plan') == 'Regular' for s in schemes), 'Regular-plan isolation')
            check(slug + ' AAUM excludes Direct plans', all('direct' not in a.get('schemeName', '').lower() for a in data.get('quarterlyAAUM', [])), 'Regular-plan isolation')
            if slug in catalog:
                code = monthly_funds.get(slug, {}).get('schemeCode')
                selected = next((s for s in schemes if s.get('schemeCode') == code and s.get('option') == 'Growth'), None)
                if not selected:
                    check(slug + ' preferred Regular NAV is resolvable', False, 'NAV history', f'preferred schemeCode={code}')
                else:
                    sd_id = selected.get('tempSifCode')
                    nav, nav_status, _ = request(endpoint('/api/nav-history', sd_id=sd_id,
                        from_date='2020-01-01', to_date=TODAY, limit=1000, page=1), 'NAV history')
                    if nav_status == 200:
                        points = nav.get('data', [])
                        dates = [p['date'] for p in points]
                        check(slug + ' NAV dates are ordered and unique', bool(points) and dates == sorted(set(dates)) and dates[-1] <= TODAY, 'NAV history')
                        check(slug + ' NAV values are valid', all(isinstance(p.get('nav'), (int,float)) and math.isfinite(p['nav']) and p['nav'] > 0 for p in points), 'NAV history')
                        check(slug + ' full NAV history fits page', nav.get('pagination', {}).get('total', len(points)) == len(points), 'NAV history')
                        if points:
                            check(slug + ' latest NAV reconciles with catalog', points[-1]['nav'] == catalog[slug].get('latestNav') and points[-1]['date'] == catalog[slug].get('latestDate'), 'Data integrity')
                            calculate_fund(catalog[slug], points)
        for family in families:
            body, status, _ = request('/api/funds/' + slug + '/' + family, 'Allocations', expected=(200,404))
            named, named_status, _ = request(endpoint('/api/' + names[family], scheme_name=name), 'Scheme-name APIs', expected=(200,404))
            check(slug + ' ' + family + ' name/slug agreement', status == named_status and body == named, 'Scheme-name APIs')
            expected = bulk[family].get(slug)
            if status == 200:
                check(slug + ' ' + family + ' bulk/individual agreement', body.get('data') == expected, 'Allocations')
                items = body['data'].get('items', [])
                total = body['data'].get('totalPercent')
                single = bool(items) and all(i.get('percent') is not None for i in items) and len({i.get('basis') for i in items}) == 1
                calculated = round(sum(i['percent'] for i in items), 8) if single else None
                check(slug + ' ' + family + ' denominator-aware total', total == calculated, 'Allocations')
            elif status == 404:
                code = body.get('error', {}).get('code')
                check(slug + ' ' + family + ' unavailable disclosure is explicit', code == 'FACTSHEET_NOT_FOUND' and expected is not None and expected.get('reportId') is None, 'Data availability', code)
                with lock:
                    warnings.append({'scheme': name, 'metric': family, 'reason': code})
        band, bs, _ = request(endpoint('/api/band', scheme_name=name), 'Riskometers', expected=(200,404))
        alias, als, _ = request(endpoint('/api/risk-band', scheme_name=name), 'Riskometers', expected=(200,404))
        check(slug + ' risk-band alias agreement', bs == als and band == alias, 'Riskometers')
        if bs == 200:
            for field in ['investmentStrategyRiskLevel', 'benchmarkRiskLevel']:
                level = band.get('data', {}).get(field)
                check(slug + ' ' + field + ' valid explicit level', level is None or type(level) is int and 1 <= level <= 5, 'Riskometers')
        elif bs == 404:
            check(slug + ' risk absence is explicit', band.get('error', {}).get('code') == 'FACTSHEET_NOT_FOUND', 'Data availability')

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(per_fund, item) for item in registry.items()]
        for i, future in enumerate(as_completed(jobs), 1):
            try:
                future.result()
            except Exception as exc:
                check('Per-fund test execution', False, 'Harness', type(exc).__name__ + ': ' + str(exc)[:250])
            if i % 5 == 0 or i == len(jobs):
                print(json.dumps({'phase': 'fund checks', 'completed': i, 'total': len(jobs), 'checks': len(checks)}), flush=True)

    extra = [
        endpoint('/api/analytics/performance', to_date=TODAY),
        endpoint('/api/performance-all', to_date=TODAY),
        endpoint('/api/analytics/annual', to_date=TODAY), '/api/benchmark-performance', '/api/sif-ter',
        *[endpoint('/api/heatmap-all', window=w, to_date=TODAY) for w in ['3m','6m','12m','all']],
        *[endpoint('/api/analytics/monthly-heatmap', range=r, to_date=TODAY) for r in ['12m','24m','36m','60m']],
        *[endpoint('/api/analytics/rolling', fund_id=funds[0]['id'], window=w, to_date=TODAY) for w in ['1m','3m','6m','1y']],
        endpoint('/api/analytics/trend', fund_id=funds[0]['id'], from_date='2025-01-01', to_date=TODAY),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda path: request(path, 'Analytics variants'), extra))

    def compare_benchmark(pair):
        fund, benchmark = pair
        body, status, _ = request(endpoint('/api/analytics/benchmark-comparison', fund_id=fund['id'], benchmark_id=benchmark['id'], to_date=TODAY), 'Benchmark comparison')
        if status == 200:
            for period, values in body.get('periods', {}).items():
                a, b, excess = values.get('fundReturn'), values.get('benchmarkReturn'), values.get('excessReturnPctPoints')
                expected = rounded(Decimal(str(a)) - Decimal(str(b))) if a is not None and b is not None else None
                check(f"{fund['slug']} / {benchmark['id']} / {period} excess return", excess == expected, 'Benchmark arithmetic')
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(compare_benchmark, [(f,b) for f in funds for b in benchmark_rows]))

    invalid = [
        ('/api/analytics/category-comparison?to_date=2026-02-30',400),
        ('/api/analytics/performance?to_date=bad',400),
        ('/api/analytics/monthly-heatmap?range=invalid',400),
        ('/api/analytics/monthly-heatmap?range=all&to_date=2026-02-30',400),
        ('/api/analytics/rolling?fund_id=0&window=1m',400),
        ('/api/analytics/rolling?fund_id=1&window=bad',400),
        ('/api/analytics/trend?fund_id=1&from_date=2026-11-06&to_date=2026-01-01',400),
        ('/api/analytics/benchmark-comparison?fund_id=1&benchmark_id=notreal',404),
        ('/api/analytics/benchmark-comparison?fund_id=999&benchmark_id=nifty50',404),
        ('/api/funds/not-a-real-fund',404),('/api/funds/Bad_Slug',400),
        ('/api/nav-history?sd_id=bad&from_date=2026-01-01&to_date=2026-11-06',400),
        ('/api/nav-history?sd_id=SIF-13&from_date=2026-01-01&to_date=2026-11-06&limit=1001',400),
        ('/api/nav-history?sd_id=SIF-14&from_date=2026-01-01&to_date=2026-11-06',404),
        ('/api/asset-allocation',400),('/api/risk-band?scheme_name=',400),
        ('/api/market-cap-allocation?scheme_name=not-a-real-fund',404),
        ('/api/not-a-real-route',404),
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda item: request(item[0], 'Input validation', expected=(item[1],)), invalid))
    request('/api/admin/tigzig/sync/status', 'Admin authentication', expected=(401,))
    request('/api/admin/tigzig/sync/status', 'Admin authentication', expected=(401,), headers={'Authorization':'Bearer invalid-test-key'})
    admin = dotenv_values(ROOT / '.env.render').get('ADMIN_API_KEY')
    if admin:
        _, code, _ = request('/api/admin/tigzig/sync/status', 'Admin authentication', expected=(200,401), headers={'Authorization': 'Bearer ' + admin})
        check('Known local admin key accepted by deployed service', code == 200, 'Admin authentication',
              'A 401 means Render uses a different admin key; valid-key coverage unavailable.', status='PASS' if code == 200 else 'SKIP')
    _, _, headers = request('/health', 'Browser CORS', method='OPTIONS', expected=(204,), headers={
        'Origin':'http://127.0.0.1:8080','Access-Control-Request-Method':'GET'})
    check('Browser CORS permits app requests', headers.get('Access-Control-Allow-Origin', headers.get('access-control-allow-origin')) == '*', 'Browser CORS')
    report = {'baseUrl': BASE, 'startedAt': started, 'finishedAt': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
        'asOfDate': TODAY, 'maxConcurrency': 2, 'readOnlyProductionRequests': True,
        'checks': checks, 'requests': requests_log, 'warnings': warnings,
        'summary': {'checks': dict(Counter(c['status'] for c in checks)), 'requests': len(requests_log),
            'funds': len(funds), 'registeredStrategies': len(registry), 'publishedAumSchemes': len(published),
            'totalAumCr': round(sum(f['netAumCr'] for f in published),2), 'missingAum': missing_aum,
            'benchmarkMonthToDate': benchmark_summary, 'navDates': sorted({f.get('latestDate') for f in funds if f.get('latestDate')})}}
    (OUT / 'live-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
