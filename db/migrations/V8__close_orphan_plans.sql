-- V8: close harvest plans that have no shares under them (#147 Part H5/H8).
--
-- From this release a plan may only be built for a symbol the owner holds an
-- OPEN lot of, and selling the last lot closes the plan. Rows that predate the
-- invariant can violate it: an ACTIVE ladder on a symbol its owner sold out of
-- keeps firing alerts for a position nobody holds.
--
-- This is a DATA migration, not DDL. It must NOT be mirrored into `_SCHEMA` in
-- quantcore/db.py -- init_schema() runs on every application startup, and a
-- backfill that re-runs on every startup would close plans built moments
-- earlier by a request whose position write had not yet landed.
--
-- CLOSED rather than SUPERSEDED because nothing replaced these plans. Run the
-- read-only pre-flight in docs/proposals/legacy-report-retirement-plan.md
-- (Part H8) first to see exactly which rows this will touch.

UPDATE plan_instances pi
   SET status = 'CLOSED',
       notes = CASE
           WHEN pi.notes IS NULL OR pi.notes = '' THEN 'closed by V8: no open position'
           ELSE pi.notes || ' | closed by V8: no open position'
       END
 WHERE pi.status = 'ACTIVE'
   AND NOT EXISTS (
       SELECT 1
       FROM positions p
       WHERE p.symbol_id = pi.symbol_id
         AND p.owner = pi.owner
         AND p.status = 'OPEN'
   );
