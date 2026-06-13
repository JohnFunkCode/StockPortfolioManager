"""Repositories — SQL persistence only, no analytics (architectural standard v2 §5.1).

Each module owns the SQL for one domain and talks to the unified QuantCore
PostgreSQL database via quantcore.db.get_connection(). Import the module you
need directly (e.g. ``from quantcore.repositories.options_repository import
OptionsStore``); this package intentionally re-exports nothing so that heavy
dependencies (pandas, yfinance) load only when actually used.
"""
