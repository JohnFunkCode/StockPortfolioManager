"""Shared helper for turning exceptions into model/API-safe text.

Raw ``str(exc)`` on library exceptions (requests/urllib3 connection errors,
yfinance/Polygon HTTP errors, etc.) can carry invalid-UTF-8 byte sequences,
lone surrogates, or unbounded length (embedded response bodies, tracebacks).
Any of those can reach a downstream JSON-consuming API — e.g. as Anthropic
tool_result content in quantcore/services/chat.py — and trip its request
validation. Route exception text through this before it leaves the service
layer.
"""

MAX_ERROR_TEXT_LEN = 500


def safe_error_text(exc: BaseException | str, max_len: int = MAX_ERROR_TEXT_LEN) -> str:
    text = exc if isinstance(exc, str) else str(exc)
    text = text.encode("utf-8", "replace").decode("utf-8")
    if len(text) > max_len:
        text = text[:max_len] + "...(truncated)"
    return text
