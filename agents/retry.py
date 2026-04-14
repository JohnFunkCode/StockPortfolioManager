"""
Exponential backoff retry for tool calls.

Usage (functional):
    from agents.retry import with_retry
    data = with_retry(get_rsi, "AAPL")
    data = with_retry(get_macd, "AAPL", max_attempts=4, base_delay=2.0)

Usage (decorator):
    from agents.retry import retry

    @retry(max_attempts=3, base_delay=1.0)
    def fetch_something(symbol: str) -> dict: ...

Default behaviour:
  max_attempts  3
  base_delay    1.0 s
  backoff       2× per attempt (1 s → 2 s → 4 s)
  jitter        ±20% random jitter to avoid thundering-herd
  exceptions    Exception (catches everything)

The final exception is re-raised after all attempts are exhausted.
Errors are recorded via circuit_breaker.record_tool_error() so the
per-tool error rate breaker is automatically updated.
"""
import functools
import logging
import os
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

from agents.circuit_breaker import record_tool_error

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

_DEFAULT_MAX_ATTEMPTS = int(os.environ.get("RETRY_MAX_ATTEMPTS", "3"))
_DEFAULT_BASE_DELAY   = float(os.environ.get("RETRY_BASE_DELAY", "1.0"))
_DEFAULT_BACKOFF      = 2.0
_JITTER_FACTOR        = 0.20   # ±20%


def _jitter(delay: float) -> float:
    return delay * (1 + random.uniform(-_JITTER_FACTOR, _JITTER_FACTOR))


def with_retry(
    fn: Callable,
    *args,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    exceptions: tuple = (Exception,),
    tool_name: str | None = None,
    **kwargs,
) -> Any:
    """
    Call `fn(*args, **kwargs)` with exponential backoff.

    Args:
        fn:           Callable to invoke.
        *args:        Positional arguments forwarded to fn.
        max_attempts: Total attempts (including the first call).
        base_delay:   Seconds to wait before the second attempt.
        exceptions:   Exception types that trigger a retry.
        tool_name:    Name used for circuit_breaker error tracking.
                      Defaults to fn.__name__.
        **kwargs:     Keyword arguments forwarded to fn.
    """
    name = tool_name or getattr(fn, "__name__", str(fn))
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except exceptions as exc:
            last_exc = exc
            record_tool_error(name)
            if attempt == max_attempts:
                break
            delay = _jitter(base_delay * (_DEFAULT_BACKOFF ** (attempt - 1)))
            log.warning(
                "Retry %d/%d for %s after %.1fs: %s",
                attempt, max_attempts, name, delay, exc,
            )
            time.sleep(delay)

    raise last_exc  # type: ignore[misc]


def retry(
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    exceptions: tuple = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator version of with_retry.

    @retry(max_attempts=3, base_delay=1.0)
    def my_tool_call(symbol: str) -> dict: ...
    """
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return with_retry(
                fn, *args,
                max_attempts=max_attempts,
                base_delay=base_delay,
                exceptions=exceptions,
                **kwargs,
            )
        return wrapper  # type: ignore[return-value]
    return decorator
