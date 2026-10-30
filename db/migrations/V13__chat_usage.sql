-- V13: per-user daily Sidekick token budget (G2, #461).
--
-- chat_usage holds one row per user per America/New_York day. `owner` is the
-- raw principal identity (IAP email or token sub), the same key user_settings
-- uses, so the cap a user sets and the usage counted against it match.
--
-- Admission is an atomic reservation (docs/proposals/rate-limiting-g2-plan.md
-- D3): before each model call one conditional upsert adds that call's worst
-- case to reserved_tokens only if input + output + reserved stays within the
-- budget; after the call the reservation is released and the actual usage
-- added. A reservation never reconciled (an instance died mid-call) stays
-- charged until the day rolls over, which errs toward stopping.
--
-- user_settings.chat_daily_token_budget is the user's own lower cap; NULL means
-- the server ceiling (SIDEKICK_DAILY_TOKEN_BUDGET).

CREATE TABLE IF NOT EXISTS chat_usage (
    owner           TEXT        NOT NULL,
    usage_day       DATE        NOT NULL,
    turns           INTEGER     NOT NULL DEFAULT 0,
    input_tokens    BIGINT      NOT NULL DEFAULT 0,
    output_tokens   BIGINT      NOT NULL DEFAULT 0,
    reserved_tokens BIGINT      NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, usage_day)
);

ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS chat_daily_token_budget INTEGER;
