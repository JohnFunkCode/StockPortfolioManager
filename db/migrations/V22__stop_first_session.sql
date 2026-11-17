-- V22: the first session a stop is tested against, when a buy's fill sets it
-- (#431 phase 2 and #221, decision 63 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- first_session  NULL for a stop set by hand: it is first tested against the
--                first session to open after created_at. Set for the stop of
--                a buy that filled at a session's opening price: the session
--                it filled in. The whole of that day's bar came after the
--                fill, so the stop is live from it, as a stop attached to a
--                broker's order is live from the moment the entry fills.
--
-- ADD COLUMN only, so CI applies it. No backfill: every existing stop keeps
-- the rule it was set under.
ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS first_session DATE;
