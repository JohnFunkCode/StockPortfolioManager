-- V15: the sale-side FX rate (#235, plan step 2.3 in
-- docs/proposals/paper-books-realized-pnl-plan.md).
--
-- Realized P/L is reported in USD: cost basis at the rate on the lot's
-- trade_date, proceeds at the rate on the sale's sale_trade_date.
-- positions.fx_rate_at_purchase has existed since V4 (and was never written);
-- lot_sales had nowhere to keep the other side.
--
-- The rate is USD per one unit of the lot's currency, captured when the sale
-- is recorded so the realized read makes no network call. NULL means "no rate
-- recorded": a USD sale needs none, and a non-USD sale without one is flagged
-- and left out of the totals, never converted at 1.0.
--
-- Existing non-USD rows are filled by scripts/backfill_fx_rates.py, not here:
-- the rates come from a network lookup, which a migration must not make.

ALTER TABLE lot_sales
    ADD COLUMN IF NOT EXISTS fx_rate_at_sale NUMERIC(18,8);
