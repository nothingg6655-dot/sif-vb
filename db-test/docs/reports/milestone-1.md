# Milestone 1 implementation and verification

Completed on 13 October 2026 (Asia/Kolkata) in the existing `db-test` project root.

## Outcome

The project foundation is implemented and connected to `VB-SIF.public`.
PostgreSQL migration 0001 is applied. All 27 tests pass, the TypeScript build
passes, and the compiled Fastify server responds successfully over real HTTP.
Milestone 2 has not started and requires explicit approval.

## Database discrepancy and preservation

The prompt assumed an empty database. Preflight found five empty tables:
`sif_amcs`, `sif_schemes`, `sif_scheme_isins`, `sif_nav_history`, and
`sif_aaum_history`, plus the view `vw_sif_scheme_latest` from the earlier request.
This discrepancy was reported before creating new tables. No original objects
were dropped or altered. Read-only before/after snapshots compare row counts,
column types/defaults, constraints, indexes, and the view definition.
`schema-verified.json` confirms `legacy_preserved: true`.

## Files created

- Root: `.env.example`, local ignored `.env`, `.gitignore`, `README.md`.
- Backend: `package.json`, `package-lock.json`, `tsconfig.json`, `src/app.ts`,
  `src/server.ts`, `src/config/env.ts`, `src/config/database.ts`,
  `src/routes/health.ts`, `src/utils/check-database.ts`,
  `tests/foundation.test.ts`, and `tests/health.test.ts`.
- Backend placeholder directories: `src/services/`, `src/repositories/`, and
  `src/middleware/`, each tracked with `.gitkeep`.
- Python: `ingestion/__init__.py`, `ingestion/tigzig/__init__.py`,
  `ingestion/tigzig/config.py`, `ingestion/requirements.txt`,
  `ingestion/requirements.lock.txt`, `ingestion/run_tests.py`,
  `ingestion/tests/__init__.py`, `ingestion/tests/test_schema.py`, and
  `ingestion/tests/test_migrations.py`.
- Future worker placeholders: `manifest.py`, `downloader.py`, `validator.py`,
  `staging.py`, `importer.py`, `scheduler.py`, and `sync.py` in `ingestion/tigzig/`.
  Each contains a milestone-specific docstring only.
- Database: `database/__init__.py`, `database/migrate.py`, `database/verify.py`,
  `database/bootstrap/0000_migration_ledger.sql`,
  `database/migrations/0001_tigzig_foundation.sql`, and empty `seeds/` and `queries/`
  directories tracked with `.gitkeep`.
- Documentation: `docs/schema.md`, `docs/source-inspection.md`, this report,
  the preflight and verified JSON snapshots, Python/backend test logs,
  `http-health.json`, and the inspected source manifest and README copies.

The original root `schema.sql` is retained unchanged. Generated `node_modules/`,
`backend/dist/`, `.venv/`, and Python caches are ignored.

## Tables and relationships

Eight provider tables created:

1. `tigzig_fund_houses`
2. `tigzig_schemes`
3. `tigzig_scheme_isins`
4. `tigzig_scheme_nav`
5. `tigzig_scheme_aaum`
6. `tigzig_sync_runs`
7. `tigzig_sync_file_versions`
8. `tigzig_ingestion_rejections`

Two canonical identity tables created:

1. `app_funds`
2. `app_tigzig_fund_mapping`

The migration framework also created `app_schema_migrations`.
All eleven new tables have primary keys. There are eight foreign keys:
schemes to fund houses; ISIN/NAV/AAUM to composite source schemes; file versions
and rejections to runs; verified mappings to both funds and schemes.

Scheme identity is `(source, scheme_code)`. NAV adds `nav_date` to that key;
AAUM adds `quarter_end`. Two identifiers refer to the same scheme. A canonical
fund can map to multiple plan/option variants, and a scheme maps to at most one
fund. Verification evidence is required before mapping insertion.

Ten explicit indexes support fund-house/category lookup, global NAV and AAUM dates,
recent and failed runs, version-to-run and rejection-to-run references, pending
rejection review, and fund-to-scheme lookup. Primary/unique constraints provide
identity indexes. Detailed definitions and a relationship diagram are in
`docs/schema.md`; exact catalog output is in `schema-verified.json`.

All source, canonical, mapping, checkpoint, rejection, and run tables contain zero
persisted rows after tests. Only the migration ledger contains its one applied
platform version. No factsheet tables or calculated analytics tables were created.

## Migrations executed

- `0000_migration_ledger.sql`: versioned framework bootstrap, executed transactionally
  to create the migration ledger. It is separate from the platform version records.
- `0001_tigzig_foundation.sql`: applied once and recorded with its SHA-256.
- A repeated migration run applied zero new versions.
- The checksum test changed a temporary copy of 0001 and verified rejection.
- The rollback test attempted temporary migration 0002, created a probe table,
  and deliberately raised division by zero. Both DDL and ledger changes rolled back;
  no 0002 migration exists in the final database or migration directory.

## Executed verification

- Python: **23 tests passed**, including 19 schema integrity tests and four migration
  safety tests. Log: `docs/reports/python-tests.txt`.
- Backend: **4 tests passed**, including a real database-backed health route and
  unavailable-database HTTP 503 response without leaking error details.
  Log: `docs/reports/backend-tests.txt`.
- `npm run typecheck`: passed.
- `npm run build`: passed.
- `npm run db:check`: connected to `VB-SIF / public`.
- Real HTTP GET `/health` against `node dist/server.js`: HTTP 200 with
  `{"status":"ok","database":"connected"}`. Response saved in `http-health.json`.
- The smoke-test server was stopped after verification.
- Legacy definitions and zero-row counts preserved; no factsheet tables exist.
- Source credential scan: supplied password absent from project source/report
  files outside the ignored local `.env`.
- Dependency installation audit reported zero npm vulnerabilities at install time.

Constraint coverage includes duplicate schemes/NAV/AAUM, source-aware foreign keys,
orphan child/run/mapping references, two ISINs on one scheme, identifier conflicts,
positive finite NAV, exact financial precision, AAUM unit conversion and date order,
nullable/non-key temporary metadata, unique slugs, and four verified variants on
one fund. All fixture rows were rolled back; identity sequence allocations may advance.

## Run and verify

From the project root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m database.migrate
.\.venv\Scripts\python.exe -m ingestion.run_tests
.\.venv\Scripts\python.exe -m database.verify --compare-legacy docs/reports/preflight.json
npm.cmd --prefix backend run db:check
npm.cmd --prefix backend run typecheck
npm.cmd --prefix backend run build
npm.cmd --prefix backend test
npm.cmd --prefix backend start
```

Check `http://127.0.0.1:3000/health` from another terminal. Stop the server with
Ctrl+C. Installation and environment instructions are in `README.md`.

## Issues and milestone boundary

No unresolved Milestone 1 blockers. The earlier schema remains as documented.
The local development connection uses the supplied `postgres` account; least-privilege
deployment roles and production operations belong to later hardening work.

Only manifest and documentation copies were fetched for schema inspection.
No Parquet downloads, parser, validation service, importer, scheduler, mapping engine,
analytics, fund API, deployment, or factsheet pipeline has been implemented.
Approval is required before beginning Milestone 2.
