# VB-SIF REST API

All endpoints return JSON. Financial `NUMERIC` database values are converted to JSON
numbers after finite-value checks. Errors use:

```json
{"success":false,"error":{"code":"INVALID_QUERY","message":"..."}}
```

## `GET /api/liveTicker`

Returns `{ "data": [...] }` in configured ticker order. Each item has `fullName`,
`latestNAV`, and `change`, where `change` is the percentage change from the immediately
preceding available NAV observation. The endpoint uses the same verified Regular Growth
mapping and external fund order as `/api/performance-all`. It does one batch read of the
latest two dates per mapped scheme; weekends, holidays, and missing calendar dates are
therefore handled without date subtraction. The calculation is
`((latestNAV / previousNAV) - 1) * 100`, rounded half-up to two decimal places.

A missing, zero, invalid, or conflicting previous observation produces `null` rather
than zero. When a mapped fund has a data-quality warning, the response includes the
`X-Data-Quality-Warning-Count` header and the server emits structured diagnostics while
the JSON body remains exactly `{ "data": [...] }`. Source scheme/date details and the
34-fund comparison are in `docs/reports/sif360-daily-change-reconciliation.csv`.

## `GET /api/nav-history`

Required query parameters are `sd_id`, `from_date`, and `to_date`. `sd_id` uses the
provider `SIF-<number>` form. Dates use `YYYY-MM-DD` and the start cannot follow the
end. Optional `page` defaults to 1 and `limit` defaults to 500 with a maximum of
1,000. Without pagination parameters the response contains exactly `success` and the
ascending `data` records, matching the supplied client contract. A `pagination`
object is added only when `page` or `limit` is explicitly requested.

## `GET /api/heatmap-all`

Requires `window` (`3m`, `6m`, `12m`, or `all`) and `to_date`. `monthlyMap` is keyed
by configured ticker display order to retain the supplied frontend response style.
`months` contains the displayed labels and full calendar boundaries. `benchmarkMap`
contains calculated BSE 500, NIFTY 50, and NIFTY MidSmallcap 400 PRICE-index returns
from official BSE/NSE levels. `benchmarks` contains their display metadata.

Fund cells select the observation closest to the first calendar day and closest to
month end (or `to_date` for the current month), preferring the earlier observation on
an equal-distance tie. Benchmark cells use standard close-to-close returns: the last
official close before the month starts through the last official close on or before
month end. Missing boundary history produces an explicit `null`.

## `GET /api/performance-all`

`to_date` is optional and defaults to today. Optional `from1m` and `from3m` override
the derived 30- and 90-calendar-day targets for compatibility with the supplied client.
The endpoint uses one verified Regular Growth scheme per fund and returns
`performanceMap` keyed by the verified external IDs 1 through 34. Each value contains
exactly the supplied fields: `changePct`, `return1m`, `return3m`, `latestNav`,
`latestDate`, and `inceptionDate`. `changePct` retains the sample response meaning of
since-inception absolute change. Daily change remains available from `/api/liveTicker`.

All returns are absolute:

```text
((ending NAV / starting NAV) - 1) * 100
```

The ending observation is the last NAV on or before `to_date`. The observed
compatibility method selects the first NAV on or after each target date. When a
scheme began after that target, its inception NAV is used and the normalized result
is marked partial. Calculations preserve decimal precision and round half-up to two
places. The supplied reference converts an exactly zero rounded return to `null`;
the compatibility method preserves that behavior. No value is annualized. See
`docs/performance-methodology.md` for candidate match rates, boundary uncertainty,
mapping evidence, and the complete reconciliation.

## Normalized analytics APIs

These routes use the shared `sif-unified-returns-v1` engine and verified Regular Growth
mappings. Legacy routes retain their supplied SIF360 response shapes.

### `GET /api/analytics/performance`

Accepts optional `to_date`. Returns all 34 funds with `r1d`, `r1w`, `r1m`, `r3m`,
`r6m`, `r1y`, `r2y`, `r3y`, `r5y`, `ytd`, and `si`. Each metric includes its value,
requested and selected start dates, start/end NAVs, elapsed days, method, status, and
warnings. Unsupported periods are `null` with `insufficient-history` status.

### `GET /api/analytics/monthly-heatmap`

Requires `range=12m|24m|36m|60m|all`; `to_date` is optional. Returns fund metadata,
month definitions, `monthlyMap`, cell metadata, and benchmark maps. The current month
is marked `partial`; unavailable cells are `null`.

### `GET /api/analytics/annual`

Accepts optional `to_date`. Returns completed calendar-year and Indian financial-year
maps, separate YTD and financial-YTD results, and calculation metadata. Ongoing years
are never included as completed returns.

### `GET /api/analytics/rolling`

Requires `fund_id=1..34` and `window=1m|3m|6m|1y`; `to_date` is optional. Returns the
rolling series and average, minimum, maximum, median, and valid-observation count.

### `GET /api/analytics/trend`

Requires `fund_id`, `from_date`, and `to_date`. Returns observed NAV history and
cumulative return from the first available observation in that range.

### `GET /api/analytics/benchmarks`

Returns configured benchmark coverage, PRICE/TRI declaration, history bounds, and
normalized returns. All three configured series are declared `PRICE`; the source and
official identifier are returned with each benchmark.

### `GET /api/analytics/benchmark-comparison`

Requires `fund_id`, `benchmark_id`, and optional `to_date`. It returns fund return,
benchmark return, excess percentage points, relative compounded return, and both
calculation records for every trailing period.

## `GET /api/funds/:slug`

Returns canonical fund identity and status, every verified Direct/Regular and
Growth/IDCW scheme variant, ISINs, latest NAV and daily change per variant, quarterly
AAUM records, and source/import timestamps. `factsheet` is `null`. `benchmark` and
`expenseRatio` explicitly report unavailable status until verified sources exist.

## Risk band by SIF scheme name

```text
GET /api/band?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/risk-band?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
```

`/api/risk-band` is an alias of `/api/band`. Both require a single `scheme_name`
query parameter and accept registered strategy names and verified full plan names,
using the same case, whitespace, and punctuation normalization as the allocation
APIs. A full normalized name must match; partial names do not select a fund.

The response is `{ "success": true, "data": { ... } }`, with `strategyId`,
`fullName`, `slug`, `reportId`, `reportMonth`, `portfolioAsOf`, `riskBandAsOf`, `availability`, the
following risk fields, and `source` metadata (`pdfUrl`, `validationStatus`, and
`latestListingVerified`). Example risk fields for the stored Dynasif Equity
Long-Short Fund report:

```json
{
  "availability": "PUBLISHED",
  "investmentStrategyRiskBand": "Level 5",
  "investmentStrategyRiskLevel": 5,
  "benchmarkRiskBand": "Level 5",
  "benchmarkRiskLevel": 5,
  "benchmarkName": "BSE 500 TRI"
}
```

Bands come from `sif_report_risk_bands` for the latest eligible official primary
report. The benchmark name falls back to the report's published benchmark when
the risk-band row has no benchmark name. A numeric level is returned only for an
explicit level from 1 through 5 (for example `Level 5` or `Risk Band Level 1`).
Published prose labels remain intact with a null numeric level.

`riskBandAsOf` is the date explicitly printed for the risk band, or null when no
separate date is printed. It can differ from `portfolioAsOf` (for example, the
stored Altiva September factsheets show risk bands as of August 31).

The shared Python factsheet extractor reads captions on each scheme's attributed
pages, using local OCR for embedded charts. Selected indicators without readable
captions use reviewed values tied to exact image SHA256. Scale ticks and monthly
history rows are not interpreted as the selected band. Source pages, captions,
image checksums and OCR evidence are retained in `Supplementary Fields/Risk Band Evidence`.

To enrich all existing latest reports without re-extracting their holdings:

```powershell
.\.venv\Scripts\python.exe -m scripts.extract_risk_bands --apply
.\.venv\Scripts\python.exe -m factsheetDataIngestion verify
```

Omit `--apply` for a preview. Normal scheduled extractions also apply the same
risk-band enrichment. The backfill archives checksum-verified source PDFs,
updates extraction JSON and monitor checksums, and loads the existing risk-band
table. It fails if a report has an ambiguous risk pair or a missing source PDF.
Schemes without an eligible official factsheet continue to return `FACTSHEET_NOT_FOUND`.

When both band labels are stored, availability is `PUBLISHED`; when only one is
stored, it is `PARTIAL`; when neither has been normalized, it is `UNKNOWN` and both
bands and levels are null. `UNKNOWN` does not assert that the publisher omitted
the information. A missing strategy band is never copied from the benchmark.

Validation and matching errors are 400 `INVALID_QUERY`, 404 `SCHEME_NOT_FOUND`,
409 `AMBIGUOUS_SCHEME`, or 404 `FACTSHEET_NOT_FOUND`, matching the name-based
allocation API conventions. Database failures return 500 `BAND_UNAVAILABLE`.

## Fund allocation charts

The per-fund routes use a strategy slug and apply to every registered fund, not
only Dynasif. Direct/Regular and Growth/IDCW variants share their strategy's
published portfolio allocations; these APIs do not imply separate plan portfolios.

```text
GET /api/funds/dynasif-equity-long-short-fund/allocation
GET /api/funds/dynasif-equity-long-short-fund/market-cap
GET /api/funds/dynasif-equity-long-short-fund/sector-allocation
GET /api/funds/dynasif-equity-long-short-fund/rating-allocation
```

The four routes accept a strategy slug and return the latest designated primary
factsheet from an official document with `PASSED` or `WARNING` validation. They read
the `ASSET`, `MARKET_CAP`, `SECTOR`, and `RATING` families respectively from
`sif_report_allocations`; they do not calculate allocations from holdings.
No query parameters are required. These routes also support other stored strategy slugs.

Example market-cap response for the locally stored September 2026 Dynasif report:

```json
{
  "success": true,
  "data": {
    "strategyId": 11,
    "fullName": "DynaSIF Equity Long-Short Fund",
    "slug": "dynasif-equity-long-short-fund",
    "allocationType": "market-cap",
    "reportId": 8,
    "reportMonth": "2026-09",
    "portfolioAsOf": "2026-09-30",
    "availability": "PUBLISHED",
    "unit": "percent",
    "totalPercent": 100.01,
    "items": [
      { "label": "Large Cap", "percent": 47.1, "basis": null, "sourcePage": null, "publishedOrder": 1 },
      { "label": "Small Cap", "percent": 37.2, "basis": null, "sourcePage": null, "publishedOrder": 3 },
      { "label": "Mid Cap", "percent": 15.71, "basis": null, "sourcePage": null, "publishedOrder": 2 }
    ],
    "source": {
      "pdfUrl": "https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/Dyna_SIF_Factsheet_as_on_September_30_2026_2191c44f31.pdf",
      "validationStatus": "PASSED",
      "latestListingVerified": false
    }
  }
}
```

All four responses share this shape. Chart labels use `data.items[].label` and
values use `data.items[].percent`. Items are sorted by descending percentage;
`publishedOrder` retains their original source order. Values stay in percentage
units (`47.1` means 47.1%), with full labels and published precision. Colors and
display rounding are frontend choices. Database-generated IDs can vary by installation.

Signs and zeros are preserved. September Dynasif asset data includes `Derivatives:
-1.2`; a donut renderer must handle signed exposure explicitly. `totalPercent`
is the signed sum of the returned items rounded to eight decimal places, never a
forced 100. Sector percentages retain their published denominator: the current
Dynasif sector sum is 76.28, asset sum is 100, and market-cap sum is 100.01.
These stored September values differ from the supplied screenshots, which do not
identify a reporting period. A rendering library may scale arcs to fit a circle, but labels
must still use the published percentages.

Missing percentages remain `null`. Missing sections return empty `items`, a null
total, and their stored availability (`NOT_PUBLISHED`, `PARSE_FAILED`, etc.). A
missing availability record falls back to `PUBLISHED` when rows exist, otherwise
`UNKNOWN`. Separate gross/net measures retain their `basis`; when multiple bases
occur, `totalPercent` is null and the client should render each basis separately.
It is also null when any returned percentage is missing.

`reportMonth`, `portfolioAsOf`, and `source` identify the stored source; these APIs
do not fetch PDFs or verify online freshness during requests.
`latestListingVerified: false` means the stored download has not been confirmed
against the publisher's latest online listing. A successful ingestion of a newer
eligible primary report becomes visible automatically.

The rating endpoint uses the published `Rating Profile (%)` field. The stored
September 2026 factsheet for DynaSIF Equity Long-Short Fund does not publish a
rating profile, so its response has `allocationType: "rating-allocation"`,
`availability: "NOT_PUBLISHED"`, `totalPercent: null`, and `items: []`, with the
same fund identity, reporting dates, and source metadata as the other endpoints.
The frontend should show an unavailable state for this fund. Ratings are not
inferred from asset classes or copied from the example chart. Other strategy
slugs with published ratings return their stored buckets and percentages.

Errors use the standard `{success:false,error:{code,message}}` shape: 400
`INVALID_SLUG`, 404 `FUND_NOT_FOUND`, 404 `FACTSHEET_NOT_FOUND` (a registered
strategy without an eligible report), 409 `AMBIGUOUS_FUND` (the slug is shared
across AMCs), or 500 `ALLOCATION_UNAVAILABLE`.

### Dynamic allocations by SIF scheme name

```text
GET /api/asset-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/market-cap-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/sector-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/rating-wise-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
```

These four separate endpoints require a single `scheme_name` query parameter.
It accepts a registered strategy name, such as `DynaSIF Equity Long-Short Fund`,
or a verified full scheme name, such as
`DynaSIF Equity Long - Short Fund - Direct Plan - Growth Option`. Scheme variants
resolve through `vw_sif_verified_strategy_schemes` to their strategy's factsheet;
an unverified scheme link is not used to select a fund. The names are matched in
full after removing case, whitespace, and non-ASCII-alphanumeric punctuation.
Partial names are not matched. If normalization matches multiple strategy IDs,
the request fails with 409 `AMBIGUOUS_SCHEME`.

The successful response is identical to the existing per-slug API:
`{ "success": true, "data": { ... } }`. `fullName` is the canonical strategy name;
the endpoint returns the selected strategy's reporting month, portfolio date,
availability, published `items`, signed total, and source metadata. Asset,
market-cap, sector, and rating-wise routes use `allocationType` values
`allocation`, `market-cap`, `sector-allocation`, and `rating-allocation`
respectively. Unpublished rating data still returns `NOT_PUBLISHED`, an empty
list, and a null total with HTTP 200. Name lookup and allocation selection use one
parameterized database query and one consistent snapshot.

Missing, blank, punctuation-only, repeated, or longer-than-500-character trimmed
names return 400 `INVALID_QUERY`. A name absent from the registered strategies
and verified scheme links returns 404 `SCHEME_NOT_FOUND`. A matched strategy
without an eligible report returns 404 `FACTSHEET_NOT_FOUND`. Database or invalid
numeric data failures return 500 `ALLOCATION_UNAVAILABLE`.

PowerShell example:

```powershell
$schemeName = [uri]::EscapeDataString('DynaSIF Equity Long-Short Fund')
Invoke-RestMethod "http://127.0.0.1:3000/api/market-cap-allocation?scheme_name=$schemeName" |
    ConvertTo-Json -Depth 10
```

The Postman collection in `docs/allocation-apis.postman_collection.json` includes
these four endpoints under `Dynamic allocations by scheme name`. Change its
`schemeName` collection variable to test another registered fund or verified plan.

### Allocations for all funds

```text
GET /api/funds/allocations/allocation
GET /api/funds/allocations/market-cap
GET /api/funds/allocations/sector-allocation
GET /api/funds/allocations/rating-allocation
```

Each bulk route returns `{ "success": true, "count": 34, "data": [...] }` for
the current database. `count` is dynamic. Each element of `data` has exactly the
same fields as the corresponding per-fund response's `data`, including its slug,
strategy identity, reporting date, allocation items, availability, and source.
Use that slug with `/api/funds/:slug/allocation`, `/market-cap`,
`/sector-allocation`, or `/rating-allocation` to fetch a single fund.

All registered strategies are included, in slug/strategy-ID order. The bulk
query reads one database snapshot per request and groups allocations by strategy
ID. It does not merge allocations across different funds. No query parameters
are required.

Strategies without an eligible official primary factsheet remain in the bulk
response with `availability: "MISSING"`, `reportId`, `reportMonth`, and
`portfolioAsOf` set to null, `items: []`, `totalPercent: null`, and null source
fields. Their per-fund route continues to return 404 `FACTSHEET_NOT_FOUND`.
After the Arthaya source refresh, 29 of the 34 registered strategies have eligible
stored reports; five have no report. A section absent from an existing report keeps its
stored `NOT_PUBLISHED` or other availability status. Bulk endpoints return 500
`ALLOCATION_UNAVAILABLE` if the database query fails, rather than an incomplete
success response. An empty strategy registry returns a successful zero count.

Arthaya's official source is
`https://www.unionmf.com/arthayasif/about-us/downloads#!#factsheets`.
Its downloaded September 30, 2026 factsheet is now ingested and its individual
name-based and slug-based routes return HTTP 200. Asset allocation totals 100%;
sector and market-cap figures retain the published percentage-of-net-assets basis
and total 73.12%. Rating allocation is `NOT_PUBLISHED`. The source PDF checksum is
verified; online latest-listing verification remains false.

## `GET /api/admin/tigzig/sync/status`

Returns the latest attempt and success, source freshness, recent failures, and
dataset manifest/database counts. Loopback development permits access without a key.
Any non-loopback `HOST` requires `ADMIN_API_KEY` at startup and requests must send:

```text
Authorization: Bearer <ADMIN_API_KEY>
```

TIGZIG does not include benchmark index history, so benchmark levels are loaded from
official BSE Indices and NSE Indices snapshots into `app_benchmark_history`. Detailed
TER data is reserved for the later factsheet pipeline; no TER values are synthesized.

## Compatibility routes

`GET /api/benchmark-performance` accepts optional `to_date` and returns calculated
`r1d`, `r1w`, `r1m`, and `r3m` metrics plus the latest official index level/date in
the supplied SIF360 response shape. `si` remains `null` because the imported source
window does not establish the indices' true inception.
`GET /api/sif-ter` returns the supplied top-level `success`, `month`, `count`, and
`funds` fields with a zero count and empty fund list. These endpoints preserve the
client contract without copying the unverified benchmark and factsheet values from
the example response.
