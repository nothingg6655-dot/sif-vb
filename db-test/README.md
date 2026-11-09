# VB-SIF

Milestones 1 through 7 implement the foundation, verified TIGZIG source-download
layer, historical and incremental ingestion, automated operations, and canonical
fund identity, calculated read models, and REST APIs.
The existing `db-test` directory is the project root; all commands below start here.

Implemented: public-schema migrations, eight TIGZIG tables, canonical fund and
verified mapping tables, the complete `sif_*` domain schema from `schema.sql`,
environment configuration, PostgreSQL connectivity,
a Fastify `/health` endpoint, manifest parsing, change detection, atomic Parquet
downloads, typed inspection, row validation, COPY staging, set-based historical
loading, source-version auditing, delta classification, conditional UPSERTs,
transactional recovery, reconciliation, and automated tests.

The factsheet storage schema and the `factsheetDataIngestion` integration are
installed. Factsheet extraction, normalization, and PostgreSQL database ingestion
load verified primary PDFs, child scheme reports, portfolio holdings, allocations,
managers, and AUM into the complete `sif_*` domain.


## Requirements

- PostgreSQL 14+ with an existing database named exactly `VB-SIF`.
- Node.js 22+ and npm.
- Python 3.12+.

Verified locally with PostgreSQL 18.3, Node.js 24.18.0, Python 3.14.6, and npm 10.9.0.

## Local setup (PowerShell)

```powershell
Copy-Item .env.example .env
# Edit .env and set the actual local database credentials.
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r ingestion/requirements.lock.txt
npm.cmd --prefix backend ci
```

The local `.env` is already configured in this workspace and ignored by Git.
Do not replace it unless reconfiguring the connection. Operating-system environment
variables take precedence over `.env`. A password is never embedded in a connection
URL, so special characters do not require URL encoding.

Connections are restricted to `VB-SIF` and `public`; session timezone is
`Asia/Kolkata`. The backend pool defaults to five connections with connection,
idle, and statement timeouts. Only `PGSSLMODE=disable` (local) or `verify-full`
(trusted server certificate) is supported consistently by both runtimes.

## Apply migrations

```powershell
.\.venv\Scripts\python.exe -m database.migrate
```

For a new empty database this creates the schema atomically. This workspace
previously contained five empty `sif_*` tables and `vw_sif_scheme_latest`, from the
earlier SQL request. Those objects were reported and preserved. The first run used:

```powershell
.\.venv\Scripts\python.exe -m database.migrate --preserve-legacy
```

That flag permits only the exact six documented initial objects with empty tables.
Any other initial public relation is refused for review. No migration drops or
truncates tables. The updated root `schema.sql` is implemented by the immutable,
checksum-tracked `database/migrations/0006_complete_sif_domain.sql` migration.

The versioned ledger bootstrap is
`database/bootstrap/0000_migration_ledger.sql`. Platform migrations are stored in
`database/migrations/`, including `0006_complete_sif_domain.sql`. The ledger records
each version, filename, SHA-256, and application timestamp. An advisory transaction
lock serializes migrators; failed DDL rolls back, checksums prevent editing applied
files, and subsequent runs skip already applied versions.

## Verify

```powershell
.\.venv\Scripts\python.exe -m database.verify
.\.venv\Scripts\python.exe -m unittest discover -s ingestion/tests -v
npm.cmd --prefix backend run db:check
npm.cmd --prefix backend run typecheck
npm.cmd --prefix backend run build
npm.cmd --prefix backend test
```

## Verify current TIGZIG snapshots

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.verify_source
```

The command fetches and validates the actual manifest structure before selecting
the three Parquet entries. It compares each SHA-256 with the last successful import
checkpoint. Changed files stream to same-directory temporary files with bounded
timeouts, exponential retries, byte and SHA-256 checks, cleanup on failure, and
atomic promotion. An unchanged successful source is not downloaded; its last-check
time is updated. A valid local cache may still be inspected without network transfer.

The command exits `0` when every source row validates, `2` when downloads are sound
but source rows require quarantine, and `1` for manifest, network, integrity, or
Parquet failures. Its report is written to
`docs/reports/milestone-2-download-verification.json`. The current provider snapshot
has seven rejected rows, so exit `2` is the expected outcome until TIGZIG corrects
them. This step never imports rows or advances successful-import hashes.

Downloaded files are stored under ignored `data/tigzig/`. Configuration is available
through `TIGZIG_BASE_URL`, `TIGZIG_DOWNLOAD_DIR`, `TIGZIG_HTTP_TIMEOUT_SECONDS`,
`TIGZIG_HTTP_RETRIES`, and `TIGZIG_HTTP_BACKOFF_SECONDS`.

## Import the historical snapshots

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.importer --repeat-test
```

The importer verifies the current manifest and downloads before opening a run audit.
It validates outside the main transaction, bulk-copies valid rows and rejection rows
into connection-scoped temporary tables, and uses set-based inserts in dependency
order. Successful file hashes advance only in the same transaction as the source
rows. The NAV Parquet remains the typed source; the checksum-listed NAV CSV supplies
the exact provider timestamp text, which is stored alongside its IST wall-clock value.

The completed Milestone 3 load contains 17 fund houses, 126 schemes, 175 ISINs,
15,496 NAV records, and 292 AAUM records. Four zero-NAV records and three AAUM
records without scheme codes are quarantined per run. A repeat of the same snapshot
inserted and updated zero source rows. Detailed reconciliation and rejection evidence
is in `docs/reports/milestone-3-import-report.json`.

## Synchronize updated snapshots

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.sync
```

The synchronization command validates and COPY-stages the complete snapshots, then
classifies each source record as `NEW`, `CHANGED`, `UNCHANGED`, or `INVALID` against
its full business key and stored values. Set-based UPSERTs use `IS DISTINCT FROM`,
so unchanged records keep their original `updated_at`. Corrections to old NAV dates
and AAUM quarters are applied, and late historical dates are inserted without relying
on the latest database date.

Source mutations, rejection audits, run completion, and successful file checkpoints
commit in one transaction. A failure rolls them all back and leaves a failed run audit
for safe retry. Snapshot omissions do not delete schemes, NAV, or AAUM. An identifier
is removed only when a metadata row present in the snapshot explicitly clears it.

The completed Milestone 4 live run classified all 15,914 valid source rows as
unchanged and made zero inserts or updates. Details are in
`docs/reports/milestone-4-sync-report.json`.

## Run and monitor the ingestion worker

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker manifest
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker backfill
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker sync
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker revalidate
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker status
.\.venv\Scripts\python.exe -m ingestion.tigzig.worker scheduler
```

The scheduler runs every two hours in `Asia/Kolkata` at 00:00, 02:00, through
22:00 daily. `TIGZIG_SCHEDULE_INTERVAL_HOURS` defaults to `2` and must divide 24
evenly. Retry limits, capped exponential backoff, jitter, misfire grace, and
startup recovery are environment-configurable. Every check compares the three Parquet
checksums and NAV timestamp CSV checksum with committed imports. An unchanged snapshot
records a successful check audit with zero source inserts or updates and skips downloads,
validation, staging, and analytics refresh. A changed snapshot is fully validated and
imported atomically; conditional UPSERTs still preserve unchanged rows. Explicit
`revalidate` bypasses the checksum shortcut. A stable PostgreSQL session advisory lock prevents overlap across
processes and is held through preparation, retry delays, and commit. Startup recovery
marks audits left `running` by a terminated lock owner as `interrupted` before syncing.

On Windows, install the persistent **SIF NAV Sync** task instead of leaving a
terminal scheduler running:

```powershell
.\scripts\install_nav_schedule.ps1
```

It uses the project virtual environment and current absolute paths, with daily
triggers at every even hour in IST. One-shot runs write `logs/nav-sync-YYYY-MM-DD.log`.
The task requests wake from sleep and catches a missed run when available; the
computer must be on, online, and the user logged in. Use one scheduling mechanism
to avoid duplicate checks.

Structured JSON logs and `tigzig_sync_runs` carry job identity, trigger, manifest,
file/download state, counters, timestamps, and bounded errors. Forced revalidation
uses conditional UPSERTs and never truncates production data. The read-only endpoint
`GET /api/admin/tigzig/sync/status` reports the latest attempt and success, freshness,
recent failures, and source/database row counts. Protect this admin endpoint before
exposing it outside the development network.

## Reconcile canonical funds

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.fund_mapping
```

The matcher uses exact fund-house identity, the provider's exact `category_sub` fund
family, normalized equality, declared plan/option, and scheme ISINs. It does not use
fuzzy similarity to write mappings. Incomplete or conflicting identities are written
to `app_tigzig_mapping_reviews` for human review. The current snapshot resolves 126
schemes into 34 active canonical funds with 175 verified external identifiers.

Each fund has one verified Direct Growth selection in `app_ticker_config` for internal
heatmap use. A separate `app_performance_config` maps external response IDs 1 through
34 to verified Regular Growth schemes. `/api/liveTicker` and `/api/performance-all`
both use that compatibility mapping, so their latest NAV values and ordering stay
consistent. `app_monthly_returns` and `app_performance_cache` reserve
scheme-level calculated read models for Milestone 7 without mixing derived values
into TIGZIG source tables. Reconciliation details are in
`docs/reports/milestone-6-reconciliation.json`.

## Ingest and monitor official factsheets

```powershell
# Ensure AMCs, canonical strategies, and scheme links exist
.\.venv\Scripts\python.exe -m factsheetDataIngestion seed-strategies

# Load extracted factsheet JSON and PDF documents into PostgreSQL
.\.venv\Scripts\python.exe -m factsheetDataIngestion load

# Run discovery and extraction across all official AMC sources
.\.venv\Scripts\python.exe -m factsheetDataIngestion extract

# Synchronize identities and load existing extracted factsheets into the database
.\.venv\Scripts\python.exe -m factsheetDataIngestion sync

# Check database factsheet status, documents, reports, and coverage
.\.venv\Scripts\python.exe -m factsheetDataIngestion status

# Compare every extracted factsheet with its stored report and child records (read-only)
.\.venv\Scripts\python.exe -m factsheetDataIngestion verify
```

The factsheet ingestion engine populates `sif_factsheet_documents`, `sif_factsheet_reports`,
and 15 normalized child tables (`sif_report_holdings`, `sif_report_allocations`,
`sif_report_strategy_profile`, `sif_report_fund_managers`, `sif_report_aum`,
`sif_report_nav`, `sif_report_expense_ratios`, etc.). The verified read models
`vw_sif_strategy_latest_report` and `vw_sif_strategy_directory` resolve strategy-level
reporting months, portfolio as-of dates, net AUM, benchmarks, and portfolio quants directly
from the ingested factsheets.

The loader preserves gross and net market-cap allocations as separate rows, loads
published asset/debt/other-exposure tables, and stores published performance, IDCW
history, links, page text, and AMC-specific evidence. Percentage values stay in
published percentage units; minimum investment amounts are converted to rupees
(for example, INR 10 lakh becomes 1,000,000). Missing numeric values remain NULL.
Values that the extractor has not normalized remain in the original JSON and page
evidence; they are marked `PARSE_FAILED`, not silently presented as zero.

Every source PDF is checked against its extraction SHA-256. Each report reload uses
a savepoint: a rejected report leaves its previous stored rows intact and is recorded
in `sif_ingestion_rejections`; subsequent files can still load. Repeating a successful
load reuses document/report identities and instruments, replaces report children,
and reports actual holdings/allocation and inserted/updated report counts.

Market-cap extraction reads attributed scheme pages, including continuation tables,
gross/net columns, and donut-chart legends. Raster market-cap charts use local
Tesseract when installed, or the built-in Windows OCR engine on Windows with an
installed recognition language. OCR runs locally and retains the recognized words,
chart checksum, source page, and extraction method in `Supplementary Fields`.
Published percentages keep their original denominator and are not rescaled to 100%.
`Field Availability` distinguishes `PUBLISHED`, `NOT_PUBLISHED` on inspected scheme
pages, and `PARSE_FAILED` when a detected section needs review. Missing values are
never inferred from top holdings or market-cap definitions in strategy prose.
Changes to market-cap parsing and its OCR bridge invalidate the monitor cache.

Run market-cap regression checks with:

```powershell
.\.venv\Scripts\python.exe -m unittest ingestion.tests.test_market_cap_extraction
```

The `sync` command seeds identities and loads existing extraction output. To discover
and extract fresh official PDFs first, run:

```powershell
.\.venv\Scripts\python.exe -m factsheetDataIngestion.monitor --once --output-dir data/factsheets
.\.venv\Scripts\python.exe -m factsheetDataIngestion sync --dir data/factsheets/latest
```


## Query the Milestone 7 APIs

```text
GET /api/liveTicker
GET /api/nav-history?sd_id=SIF-13&from_date=2025-10-22&to_date=2026-10-08&page=1&limit=500
GET /api/heatmap-all?window=6m&to_date=2026-10-08
GET /api/performance-all?to_date=2026-10-14&from1m=2026-09-14&from3m=2026-07-16
GET /api/funds/magnum-hybrid-long-short-fund
GET /api/funds/dynasif-equity-long-short-fund/allocation
GET /api/funds/dynasif-equity-long-short-fund/market-cap
GET /api/funds/dynasif-equity-long-short-fund/sector-allocation
GET /api/funds/dynasif-equity-long-short-fund/rating-allocation
GET /api/funds/allocations/allocation
GET /api/funds/allocations/market-cap
GET /api/funds/allocations/sector-allocation
GET /api/funds/allocations/rating-allocation
GET /api/asset-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/market-cap-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/sector-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/rating-wise-allocation?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/band?scheme_name=DynaSIF%20Equity%20Long-Short%20Fund
GET /api/benchmark-performance?to_date=2026-10-14
GET /api/sif-ter
GET /api/admin/tigzig/sync/status
GET /api/analytics/performance?to_date=2026-10-14
GET /api/analytics/monthly-heatmap?range=12m&to_date=2026-10-14
GET /api/analytics/annual?to_date=2026-10-14
GET /api/analytics/rolling?fund_id=1&window=1m&to_date=2026-10-14
GET /api/analytics/trend?fund_id=1&from_date=2026-09-01&to_date=2026-10-14
GET /api/analytics/benchmarks?to_date=2026-10-14
GET /api/analytics/benchmark-comparison?fund_id=1&benchmark_id=bse500&to_date=2026-10-14
```

NAV history supports pages from 1 and limits from 1 to 1,000. Heatmap windows are
`3m`, `6m`, `12m`, and `all`. All date parameters use `YYYY-MM-DD`. Invalid queries
return a stable `{success:false,error:{code,message}}` object.

Returns use `((ending NAV / starting NAV) - 1) * 100`. Performance uses verified
Regular Growth mappings, 30- and 90-calendar-day targets, and the first NAV on or
after each target. A newer fund uses inception and is marked partial internally.
Heatmap fund values use the NAV closest to each calendar boundary. Benchmark values
use official PRICE-index month-end closes. These are absolute returns and are never
presented as annualized returns. Full evidence and source limitations are in
`docs/analytics-methodology.md`.

Regenerate the mapping and per-metric reconciliation reports with:

```powershell
npm.cmd --prefix backend run performance:reconcile
npm.cmd --prefix backend run daily-change:reconcile
npm.cmd --prefix backend run analytics:reconcile
```

The ingestion transaction refreshes calculated rows only for schemes with inserted
or corrected NAV observations. A full one-time refresh is available with:

```powershell
.\.venv\Scripts\python.exe -m ingestion.tigzig.analytics
```

TIGZIG does not supply benchmark history. Verified BSE/NSE PRICE-index snapshots are
loaded separately with `scripts/import_official_benchmarks.py`; factsheet TER data is
not yet integrated and remains explicitly unavailable.
The admin endpoint is unauthenticated only while bound to loopback. Set
`ADMIN_API_KEY` and send `Authorization: Bearer <key>` before using a non-loopback
`HOST`; startup refuses an externally bound server without a key.

Database tests use real PostgreSQL and roll back inserted test rows. Identity
sequences may advance because PostgreSQL does not roll back sequence allocations.
Migration safety tests deliberately fail a temporary DDL transaction; the probe
table and its migration ledger entry must both disappear on rollback.

To also save a clean Python test log to `docs/reports/python-tests.txt`, run
`.\.venv\Scripts\python.exe -m ingestion.run_tests`.

`database.verify` is read-only and reports columns, constraints, indexes, and counts.
The pre-0006 comparison fixture remains historical evidence and is no longer an
expected equality target because the updated schema intentionally extends the five
original `sif_*` tables. Database tests require local migration privileges and should
run against a dedicated development database, not an active production deployment.

## Start the foundation server

```powershell
npm.cmd --prefix backend run build
npm.cmd --prefix backend start
```

From another terminal:

```powershell
curl.exe --fail http://127.0.0.1:3000/health
```

Expected response: `{"status":"ok","database":"connected"}`. The health endpoint
checks the database and returns HTTP 503 if unavailable. The server defaults to
loopback and closes its pool on shutdown. `npm.cmd --prefix backend run dev` enables
development reload. Business endpoints belong to Milestone 7.

## Project layout and documentation

- `backend/`: Fastify app/server, config, health route, service/repository/middleware
  directories, connection command, tests, TypeScript config, npm lockfile.
- `ingestion/tigzig/`: shared config, manifest reader, change detection, downloader,
  typed Parquet reader, validator, COPY staging, historical importer, and verification
  and incremental synchronization commands; later milestone placeholders.
- `ingestion/tests/`: schema integrity and migration safety tests.
- `database/`: ledger bootstrap, versioned migrations, migration runner, read-only
  verifier, empty seeds and query directories.
- `docs/schema.md`: tables, relationships, indexes, and field semantics.
- `docs/source-inspection.md`: inspected manifest shape and source conventions.
- `docs/reports/milestone-1.md`: executed checks, discrepancy, and milestone boundary.
- `docs/reports/milestone-2.md`: download and validation implementation report.
- `docs/reports/milestone-2-download-verification.json`: current source evidence.
- `docs/reports/milestone-3.md`: historical import and reconciliation report.
- `docs/reports/milestone-3-import-report.json`: machine-readable run, reconciliation,
  source-version, and rejected-record evidence.
- `docs/reports/milestone-4.md`: incremental synchronization and recovery report.
- `docs/reports/milestone-4-sync-report.json`: live delta classifications, mutations,
  run metrics, and database reconciliation.
- `docs/reports/milestone-5.md`: scheduler, lock, retry, recovery, and monitoring evidence.
- `docs/reports/milestone-6.md`: canonical identity design and reconciliation evidence.
- `docs/reports/milestone-6-reconciliation.json`: machine-readable mapping counts.
- `docs/api.md`: endpoint contracts, validation, return methodology, and limitations.
- `docs/reports/milestone-7.md`: executed API and analytics validation evidence.
- `docs/reports/preflight.json`: original database snapshot.
- `docs/reports/schema-verified.json`: captured database-schema verification report.

The scheduler implementation is ready to run but is not installed as an operating
system service in this local workspace. The local connection uses the previously
provided `postgres` account; separate least-privilege runtime roles and deployment
configuration belong to approved production hardening work.

## Factsheet allocation and holdings JSON

Each available strategy has one JSON file under `data/factsheets/latest/extraction/`.
The primary fields are `Sector Allocation (%)`, `Asset Allocation (%)`,
`Rating Profile (%)`, `Market Capitalization (%)`, `Portfolio`, and
`Derivative Portfolio`. Holdings retain their source page, category, sector,
weight and published quantity/value when available. Aggregate rows are flagged.

`Supplementary Fields["Published Allocation Groups"]` retains chart/table titles,
source pages, original labels, percentages and allocation basis. Benchmark series,
cash-sector subtotals, derivative-sector exposure and rounded top-ten charts remain
separate groups. The primary sector field uses the complete printed sector table
when available; ITI and quant's comparative charts use the published scheme series.
Do not combine categories across these different bases or normalize them to 100%.
`Field Availability` distinguishes published data, absent source data and parse failures.

The five quant relative-weight images have tiny rotated labels that OCR does not
read reliably. Their visually verified printed values are stored in
`factsheetDataIngestion/reviewed_raster_allocations.json`, keyed by the exact embedded
image SHA256. Changed images require review and produce `PARSE_FAILED` instead of
reusing stale weights. Other quant asset and market-cap images use local OCR.

Re-extract the downloaded source PDFs, then bundle the results:

```powershell
.venv/Scripts/python.exe -m factsheetDataIngestion.monitor --once --offline --no-browser --config data/factsheets/local_sources.json --output-dir data/factsheets
.venv/Scripts/python.exe -m scripts.export_allocations_and_holdings
```

The export writes `extracted_factsheets.json`, `allocation-holdings-coverage.json`
and `scheme-allocations-and-holdings.zip` in `data/factsheets/latest/`.
Only available local reports can be extracted. Some publishers supply top-ten
holdings or partial allocation categories; the original publication scope is retained.
