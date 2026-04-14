"""
Smoke test for the Signal Scanner — run with the Auth Proxy on port 5433.

Tests scan_symbol() directly against a real symbol (AAPL) to verify:
  - All 6 indicator scorers run without error
  - Total score is within the -9 to +9 range
  - Indicator breakdown is present for each key

Usage (fish):
  set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
  source .venv/bin/activate.fish; and python scripts/test_signal_scanner.py

Usage (bash):
  export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
  source .venv/bin/activate && python scripts/test_signal_scanner.py
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
TEST_SYMBOL = "AAPL"

from agents.signal_scanner.scanner import scan_symbol

print(f"Scanning {TEST_SYMBOL} for tenant {TENANT_ID[:8]}...")
result = scan_symbol(TENANT_ID, TEST_SYMBOL)

print(f"\nSymbol:    {result['symbol']}")
print(f"Score:     {result['score']:+d} / 9")
print(f"Direction: {result['direction']}")
print(f"Threshold: {result['threshold']}")
print(f"Triggered: {result['triggered']}")
print("\nIndicator breakdown:")
for name, data in result["indicators"].items():
    score = data.get("score", "?")
    print(f"  {name:<16} score={score:+d}  {data}")

assert -9 <= result["score"] <= 9, f"Score {result['score']} out of range"
assert result["direction"] in ("buy", "sell"), f"Invalid direction: {result['direction']}"
assert set(result["indicators"].keys()) == {
    "rsi", "macd", "vwap", "bollinger", "unusual_calls", "daoi"
}, "Missing indicator keys"

print("\nAll assertions passed.")
