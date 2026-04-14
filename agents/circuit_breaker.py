"""
Circuit breakers for the Agentic Market Intelligence System.

Two independent breakers:

1. MarketHoursBreaker
   Raises MarketClosedError when called outside regular US equity market hours
   (9:30 AM – 4:00 PM ET, Monday–Friday).  The Signal Scanner and Portfolio
   Monitor check this before running so Cloud Scheduler jobs that fire slightly
   early or late don't waste resources on stale data.

   A configurable PRE_RUN_MINUTES buffer (default 5) allows jobs to start
   up to 5 minutes before the official open (9:25 AM) so Cloud Run cold-start
   latency doesn't cause a missed scan.

2. ToolErrorRateBreaker
   Tracks per-tool error counts in a rolling 5-minute window.  When a tool
   exceeds ERROR_THRESHOLD errors in that window, calls to that tool are
   blocked with ToolErrorRateExceeded for COOLDOWN_SECONDS.

   This prevents a degraded yfinance endpoint (rate-limited or temporarily
   down) from flooding logs and slowing every agent run.

Environment variables:
  CIRCUIT_BREAKER_ENABLED   — set to "false" to disable both breakers (e.g. tests)
  MARKET_PRE_RUN_MINUTES    — minutes before open that jobs are allowed (default 5)
  TOOL_ERROR_THRESHOLD      — max errors per 5-min window before tripping (default 5)
  TOOL_COOLDOWN_SECONDS     — how long the tool breaker stays open (default 120)
"""
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

_ENABLED          = os.environ.get("CIRCUIT_BREAKER_ENABLED", "true").lower() not in ("false", "0", "no")
_PRE_RUN_MINUTES  = int(os.environ.get("MARKET_PRE_RUN_MINUTES", "5"))
_ERROR_THRESHOLD  = int(os.environ.get("TOOL_ERROR_THRESHOLD", "5"))
_COOLDOWN_SECONDS = int(os.environ.get("TOOL_COOLDOWN_SECONDS", "120"))
_ERROR_WINDOW     = 300   # 5-minute rolling window in seconds

ET = ZoneInfo("America/New_York")

MARKET_OPEN  = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)


class MarketClosedError(RuntimeError):
    """Raised when an operation is attempted outside market hours."""


class ToolErrorRateExceeded(RuntimeError):
    """Raised when a tool's error rate has tripped its circuit breaker."""


# ---------------------------------------------------------------------------
# Market hours breaker
# ---------------------------------------------------------------------------

def is_market_open(*, include_pre_run: bool = True) -> bool:
    """
    Return True if the US equity market is currently open (or in pre-run window).

    Args:
        include_pre_run: If True, allow up to PRE_RUN_MINUTES before official
                         market open (9:30 AM ET). Default True.
    """
    if not _ENABLED:
        return True

    now_et   = datetime.now(ET)
    weekday  = now_et.weekday()   # 0 = Monday, 6 = Sunday

    # Weekends
    if weekday >= 5:
        return False

    now_time = now_et.time()

    if include_pre_run:
        # Allow from (MARKET_OPEN - PRE_RUN_MINUTES) to MARKET_CLOSE
        from datetime import timedelta
        open_dt  = datetime.combine(now_et.date(), MARKET_OPEN, tzinfo=ET)
        early_dt = open_dt - timedelta(minutes=_PRE_RUN_MINUTES)
        early_t  = early_dt.time()
        return early_t <= now_time < MARKET_CLOSE

    return MARKET_OPEN <= now_time < MARKET_CLOSE


def require_market_open(*, include_pre_run: bool = True) -> None:
    """
    Raise MarketClosedError if the market is currently closed.

    Call this at the top of any agent run function that should only execute
    during trading hours.
    """
    if not _ENABLED:
        return

    now_et = datetime.now(ET)
    if not is_market_open(include_pre_run=include_pre_run):
        raise MarketClosedError(
            f"Market is closed at {now_et.strftime('%Y-%m-%d %H:%M ET')}. "
            "Agents only run Mon–Fri 09:25–16:00 ET."
        )


# ---------------------------------------------------------------------------
# Tool error rate breaker
# ---------------------------------------------------------------------------

_lock         = threading.Lock()
_error_times: dict[str, deque] = defaultdict(deque)   # tool → deque of error timestamps
_tripped_at:  dict[str, float] = {}                    # tool → monotonic time when tripped


def record_tool_error(tool_name: str) -> None:
    """
    Record one error for `tool_name`.

    If the error count in the rolling window exceeds ERROR_THRESHOLD, the
    breaker is tripped and subsequent calls to `check_tool_allowed()` for
    that tool will raise ToolErrorRateExceeded for COOLDOWN_SECONDS.
    """
    if not _ENABLED:
        return

    now = time.monotonic()
    with _lock:
        q = _error_times[tool_name]
        q.append(now)
        # Evict entries outside the rolling window
        cutoff = now - _ERROR_WINDOW
        while q and q[0] < cutoff:
            q.popleft()

        if len(q) >= _ERROR_THRESHOLD and tool_name not in _tripped_at:
            _tripped_at[tool_name] = now


def check_tool_allowed(tool_name: str) -> None:
    """
    Raise ToolErrorRateExceeded if `tool_name`'s breaker is currently open.

    Call this before invoking a tool that might be flaky.
    """
    if not _ENABLED:
        return

    now = time.monotonic()
    with _lock:
        tripped = _tripped_at.get(tool_name)
        if tripped is None:
            return
        if now - tripped >= _COOLDOWN_SECONDS:
            # Cooldown elapsed — reset breaker
            del _tripped_at[tool_name]
            _error_times[tool_name].clear()
            return
        remaining = int(_COOLDOWN_SECONDS - (now - tripped))
        raise ToolErrorRateExceeded(
            f"Tool '{tool_name}' error breaker is open — "
            f"too many errors in the last {_ERROR_WINDOW}s. "
            f"Auto-resets in {remaining}s."
        )


def reset_tool_breaker(tool_name: str) -> None:
    """Manually reset the breaker for a tool (useful in tests)."""
    with _lock:
        _tripped_at.pop(tool_name, None)
        _error_times[tool_name].clear()


def breaker_status() -> dict:
    """Return current breaker state for all tools (for health endpoint)."""
    now = time.monotonic()
    status = {}
    with _lock:
        for tool, tripped in list(_tripped_at.items()):
            remaining = max(0, int(_COOLDOWN_SECONDS - (now - tripped)))
            status[tool] = {"state": "open", "resets_in_seconds": remaining}
        for tool, q in _error_times.items():
            if tool not in _tripped_at:
                status.setdefault(tool, {"state": "closed", "errors_in_window": len(q)})
    return status
