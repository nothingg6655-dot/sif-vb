# Milestone 4 incremental synchronization

Completed on 13 October 2026 (Asia/Kolkata). This milestone implements complete
snapshot comparison while writing only new or changed PostgreSQL records.

## Synchronization design

- The existing verified download and validation pipeline prepares the three Parquet
  snapshots and exact NAV timestamp text before database mutation begins.
- Valid metadata, NAV, AAUM, and rejection records are bulk-loaded into temporary
  staging tables with PostgreSQL COPY outside the main mutation transaction.
- Set-based joins classify each metadata, NAV, and AAUM record as `NEW`, `CHANGED`,
  or `UNCHANGED`; validation issues are counted as `INVALID` and retain their raw
  source records.
- Classification compares complete stored values with `IS DISTINCT FROM`, including
  nullable fields. NAV and AAUM comparisons use business keys across the entire
  snapshot, so old corrections and late records are detected.
- Set-based UPSERTs insert new records and update only changed records. Every update
  has an `IS DISTINCT FROM` predicate, so identical snapshots do not change
  `updated_at` timestamps.
- Fund houses and schemes are synchronized before identifiers and historical facts.
  Identifier changes, explicit removals for schemes present in the snapshot, and
  atomic ISIN swaps are supported. Duplicate staged ISIN assignments fail safely.
- A snapshot omission never deletes a scheme, NAV record, or AAUM quarter. An ISIN is
  removed only when the current metadata row for that scheme explicitly publishes a
  null identifier.
- Source mutations, rejection inserts, successful run metrics, and all three file
  checkpoints are one transaction. A failed transaction preserves the prior source
  data and hashes; the separately committed run audit is marked failed.

No schema migration was required because the Milestone 1 keys, audit tables, source
version fields, and explicit `updated_at` columns already support this behavior.

## Live synchronization result

Run: `8155217b-855f-41ae-a36b-76d96d9466da`.

Manifest `generated_at`: `2026-10-13T06:16:27+00:00`.

| Dataset | New | Changed | Unchanged | Invalid |
|---|---:|---:|---:|---:|
| Metadata | 0 | 0 | 126 | 0 |
| NAV | 0 | 0 | 15,496 | 4 |
| AAUM | 0 | 0 | 292 | 3 |
| ISIN relationships | 0 | 0 | 175 | 0 |

The run read 15,921 source records, inserted zero, updated zero, skipped 15,914,
and quarantined seven. All mutation counters were zero. The database remains at 17
fund houses, 126 schemes, 175 ISIN relationships, 15,496 NAV records, and 292 AAUM
records.

Manifest reconciliation remains exact: 126 metadata rows; 15,496 NAV plus four NAV
rejections equals 15,500; and 292 AAUM plus three AAUM rejections equals 295. There
are zero duplicate business-key groups, zero orphan records, and zero AAUM conversion
mismatches. The successful file checkpoints now reference the live synchronization
run.

## Recovery and behavior tests

Twelve Milestone 4 integration tests cover:

- a new scheme and identifier;
- a new NAV date;
- correction of an old NAV value and publication timestamp;
- insertion of a late NAV date while retaining existing history;
- changed scheme metadata;
- ISIN-only corrections, explicit removal, and an atomic relationship swap;
- a new AAUM quarter and correction of an existing quarter;
- an identical reimport with unchanged `updated_at` values;
- an unknown scheme reference classified as invalid;
- an injected interruption after source writes, run completion, and checkpoint writes.

The interruption test proves all transaction work rolls back, prior hashes remain
unchanged, the run is marked failed, and the identical source succeeds on retry. Test
fixtures restore the original live source rows and checkpoints after completion.

The full Python suite reports **53 passing tests**. The backend typecheck, build, tests,
read-only database verification, artifact checks, and credential scan are included in
the final milestone verification. Machine-readable live results are in
`milestone-4-sync-report.json`, and Python output is in `python-tests.txt`.

Milestone 5 scheduling, advisory locking, retry orchestration, and monitoring remain
unimplemented. Approval is required before beginning Milestone 5.
