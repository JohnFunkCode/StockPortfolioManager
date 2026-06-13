"""Backward-compatibility shim — logic moved to quantcore.services.options_contracts.

Relocated in Phase 1 Step 5. This re-export keeps legacy importers
(test_options_contract_tools.py, options_analysis.py until Step 8) green.
Deleted in Step 10 cleanup once all importers point at the new path.
"""

from quantcore.services.options_contracts import (  # noqa: F401
    VALID_KINDS,
    fetch_and_store_full_chain,
    get_option_contracts_data,
    price_vertical_spread_data,
)
