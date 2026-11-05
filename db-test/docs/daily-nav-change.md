# Daily NAV percentage change

`GET /api/liveTicker` calculates daily change from TIGZIG data stored in
`public.tigzig_scheme_nav`. A NAV series is uniquely identified by `(source,
scheme_code)`, and the table primary key `(source, scheme_code, nav_date)` prevents
duplicate dates in production. `public.app_performance_config` selects one verified
Regular Growth series for each of the 34 response positions. The composite foreign key
to `app_tigzig_fund_mapping` prevents a configured scheme from belonging to another
fund.

The repository uses one windowed batch query to retrieve the two most recent dates for
all configured schemes. It never derives the previous date by subtracting a calendar
day. The service calculates:

```text
change = ((latest NAV / previous NAV) - 1) * 100
```

The NAV strings are parsed as decimal coefficients, the ratio is evaluated with integer
arithmetic, and the final percentage is rounded half-up to two decimal places. Zero is
a valid daily return and remains numeric `0`. A missing or invalid previous observation
returns `null` and generates a structured warning. Identical duplicate observations are
deduplicated deterministically in the service boundary; conflicting duplicates are
rejected. These cases are defensive because the production primary key already forbids
duplicate scheme/date rows.

The reference fixture was captured from `https://www.sif360.com/api/liveTicker` on
14 October 2026 and contains 34 funds. The database reproduces both latest NAV and daily
change for 32 funds. Two mismatches are upstream source gaps:

| Reference ID | Fund | Local latest | Reference latest | Local change | Reference change |
|---:|---|---|---|---:|---:|
| 12 | DynaSIF Active Asset Allocator Long-Short Fund | 12 Oct 2026, 10.3728 | 13 Oct 2026, 10.3892 | -0.10 | 0.16 |
| 25 | iSIF Active Asset Allocator Long-Short Fund | 12 Oct 2026, 10.1764 | 13 Oct 2026, 10.2155 | -0.29 | 0.38 |

No October 13 row exists locally for either selected scheme, so the API correctly uses
their immediately preceding available local observations. It does not copy the external
values. Four display names differ only by provider styling (`DynaSIF`/`Dyna` and
`SUMMIT`/`Summit`); their scheme mapping and financial values remain verified.

Regenerate the full CSV and JSON evidence with:

```powershell
npm.cmd --prefix backend run daily-change:reconcile
```

The generated artifacts are `docs/reports/sif360-daily-change-reconciliation.csv` and
`docs/reports/sif360-daily-change-reconciliation.json`.
