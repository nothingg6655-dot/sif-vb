# Milestone 3 historical import and reconciliation

Completed on 13 October 2026 (Asia/Kolkata). This milestone imported the verified
TIGZIG historical snapshots into PostgreSQL and then imported the identical snapshot
a second time to prove idempotency.

## Import implementation

- `staging.py` creates connection-scoped temporary metadata, NAV, AAUM, and rejection
  tables. Psycopg's PostgreSQL COPY protocol loads each validated dataset in bulk.
- `importer.py` fetches and verifies the current manifest, reads the three typed
  Parquet snapshots, validates all rows, and loads fund houses, schemes, ISINs, NAV,
  and AAUM through set-based SQL in foreign-key order.
- Staging and validation occur before the source transaction. Source records, run
  completion metrics, and successful Parquet version checkpoints commit together.
- Conflicts use `DO NOTHING` for the initial/backfill importer. This gives the required
  unchanged repeat behavior; classification and correction updates belong to the
  separately approved Milestone 4 synchronization engine.
- The checksum-listed `sif_nav.csv` is used only to retain `upload_ts_raw`. The typed
  NAV values and dates come from Parquet. `upload_ts_ist` is stored without timezone,
  so provider wall-clock values are never shifted as UTC. The parser and tests also
  preserve a trailing `Z` verbatim when a source snapshot carries one.
- Every rejected row is COPY-loaded into `tigzig_ingestion_rejections` with the run,
  dataset, complete raw record, source row number, validation code, and reason.

## Imported source version

Manifest `generated_at`: `2026-10-13T06:16:27+00:00` (11:46:27 IST).

- Metadata Parquet: 126 rows, SHA-256
  `2645448cd077e1fc20b03d96381d51d4c11a32cb05f2328b3fb6f2ead3d89007`.
- NAV Parquet: 15,500 rows, SHA-256
  `f705158793de2bfcc7e6788514c9df736c69e92d7799351d01aa96266cc44616`.
- AAUM Parquet: 295 rows, SHA-256
  `0f74bd6dda3b236202507f6aed4d619fd38d91b5e67b81d651635ac222c740fa`.
- NAV timestamp CSV: 15,500 rows, SHA-256
  `76902af930594ff537d1a18e55fee945d13282eef0f1043446ba41e4ca79ad1c`.

The three successful Parquet checkpoints reference the second successful run. The
auxiliary CSV checksum and purpose are recorded in each run's `files_processed` audit.

## Database reconciliation

| Dataset | Manifest rows | Imported rows | Latest-run rejected | Reconciled |
|---|---:|---:|---:|---:|
| Metadata | 126 | 126 | 0 | 126 |
| NAV | 15,500 | 15,496 | 4 | 15,500 |
| AAUM | 295 | 292 | 3 | 295 |

The normalized database contains 17 fund houses and 175 ISIN mappings. NAV coverage
is 8 October 2025 through 12 October 2026. AAUM quarter-end coverage is 31 December
2025 through 30 September 2026.

Reconciliation found zero duplicate scheme, NAV, or AAUM business-key groups; zero
orphan NAV, AAUM, or ISIN rows; zero AAUM lakh-to-crore conversion mismatches; and
zero nulls in required scheme, NAV, or AAUM identity/value fields. Five schemes have
no latest AAUM, and all 126 source rows omit the optional `txic_code`. All 15,496 NAV
rows retain both timestamp representations, with zero text-to-wall-clock mismatches
for the current snapshot. No factsheet table was created.

## Quarantined source records

Four NAV rows have a zero NAV and violate the positive-NAV rule:

- scheme codes 100 and 103 on 26 May 2026;
- scheme codes 100 and 103 on 8 June 2026.

Three AAUM rows for Arudha Equity Long-Short Fund Regular Plan Daily IDCW have null
scheme codes, for quarter ends 31 March, 30 June, and 30 September 2026. They were
not attached by name because the source identity cannot be verified. The complete
raw records and reasons are retained in both PostgreSQL and the JSON report. Each
backfill attempt has its own seven rejection audit rows, so the two successful runs
created 14 audit records without duplicating source facts.

## Repeat import result

The first run read 15,921 source rows, inserted 15,914 valid source records, updated
zero, skipped zero, and quarantined seven. It also inserted the 17 normalized fund
houses and 175 ISIN records.

The second run read the same 15,921 rows, inserted zero source records, updated zero,
skipped all 15,914 valid source records, and recorded the same seven source
rejections. Final source-table counts remained identical and all duplicate counts
remained zero.

## Verification

The Python suite reports **41 passing tests**, including COPY staging, exact timestamp
text retention, duplicate CSV business-key rejection, schema integrity, migration
safety, manifest handling, download integrity, Parquet parsing, and row validation.
The populated database also passed the read-only schema verifier with all legacy
objects preserved. Backend typecheck and build passed, together with all four backend
tests. Machine-readable results are in
`milestone-3-import-report.json`; the clean Python output is in `python-tests.txt`.

Milestone 4 incremental classification and upsert behavior, automated scheduling,
fund mapping, analytics, and business APIs remain unimplemented. Approval is required
before beginning Milestone 4.
