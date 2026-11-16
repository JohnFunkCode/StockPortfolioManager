-- V20: what the bar-close checker records on a stop (#431 phase 2 and #221,
-- plan step 4.2 in docs/proposals/paper-books-realized-pnl-plan.md).
--
-- last_bar_checked   the session of the newest closed daily bar the checker
--                    has tested the stop against. NULL until its first check.
--                    The checker works through every closed bar after it, so
--                    a night the daily job missed is made up on the next run.
-- triggered_session  the session of the bar that reached the level
-- trigger_price      the price the stop was reached at: its level, or that
--                    session's open when the stock opened below the level.
--                    A paper book's sale fills at it.
-- alerted_at         when the breach alert for a real-book stop went out.
--                    NULL means it is still owed, so a failed send is made up
--                    on the next run and a sent one is never repeated.
--
-- ADD COLUMN only, so CI applies it. No stop is 'triggered' yet, so the rule
-- holds for every existing row.
ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS last_bar_checked  DATE,
  ADD COLUMN IF NOT EXISTS triggered_session DATE,
  ADD COLUMN IF NOT EXISTS alerted_at        TIMESTAMPTZ;

ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS trigger_price NUMERIC(18,6)
      CONSTRAINT stops_triggered_has_trigger
      CHECK ((status = 'triggered')
             = (trigger_price IS NOT NULL AND triggered_session IS NOT NULL));
