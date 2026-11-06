# Supabase database deployment

The current app uses the Supabase session pooler on port 5432. Flutter continues
to call the existing Node backend at localhost:3000; database credentials are
never sent to the Flutter client.

## Configuration

- `.env`: active cloud connection. Python uses the `sif_ingestion` write role;
  Node uses the separate `BACKEND_PGUSER` / `BACKEND_PGPASSWORD` read-only role.
- `.env.supabase`: administrator connection for schema maintenance.
- `.env.local-backup`: original local database configuration for rollback.
- `PGEXPECTED_DATABASE=postgres` explicitly enables this cloud database target.
- `PGSSLMODE=verify-full` and `PGSSLROOTCERT` verify the pooler's certificate.
- `data/certs/supabase-ca.crt`: CA downloaded from Supabase's published endpoint.

These environment files, certificates, migration backups and generated role
credentials are under ignored paths. Do not commit database passwords or dumps.

Use `SIF_ENV_FILE=.env.supabase` for administrator-only migration commands.
Clear that variable afterward so normal workers return to their limited role.
The copied `app_schema_migrations` ledger retains all existing migration records;
do not replay the initial schema SQL over the restored database.

## Verification

Initial deployment copied and verified:

- 63 tables and 27,102 rows, compared by canonical row hashes;
- 31 sequence values and 99 table/view/sequence objects;
- 14 API responses compared with the original local database;
- row-level security on all 63 tables;
- no public/anonymous/authenticated direct table reads;
- no backend-role table writes;
- ingestion temporary tables, session advisory locks and rollback-only writes.

Supavisor ignores some startup `options`, including the supplied timezone.
Cloud roles have an explicit Asia/Kolkata default; Node also initializes each
pool connection with SQL. Row-hash comparisons explicitly use UTC to compare
timestamp values independently of their display timezone.

Detailed reports and the local custom-format backup are recorded in the directory
referenced by `data/supabase-migration-latest.json`. The migration utility refuses
to overwrite an existing Supabase application schema.

## Running the current application

Run `npm run dev` from `backend/` for Node. Run the existing Flutter web command
from `sif_research/`. The active `.env` supplies the cloud database connection.
Run `python -m ingestion.tigzig.worker sync` from this directory for a manual
NAV/AAUM sync. Existing factsheet and benchmark ingestion commands read the same
cloud connection configuration.

The database is hosted by Supabase. The current Node and Flutter servers continue
to run on this computer; public application hosting is a separate deployment.

## Rollback

The original local `VB-SIF` database was preserved. To switch back, stop the
backend and workers, preserve the active cloud `.env`, copy `.env.local-backup`
to `.env`, then restart the backend and workers. Cloud changes made after cutover
must be reconciled separately before choosing a permanent rollback.
