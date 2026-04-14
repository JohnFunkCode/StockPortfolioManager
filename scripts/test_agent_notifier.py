"""Smoke test for AgentNotifier — run with the Auth Proxy on port 5433."""
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PASS = os.environ.get("DB_PASS", "")
os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg2://app_user:{DB_PASS}@127.0.0.1:5433/stock_portfolio"
)

TENANT_ID = "7d3cc53d-a909-4574-bbf7-c3c02ee0940b"

from agents.agent_notifier import AgentNotifier

n = AgentNotifier(TENANT_ID)
print(f"Webhook URL     : {n.discord_webhook_url!r}")

suppressed_before = n._is_suppressed("NVDA", "signal_buy")
print(f"Suppressed before record : {suppressed_before}")
assert not suppressed_before, "Should not be suppressed before first fire"

n._record_fired("NVDA", "signal_buy")

suppressed_after = n._is_suppressed("NVDA", "signal_buy")
print(f"Suppressed after record  : {suppressed_after}")
assert suppressed_after, "Should be suppressed immediately after fire"

# send_signal_alert with no webhook set should log and not crash
n.send_signal_alert(
    symbol="NVDA",
    score=6,
    direction="buy",
    triggers=["RSI 28 oversold", "MACD bullish crossover", "VWAP reclaimed"],
)

print("All assertions passed.")
