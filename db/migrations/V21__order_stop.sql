-- V21: the stop a paper buy asks for (#431 phase 2 and #221, plan step 4.3
-- in docs/proposals/paper-books-realized-pnl-plan.md).
--
-- A buy order can carry a stop. The stop row itself is written when the buy
-- fills, in the fill's transaction, so a queued order has no stop until it
-- fills and a rejected order never gets one. These columns hold what was
-- asked for until then, and what became of it.
--
-- stop_kind            'fixed' or 'trailing'. NULL: the order asked for no stop.
-- stop_level           a fixed stop's level, as asked for
-- stop_trail_pct       a trailing stop's percentage below its high-water
--                      mark, which starts at the fill price
-- stop_method          how the level was chosen: 'manual', or one of the
--                      stop card's candidates. No CHECK, like stops.method.
-- stop_proposed_level  the level the stop card suggested, when one did
-- stop_set             TRUE once the fill wrote the stop. FALSE when the
--                      order filled and the stop could not be set: a fixed
--                      level at or above the fill price. NULL before the
--                      fill, and on an order that asked for no stop.
--
-- ADD COLUMN only, so CI applies it. Every existing order asked for no stop,
-- so the rule holds for every existing row.
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS stop_kind           TEXT,
  ADD COLUMN IF NOT EXISTS stop_level          NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS stop_trail_pct      NUMERIC(7,4),
  ADD COLUMN IF NOT EXISTS stop_method         TEXT,
  ADD COLUMN IF NOT EXISTS stop_proposed_level NUMERIC(18,6);

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS stop_set BOOLEAN
      CONSTRAINT orders_stop_rule
      CHECK ((stop_kind IS NULL AND stop_level IS NULL AND stop_trail_pct IS NULL
              AND stop_method IS NULL AND stop_proposed_level IS NULL
              AND stop_set IS NULL)
             OR (side = 'buy' AND kind = 'market' AND stop_method IS NOT NULL
                 AND (stop_set IS NULL OR status = 'filled')
                 AND ((stop_kind = 'fixed' AND stop_level IS NOT NULL
                       AND stop_level > 0 AND stop_trail_pct IS NULL)
                      OR (stop_kind = 'trailing' AND stop_level IS NULL
                          AND stop_trail_pct IS NOT NULL
                          AND stop_trail_pct > 0 AND stop_trail_pct < 100))));
