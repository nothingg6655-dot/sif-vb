# Scheme returns and annualized volatility

Implemented in the existing Fastify/TypeScript API. All 139 source schemes are
included: 37 Direct Growth, 30 Direct IDCW, 37 Regular Growth and 35 Regular IDCW.
The existing overview continues to select verified Regular Growth representatives;
the scheme API exposes all plan/option variants individually.

## Method

The default calculation date is the individual scheme's latest valid NAV date.
An explicit historical `asOfDate` uses the latest valid NAV on or before that date,
and subtracts the lookback from the requested historical date. Calendar subtraction
clamps month ends: 31 March minus one month is 28 February (29 in a leap year).

For 1M, 3M, 6M and 1Y, subtract 1, 3, 6 or 12 calendar months and select the first
valid NAV **strictly after** the target, through the calculation date. Return is
`(ending NAV / starting NAV - 1) * 100`; it is not annualized. Since Inception
uses the scheme's first valid stored NAV. A young scheme uses its available NAVs
after the target, with actual selected dates disclosed. No elapsed return interval
or no NAV within the window produces null, rather than an invented zero.

Daily returns are decimal ratios between consecutive valid observed NAVs. Their
sample standard deviation divides by `n - 1`; annualized volatility is sample SD
times `sqrt(252)` times 100. The window filters the return's date, not the base NAV
date, so the first return can use a preceding NAV outside the window. At least
two actual daily returns are required. A constant observed series produces zero.
Weekends, holidays and gaps add no observations. Zero, negative, null, malformed
and nonfinite NAVs are excluded. Conflicting duplicate dates fail that scheme;
the batch continues with other schemes and reports the failure.

Financial arithmetic uses an isolated decimal.js constructor with 50 significant
digits. Display values round once, half up, to two decimal places. NUMERIC columns
store the final display values; exact calculated decimal strings are retained in
versioned metadata and the API's `precision` fields. Starting/current NAV storage
retains the source's NUMERIC(24,10) precision. This avoids double rounding during
cache retrieval. The frontend can format numeric display values with two places.

## Database integration

Migration `0007_scheme_performance_engine.sql` extends the existing
`sif_scheme_return_cache` and `sif_scheme_risk_cache`; no duplicate metrics table
was created. Both are keyed by source, source scheme code, calculation date,
calendar horizon and calculation version. Returns additionally retain their
existing method key. The source foreign key points to the authoritative
`tigzig_schemes`, so recently ingested schemes do not depend on optional strategy
or factsheet mappings being complete.

Each calculation stores the selected start/end NAVs and dates, target date,
observation count, exact values and a source fingerprint. Historical dates are
retained. Both caches are bulk-upserted in one transaction; `IS DISTINCT FROM`
prevents unchanged values from rewriting rows or timestamps. Fetching all NAV
data uses one query, rather than a query per scheme or horizon.

RLS remains enabled. The backend role may insert/update only these two derived
caches in addition to its existing read permissions; this change grants no NAV
source writes. Calculation endpoints require the configured admin key. Anonymous
database access is not enabled.

## API examples

Calculate all schemes at each scheme's latest NAV date:

```http
POST /api/performance/calculate-all
Authorization: Bearer <ADMIN_API_KEY>
Content-Type: application/json

{}
```

Calculate one reproducible historical date:

```http
POST /api/performance/SIF:13/calculate
Authorization: Bearer <ADMIN_API_KEY>
Content-Type: application/json

{"asOfDate":"2026-11-06"}
```

Read the stored result:

```http
GET /api/performance/SIF:13?asOfDate=2026-11-06&period=1M
GET /api/performance?asOfDate=2026-11-06
```

Excerpt from the actual Magnum Regular Growth result (additional provenance and
precision fields are included in the full API response):

```json
{
  "success": true,
  "data": {
    "schemeId": "SIF:13",
    "asOfDate": "2026-11-06",
    "currentNav": 10.4411,
    "periods": {
      "1M": {
        "absoluteReturn": 1.27,
        "annualizedVolatility": 5.50,
        "startingNav": 10.3104,
        "startingNavDate": "2026-10-07",
        "targetDate": "2026-10-06",
        "observationCount": 23,
        "firstReturnDate": "2026-10-07",
        "previousNavDate": "2026-10-06"
      }
    }
  }
}
```

Invalid date/body/period returns 400; unknown calculated metrics or scheme returns
404; an ambiguous identifier returns 409; a conflicting individual NAV series
returns 422. Batch calculation errors identify affected schemes. Use `SIF:13`
instead of a display fund ID to avoid mixing scheme variants.

## Run, maintain and validate

From `backend/`:

```powershell
npm run build
npm run performance:calculate -- --as-of=2026-11-06 --output=../docs/reports/scheme-performance-2026-11-06.json
npm run performance:calculate -- --output=../docs/reports/scheme-performance-latest.json
npm run test:metrics
$env:RUN_LIVE_PERFORMANCE_TESTS='true'
npm run test:metrics
```

The existing two-hour Windows NAV task runs the compiled calculation CLI after
its source check. Changed source data updates metrics; unchanged metric rows
remain untouched. It also retries derived calculations after a prior calculation
failure even when the source itself has not changed. Rebuild the backend after
changing calculation code. The task's existing daily two-hour triggers remain.

The independent validation script uses Python Decimal at precision 60 and reads
either the actual provider CSV or the complete stored NAV history:

```powershell
# From db-test/
.\.venv\Scripts\python.exe scripts/verify_scheme_performance.py docs/reports/scheme-performance-2026-11-06.json --database
.\.venv\Scripts\python.exe scripts/verify_scheme_performance.py docs/reports/scheme-performance-2026-11-06.json
```

The second command intentionally reports source-snapshot differences when the
current CSV omits valid history retained in the database. It never truncates NAV
history to make a comparison match. `--fixtures` regenerates the seven real CSV
regression fixtures; production calculations contain no hardcoded fund results.

## Validation results and source discrepancies

The final clean backend test run passed **72 existing regression tests and all
57 Vitest tests**, including live API/database integration, historical retention,
idempotent writes, invalid NAVs, plan/source separation and a double-rounding
regression. TypeScript compilation and all six existing Flutter comparison widget
tests (including narrow/wide layouts and dark/light themes) passed. A real Windows NAV task verification
completed with exit code 0 and reported zero source and zero metric writes for
an unchanged snapshot. Browser verification confirmed that the local overview
retrieves the new calculations from `http://127.0.0.1:3001`.

The real database calculation covers **139 schemes and 695 scheme-period pairs**.
The independent database-history oracle reports **zero discrepancies** for both
the 6 November reference date and each scheme's latest NAV date.

All seven supplied 1M Regular Growth references match at 6 November 2026:
qSIF -1.03%, Altiva 0.30%, Magnum 1.27%, Titanium 4.73%, Arudha 0.41%, iSIF 3.06%
and Apex 1.28%. Each selected starting NAV is dated 7 October 2026.

The current CSV begins the four Summit Active Asset Allocator variants
(`SIF:175`, `SIF:176`, `SIF:177`, `SIF:178`) on 4 November, while the database retains
earlier observations back to 1 September. Consequently 135 schemes agree with the
current CSV across all five periods; the four Summit variants differ because
their input histories differ. Their results agree with the independent oracle
over the full database history. These are retained-source-history discrepancies,
not a reason to replace inception dates or delete original NAVs.

Machine-readable results:

- [Historical metrics](reports/scheme-performance-2026-11-06.json)
- [Latest metrics](reports/scheme-performance-latest.json)
- [Independent database validation](reports/scheme-performance-2026-11-06-database-validation.json)
- [CSV comparison, including all discrepancies](reports/scheme-performance-2026-11-06-csv-validation.json)

## Implementation files

New calculation, retrieval and API files under `backend/src/`:

- `services/scheme-performance-model.ts`
- `services/absoluteReturns.service.ts`
- `services/volatility.service.ts`
- `services/scheme-performance-calculator.ts`
- `services/performance.service.ts`
- `services/performance-adapter.ts`
- `repositories/scheme-performance.ts`
- `routes/scheme-performance.ts`
- `utils/calculate-scheme-performance.ts`

New tests: `backend/tests/scheme-performance.spec.ts`,
`backend/tests/scheme-performance-api.spec.ts`,
`backend/tests/scheme-performance-live.spec.ts`, the real reference fixture
`backend/tests/fixtures/scheme-performance-reference-2026-11-06.json`, and
`backend/vitest.config.ts`.

Existing integration files updated: `backend/src/app.ts`,
`backend/src/services/analytics.ts`, `backend/src/services/category-comparison.ts`,
`backend/src/services/funds.ts`, `backend/src/routes/category-comparison.ts`,
`backend/src/routes/funds.ts`, the existing `analytics.test.ts`, `api.test.ts`,
`category-comparison.test.ts`, `performance.test.ts`, `reconciliation.test.ts`, `backend/package.json`,
and its lockfile. Database/scheduling/documentation changes are in
`database/migrations/0007_scheme_performance_engine.sql`,
`scripts/run_nav_sync.py`, `scripts/verify_scheme_performance.py`, `docs/api.md`,
this guide and the result/validation reports linked above.
The Flutter comparison footer in
`sif_research/lib/features/overview/presentation/category_performance_table.dart`
also describes the updated calendar and volatility conventions.

The local frontend at `http://127.0.0.1:8080/#/overview` points to the updated local
API on port 3001 for preview. The hosted Render API still requires deployment of
these backend changes; this task did not publish a deployment.
