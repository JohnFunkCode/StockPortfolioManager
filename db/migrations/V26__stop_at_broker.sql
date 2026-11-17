-- V26: a stop that mirrors an order placed at an outside broker (#431 phase 2
-- and #221, plan step 4.5 in docs/proposals/paper-books-realized-pnl-plan.md).
--
-- at_broker          true when the owner says the stop exists as an order at
--                    a broker, and QuantCore only records it. False for a
--                    stop that exists only here. Only a real book's stop can
--                    be true: the service refuses it on a paper book, which
--                    has no broker.
-- broker_quantity    the shares the broker's order is for, when the owner
--                    gave it. It may be fewer than the holding.
-- broker_order_type  stop, stop_limit or trailing_stop, when given. No CHECK
--                    on the list, for decision 45's reason: the service
--                    checks it, and a new one needs no hand-applied migration.
-- brokerage          the broker's name, as the owner typed it.
--
-- The three details belong to a broker's order, so a stop that isn't at a
-- broker carries none of them. QuantCore never reads the broker: what these
-- say is what the owner told it when the stop was set.
--
-- ADD COLUMN only, so CI applies it. Every existing stop takes false and no
-- details, so the rule holds for it.
ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS at_broker         BOOLEAN       NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS broker_quantity   NUMERIC(18,6) CHECK (broker_quantity > 0),
  ADD COLUMN IF NOT EXISTS broker_order_type TEXT;

ALTER TABLE stops
  ADD COLUMN IF NOT EXISTS brokerage TEXT
      CONSTRAINT stops_broker_details_rule
      CHECK (at_broker
             OR (broker_quantity IS NULL AND broker_order_type IS NULL
                 AND brokerage IS NULL));
