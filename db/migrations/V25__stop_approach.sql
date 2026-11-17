-- V25: what the bar-close checker records when a holding closes near its stop
-- (#431 phase 2 and #221, plan step 4.4 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- approached_session   the session of the newest closed bar whose close was
--                      within the approach distance of the stop's level
--                      (STOP_APPROACH_ATR_MULTIPLE times a 14-session ATR).
--                      NULL while the close is further away than that, so a
--                      holding that moves away and comes back is announced
--                      again. A check that can't judge it leaves it as it was.
-- approach_close       that bar's close
-- approach_atr         the ATR the distance was measured in, as of that bar
-- approach_alerted_at  when the approach alert for a real-book stop went out.
--                      NULL means it is still owed while approached_session
--                      is set. Cleared with approached_session, so a stop that
--                      stays close is announced once.
--
-- ADD COLUMN only, so CI applies it. Every existing row has all four NULL,
-- so both rules hold for it. Each side of each rule is an IS NULL test,
-- which is never NULL, so a CHECK can't pass on an unknown.
ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS approached_session  DATE,
  ADD COLUMN IF NOT EXISTS approach_close      NUMERIC(18,6),
  ADD COLUMN IF NOT EXISTS approach_atr        NUMERIC(18,6);

ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS approach_alerted_at TIMESTAMPTZ
      CONSTRAINT stops_approach_rule
      CHECK ((approached_session IS NULL) = (approach_close IS NULL)
             AND (approached_session IS NULL) = (approach_atr IS NULL)
             AND (approach_alerted_at IS NULL OR approached_session IS NOT NULL));
