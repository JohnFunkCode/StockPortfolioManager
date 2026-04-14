"""
Smoke test for Portfolio Monitor — run with the Auth Proxy on port 5433.

Tests _check_position() directly against a single position (AAPL) to verify:
  - All 7 health checks run without crashing
  - Result has expected keys
  - Alert types, if present, are valid

Usage (fish):
  set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
  source .venv/bin/activate.fish; and python scripts/test_portfolio_monitor.py

Usage (bash):
  export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
  source .venv/bin/activate && python scripts/test_portfolio_monitor.py
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PASS = os.environ.get("DB_PASS", "")
os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg2://app_user:{DB_PASS}@127.0.0.1:5433/stock_portfolio"
)

TENANT_ID = "7d3cc53d-a909-4574-bbf7-c3c02ee0940b"

VALID_ALERT_TYPES = {
    "at_risk",
    "drawdown_warning",
    "trend_degrading",
    "inst_exit",
    "squeeze_watch",
    "capitulation",
    "mm_bias_reversal",
}

from agents.portfolio_monitor.monitor import _check_position

test_position = {
    "symbol":         "AAPL",
    "purchase_price": 185.00,
    "quantity":       10,
}

print(f"Checking {test_position['symbol']}...")
result = _check_position(test_position)

print(f"\nSymbol:    {result['symbol']}")
print(f"Price:     ${result['price']:.2f}")
print(f"Cost basis: ${result['cost_basis']:.2f}")
print(f"Alerts ({len(result['alerts'])}):")
for a in result["alerts"]:
    print(f"  [{a['type']}] {a['detail']}")

assert "symbol"     in result, "Missing 'symbol'"
assert "price"      in result, "Missing 'price'"
assert "alerts"     in result, "Missing 'alerts'"
assert isinstance(result["alerts"], list), "'alerts' must be a list"

for alert in result["alerts"]:
    assert alert["type"] in VALID_ALERT_TYPES, f"Unknown alert type: {alert['type']}"
    assert "detail" in alert, f"Alert missing 'detail': {alert}"

print("\nAll assertions passed.")
