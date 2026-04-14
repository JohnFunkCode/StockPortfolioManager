"""
Smoke test for Deep Analysis Agent — run with the Auth Proxy on port 5433.

Runs the full 6-phase pipeline against AAPL and verifies:
  - Recommendation is one of BUY / SELL / HOLD / AVOID
  - Conviction is HIGH / MEDIUM / LOW
  - All 6 phase score keys are present in details
  - No unhandled exceptions from any phase

NOTE: This test calls real market data APIs and takes 20–60 seconds.

Usage (fish):
  set -x DB_PASS (gcloud secrets versions access latest --secret=db-app-user-password)
  set -x PUBSUB_ENABLED false
  source .venv/bin/activate.fish; and python scripts/test_deep_analysis.py

Usage (bash):
  export DB_PASS=$(gcloud secrets versions access latest --secret=db-app-user-password)
  export PUBSUB_ENABLED=false
  source .venv/bin/activate && python scripts/test_deep_analysis.py
"""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PASS = os.environ.get("DB_PASS", "")
os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg2://app_user:{DB_PASS}@127.0.0.1:5433/stock_portfolio"
)
os.environ.setdefault("PUBSUB_ENABLED", "false")

TENANT_ID   = "7d3cc53d-a909-4574-bbf7-c3c02ee0940b"
TEST_SYMBOL = "AAPL"

VALID_RECS  = {"BUY", "SELL", "HOLD", "AVOID"}
VALID_CONVS = {"HIGH", "MEDIUM", "LOW"}

from agents.deep_analysis.analyzer import analyze

print(f"Running deep analysis for {TEST_SYMBOL} (this may take 30–60s)...")
t0 = time.time()
result = analyze(TENANT_ID, TEST_SYMBOL, source="smoke_test")
elapsed = round(time.time() - t0, 1)

print(f"\nCompleted in {elapsed}s")
print(f"Symbol:         {result['symbol']}")
print(f"Score:          {result['score']:+d}")
print(f"Recommendation: {result['recommendation']}")
print(f"Conviction:     {result['conviction']}")
print(f"Entry:          ${result['entry_low']} – ${result['entry_high']}")
print(f"Target:         ${result['price_target']}")
print(f"Stop:           ${result['stop_loss']}")
print(f"\nPhase scores:   {result['details']['phase_scores']}")
print(f"\nBull case ({len(result['details']['bull_case'])} points):")
for b in result["details"]["bull_case"]:
    print(f"  + {b}")
print(f"\nBear case ({len(result['details']['bear_case'])} points):")
for b in result["details"]["bear_case"]:
    print(f"  - {b}")
if result["details"].get("options_play"):
    print(f"\nOptions play: {result['details']['options_play']}")

# Assertions
assert result["recommendation"] in VALID_RECS, f"Bad recommendation: {result['recommendation']}"
assert result["conviction"] in VALID_CONVS, f"Bad conviction: {result['conviction']}"
expected_phases = {"price_structure", "momentum", "volume_inst", "options_intel", "market_structure", "risk_sentiment"}
assert set(result["details"]["phase_scores"].keys()) == expected_phases, "Missing phase score keys"
assert isinstance(result["details"]["bull_case"], list), "bull_case must be a list"
assert isinstance(result["details"]["bear_case"], list), "bear_case must be a list"

print("\nAll assertions passed.")
