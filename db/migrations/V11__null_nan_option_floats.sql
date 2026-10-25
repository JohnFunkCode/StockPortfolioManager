-- V11: replace stored NaN in the options tables with NULL.
--
-- Postgres REAL accepts NaN, and the capture path wrote it: a chain side with
-- no contracts averages to NaN (pandas mean of nothing), and Yahoo's NaN
-- bid/ask is truthy, so it slipped past the `or 0` guards. Read back, a NaN
-- 500'd /options/iv-rank (JSON refuses NaN) and skewed max/min. On test at
-- 2026-10-25: 285 avg_call_iv, 717 avg_put_iv, 30,514 bid and 10,379 ask.
-- OptionsStore now writes NULL instead (`_finite`); this cleans what is there.
--
-- This is a DATA migration, not DDL. It must NOT be mirrored into `_SCHEMA` in
-- quantcore/db.py or db/schema_snapshot.json (same rule as V8).
--
-- In Postgres NaN = NaN is true, so `col = 'NaN'` finds them and
-- NULLIF(col, 'NaN') clears them. Every nullable REAL column of both tables is
-- covered, not only the four seen on test, and each table is scanned once.
-- `strike` is NOT NULL and part of the contracts' key, so it is left alone
-- (test has no NaN strikes; the capture drops strike <= 0, NaN included).

UPDATE options_expirations
   SET put_call_ratio = NULLIF(put_call_ratio, 'NaN'),
       avg_call_iv    = NULLIF(avg_call_iv, 'NaN'),
       avg_put_iv     = NULLIF(avg_put_iv, 'NaN')
 WHERE put_call_ratio = 'NaN'
    OR avg_call_iv    = 'NaN'
    OR avg_put_iv     = 'NaN';

UPDATE options_contracts
   SET last_price  = NULLIF(last_price, 'NaN'),
       bid         = NULLIF(bid, 'NaN'),
       ask         = NULLIF(ask, 'NaN'),
       implied_vol = NULLIF(implied_vol, 'NaN')
 WHERE last_price  = 'NaN'
    OR bid         = 'NaN'
    OR ask         = 'NaN'
    OR implied_vol = 'NaN';
