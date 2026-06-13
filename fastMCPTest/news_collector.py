"""Transitional re-export shim — NewsCollector moved to quantcore.services.sentiment.

Kept so unmigrated importers (options_analysis.py, until Phase 1 Step 8) stay
green. Deleted in the Step 10 cleanup.
"""

from quantcore.services.sentiment import NewsCollector  # noqa: F401
