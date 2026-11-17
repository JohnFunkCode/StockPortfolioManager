-- V24: a paper buy's stop must name its kind (#431 phase 2 and #221, the
-- #567 review).
--
-- V21's orders_stop_rule let through a row with stop_kind NULL and the other
-- stop columns of a fixed or trailing stop: `stop_kind = 'fixed'` is NULL
-- there, the requested-stop branch came out NULL rather than false, and a
-- CHECK passes on NULL. The rule is the same as V21's but for
-- `stop_kind IS NOT NULL` in that branch, so it is now either all-empty or
-- one whole stop. The app never wrote such a row, so every existing row
-- passes.
--
-- APPLY BY HAND. Replacing a CHECK drops the old one, and the migrate Job
-- refuses a migration that drops anything:
--     ./scripts/flyway.sh migrate          (test)
--     ./scripts/flyway.sh --prod migrate   (prod)
-- then re-run the workflow. Both statements run in one transaction.
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_stop_rule;

ALTER TABLE orders
  ADD CONSTRAINT orders_stop_rule
      CHECK ((stop_kind IS NULL AND stop_level IS NULL AND stop_trail_pct IS NULL
              AND stop_method IS NULL AND stop_proposed_level IS NULL
              AND stop_set IS NULL)
             OR (side = 'buy' AND kind = 'market' AND stop_kind IS NOT NULL
                 AND stop_method IS NOT NULL
                 AND (stop_set IS NULL OR status = 'filled')
                 AND ((stop_kind = 'fixed' AND stop_level IS NOT NULL
                       AND stop_level > 0 AND stop_trail_pct IS NULL)
                      OR (stop_kind = 'trailing' AND stop_level IS NULL
                          AND stop_trail_pct IS NOT NULL
                          AND stop_trail_pct > 0 AND stop_trail_pct < 100))));
