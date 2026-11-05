# Milestone 2 implementation and verification

Completed on 13 October 2026 (Asia/Kolkata). This milestone downloads and validates
the current TIGZIG snapshots; it does not import them into PostgreSQL.

## Implemented services

- `manifest.py` fetches `manifest.json` with a timeout and size limit, parses the
  actual top-level `files` array, requires a timezone-aware `generated_at`, rejects
  duplicate filenames, and validates rows, columns, byte sizes, and SHA-256 values.
  It selects the three required Parquet entries by filename without hardcoded JSON paths.
- `source_versions.py` compares remote hashes only with successful-import hashes in
  PostgreSQL. It records `last_checked_at` but cannot advance SHA, manifest time,
  row count, success time, or import-run fields. Unchanged successful snapshots are
  skipped even when no local cache exists.
- `downloader.py` streams into a temporary file in the destination directory, applies
  bounded timeouts and configurable exponential retry/backoff with jitter, rejects
  Content-Length/manifest-size mismatches, verifies SHA-256, flushes and fsyncs,
  atomically promotes the file, and removes temporary files after failures. Existing
  cached data is retained until a verified replacement is ready.
- `parquet.py` uses PyArrow to read typed snapshots and checks physical row count and
  ordered columns against the fetched manifest. It reports Arrow types, null counts,
  and date/timestamp ranges. Corrupt and truncated files fail before validation.
- `validator.py` tolerates additional optional columns while requiring stable business
  columns. It validates scheme identities/names, NAV dates and positive finite values,
  AAUM dates/nonnegative values/lakh-to-crore conversion, duplicate keys, and scheme
  references. Every invalid row remains attached to a structured reason.
- `verify_source.py` runs the complete read-only source workflow and writes its report
  atomically. Exit code 2 indicates quarantinable source rows; it is distinct from an
  integrity or execution failure.

## Current manifest and downloads

Manifest `generated_at`: `2026-10-13T06:16:27+00:00` (11:46:27 IST).

- Metadata: 126 rows, 11,468 bytes, SHA-256
  `2645448cd077e1fc20b03d96381d51d4c11a32cb05f2328b3fb6f2ead3d89007`.
- NAV: 15,500 rows, 96,199 bytes, SHA-256
  `f705158793de2bfcc7e6788514c9df736c69e92d7799351d01aa96266cc44616`.
- AAUM: 295 rows, 8,936 bytes, SHA-256
  `0f74bd6dda3b236202507f6aed4d619fd38d91b5e67b81d651635ac222c740fa`.

All three were downloaded and passed byte-size, SHA-256, Parquet footer, row-count,
and ordered-column verification. A second execution reused the verified local cache.
The files are ignored under `data/tigzig/`. Exact types, null counts, date ranges,
and rejection records are in `milestone-2-download-verification.json`.

Observed coverage is NAV dates 8 October 2025 through 12 October 2026, and AAUM
quarter ends 31 December 2025 through 30 September 2026. The source carries typed
`date32` fields. `upload_ts` is a timestamp without timezone in Parquet; Milestone 3
must preserve its raw provider semantics because TIGZIG documents these values as
IST wall-clock times even when CSV source text carries a trailing `Z`.

## Validation result

- Metadata: 126 input, 126 valid, zero rejected, zero duplicate keys.
- NAV: 15,500 input, 15,496 valid, four rejected, zero duplicate keys.
- AAUM: 295 input, 292 valid, three rejected, zero duplicate keys.

The four rejected NAV records are zero values for scheme codes 100 and 103 on
26 May 2026 and 8 June 2026. NAV must be positive under the approved database model.

The three rejected AAUM records have a null `scheme_code`. They describe the Arudha
Equity Long-Short Fund Regular Plan Daily IDCW for quarter ends 31 March, 30 June,
and 30 September 2026. Name-based inference is intentionally not used. Milestone 3
must quarantine them unless a verified source identity becomes available.

No conflicting duplicate metadata, NAV, or AAUM business keys were found. The seven
records were retained in the JSON report with source row number, code, reason, and
complete raw record. They were not silently discarded or written to source tables.

## Automated tests

The full Python suite reports **38 passing tests**. Milestone 2 coverage includes:

- valid and malformed manifests;
- successful streaming and verified cache reuse;
- timeout retries with exponential backoff;
- SHA-256 mismatch, size mismatch, cleanup, and preservation of the prior cache;
- truncated Parquet and manifest row/column disagreement;
- missing required columns and additive optional columns;
- invalid and nonfinite numbers, duplicate business keys, unknown schemes,
  reversed quarters, and incorrect AAUM conversion;
- unchanged source download suppression;
- all Milestone 1 migration and database constraints.

The clean test output is saved in `docs/reports/python-tests.txt`. PyArrow 23.0.1
is pinned in `ingestion/requirements.lock.txt`.

## Database state and boundary

The three checkpoint rows contain filenames and `last_checked_at` only. Their hashes,
manifest timestamps, successful import timestamps, row counts, and run references
remain null. All TIGZIG source tables, sync runs, database rejections, and application
mapping tables remain empty. There was no schema migration in Milestone 2.

No staging tables, COPY operations, database importer, historical load, UPSERT,
scheduler, mapping engine, API, benchmark, TER, factsheet, or calculated data was
implemented. Approval is required before beginning Milestone 3.
