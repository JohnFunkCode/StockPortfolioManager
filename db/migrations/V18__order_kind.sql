-- V18 (#431, plan step 3.5): what kind of order a paper order is.
--
-- 'market' is every order so far: a buy or a sell that fills at a quote or at
-- its session's opening price, and moves cash.
-- 'seed' copies one of the real book's holdings into a new paper book. It is
-- priced by the same rule as a market buy and costs no cash: the lot it fills
-- has acquisition_type 'SEED', and only a 'BUY' lot writes a cash row.
-- 'manual_close' sells a whole position whose symbol can no longer be quoted,
-- at the last good price on record. Its price_source is NULL and its
-- quote_time is the time that price is from.
--
-- ADD COLUMN only, so CI applies it. The default marks every existing order
-- 'market', which is what each one is.
ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'market'
      CONSTRAINT orders_kind_rule
      CHECK (kind = 'market'
             OR (kind = 'seed' AND side = 'buy' AND requested_shares IS NOT NULL)
             OR (kind = 'manual_close' AND side = 'sell'));
