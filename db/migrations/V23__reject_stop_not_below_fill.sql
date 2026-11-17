-- V23: one more reason a paper order can be rejected (#431 phase 2 and #221,
-- decision 64 in docs/proposals/paper-books-realized-pnl-plan.md).
--
-- 'stop_not_below_fill': a queued buy asked for a stop, and the price it
-- would fill at leaves no room for one. A fixed level is at or above the
-- opening price, or a trailing percentage is too small to give a price under
-- it. The buy is rejected, so no position is opened without the stop it was
-- asked for.
--
-- APPLY BY HAND. Widening a CHECK means replacing the constraint, and the
-- migrate Job refuses a migration that drops anything:
--     ./scripts/flyway.sh migrate          (test)
--     ./scripts/flyway.sh --prod migrate   (prod)
-- then re-run the workflow. Both statements run in one transaction, and the
-- new list holds every value the old one did, so every existing row passes.
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_reject_reason_check;

ALTER TABLE orders
  ADD CONSTRAINT orders_reject_reason_check
      CHECK (reject_reason IN (
          'stale_quote', 'no_quote', 'non_usd', 'insufficient_cash',
          'insufficient_shares', 'no_open_price', 'stop_not_below_fill'));
