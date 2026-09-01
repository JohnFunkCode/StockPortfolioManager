# Market-date audit — issue #211

Source issue: [#211](https://github.com/JohnFunkCode/StockPortfolioManager/issues/211)

## Decision

Production date arithmetic in `quantcore/` uses
`quantcore.analytics.market_time.market_date()` for US market calendar dates.
`latest_completed_session()` remains reserved for data freshness decisions where
the current session must already have started. UTC timestamps continue to use
`datetime.now(timezone.utc)`.

The twelve audited `date.today()` sites are date-only calculations for option
expiry/DTE, earnings windows, holdings age, NAV age, or historical backfill
windows. They now use `market_date()`. The earnings dates returned by yfinance
are treated as date-only exchange-calendar labels; they are not converted as if
they were UTC instants.

Affected production modules:

- `quantcore/repositories/options_position_repository.py`
- `quantcore/services/options_screening.py`
- `quantcore/services/options.py`
- `quantcore/services/fundamentals.py`
- `quantcore/services/arbitrage.py`

Affected methods accept an optional `now` clock where date-sensitive behavior
needs deterministic coverage. Existing callers retain their default behavior.

## Checkpoint — 2026-09-01

- Replaced all twelve executable `date.today()` call sites under `quantcore/`.
- Propagated the injectable clock through options flow, arbitrage scan/NAV, and
  fundamentals surfaces.
- Added the architecture guard in `tests/test_architecture_guards.py` so new
  direct `today()` calls in `quantcore/` fail CI.
- Added 23:35 ET boundary coverage for options DTE, earnings proximity and
  upcoming earnings, arbitrage holdings age, and option-position expiry.
- Focused non-database suites pass: 184 tests.
- Repository integration tests remain to be run in an environment with the test
  PostgreSQL service available; the sandbox could not connect to `127.0.0.1:5434`.
