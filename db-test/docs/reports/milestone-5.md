# Milestone 5 — scheduler, locking, and monitoring

Implemented an APScheduler worker fixed to `Asia/Kolkata` with configurable triggers
at 05:20, 12:00, 12:15, 18:00, and 21:00. The worker uses one execution thread plus
a stable PostgreSQL session advisory lock, so multiple worker processes still cannot
overlap. Capped exponential retry, jitter, network timeouts, misfire handling, and
restart recovery are configured through environment variables.

Manual manifest, backfill, incremental sync, forced revalidation, status, and
scheduler commands are available through `python -m ingestion.tigzig.worker`.
Run logs are JSON and database audits retain job identifiers, trigger and job types,
manifest and file state, counters, timestamps, duration, and bounded errors. Forced
revalidation reuses change-only UPSERTs and never deletes or truncates source data.

The Fastify read-only status service is exposed at
`GET /api/admin/tigzig/sync/status`. It returns the last attempt and successful data
sync, computed freshness, recent failed/interrupted runs, and manifest/database row
counts. Authentication is explicitly required before external exposure in a later
deployment milestone.

Verification covers the five configured IST triggers, invalid schedule rejection,
cross-session lock exclusion, deterministic retry timing, abandoned-run recovery,
JSON logs, real-database monitoring, and redacted monitoring failure responses.
