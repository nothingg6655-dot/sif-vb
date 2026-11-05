# Unified SIF performance analytics

## Architecture and coverage

The immutable NAV source is `tigzig_scheme_nav`, keyed by source, scheme, and date.
`app_performance_config` maps response IDs 1 through 34 to verified Regular Growth
schemes. Repositories batch-load histories; `backend/src/services/performance.ts`
normalizes observations and performs decimal return division; and
`backend/src/services/analytics.ts` applies period boundaries and aggregates monthly,
annual, fiscal, rolling, comparison, and chart results.

Available history runs from 8 October 2025 through 13 October 2026. Daily and weekly
returns exist for all 34 funds, but coverage declines for longer periods. Two-, three-,
and five-year returns are unavailable for every fund. The API returns `null` with an
`insufficient-history` status and never substitutes inception performance.

This repository has no frontend application or component library. The normalized APIs
provide ordered periods, fund metadata, tooltip inputs, partial-period flags, benchmark
coverage, and explicit nulls so a future UI does not need financial formulas.

## Arithmetic and reference selection

All returns use:

```text
((ending value / starting value) - 1) * 100
```

Decimal strings are converted to integer coefficients. Division retains precision and
the final percentage is rounded half-up to two decimals. Calculated zero remains `0`;
unavailable performance remains `null`.

- 1D uses the immediately preceding available observation.
- 1W uses a seven-calendar-day target and the first observation on or after it.
- 1M and 3M use the verified SIF360 30- and 90-day targets and first observation on or
  after the target.
- 6M, 1Y, 2Y, 3Y, and 5Y subtract calendar months and select the first observation on or
  after the target.
- A trailing return requires history on or before its target.
- Since inception uses the first valid observation. The source's declared first-NAV date
  matches the first imported observation for all 34 mappings.
- YTD uses the last observation on or before 1 January.

Every metric retains requested start, selected start, end observation, elapsed days,
method, status, and warnings.

## Monthly, annual, fiscal, and rolling rules

A fund monthly return uses the NAV closest to the first calendar day and the NAV closest
to month end, preferring the earlier observation on an equal-distance tie. The current
month boundary is capped at `to_date` and marked partial. This reproduces the observed
SIF360 weekend and holiday behavior without discarding valid database observations.

A completed calendar year uses the last NAV on or before 1 January and the last NAV on
or before 31 December. The current year is excluded from the completed map and returned
as YTD. Indian financial years use 1 April through 31 March; the active year is returned
separately as financial YTD.

Rolling 1M/3M windows use fixed 30/90-day targets. Rolling 6M/1Y windows use calendar
subtraction. Each ending observation is paired with the first observation on or after
its historical target, and never with an observation after the ending date. Summary
statistics include only valid points.

## Benchmarks

The schema stores official BSE 500, NIFTY 50, and NIFTY MidSmallcap 400 levels. All
three are declared PRICE indices. BSE 500 comes from BSE Indices code 17; both NIFTY
series come from the NSE Indices historical endpoint. Monthly benchmark returns use
the last close before the first day of the month through the last close on or before
month end. Source URLs, identifiers, coverage dates, and versions are recorded in the
database. Run `scripts/import_official_benchmarks.py` to load the verified snapshots.

Excess return is fund return minus benchmark return in percentage points. Relative
compounded return is:

```text
(((1 + fund return / 100) / (1 + benchmark return / 100)) - 1) * 100
```

Invalid denominators produce `null`.

## Reconciliation

Run:

```powershell
npm.cmd --prefix backend run analytics:reconcile
```

It generates:

- `docs/reports/sif-analytics-period-reconciliation.csv`
- `docs/reports/sif360-monthly-reconciliation.csv`
- `docs/reports/sif360-benchmark-reconciliation.csv`
- `docs/reports/sif-analytics-reconciliation.json`

Reports distinguish exact matches, mismatches, missing references, insufficient
history, source-snapshot differences, and unavailable benchmark sources. The supplied
October heatmap was captured before some later NAV rows and historical corrections, so
parity is measured rather than assumed.
