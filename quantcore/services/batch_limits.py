"""Shared validation for bounded multi-symbol requests."""

from __future__ import annotations


MAX_FUNDAMENTAL_BATCH_SYMBOLS = 25


def normalize_symbol_batch(
    symbols: list[str], *, max_symbols: int = MAX_FUNDAMENTAL_BATCH_SYMBOLS
) -> list[str]:
    """Trim, uppercase, deduplicate, and bound a symbol batch."""
    if not isinstance(symbols, list):
        raise ValueError("symbols must be a list")

    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        if not isinstance(symbol, str):
            raise ValueError("symbols must contain strings")
        symbol = symbol.strip().upper()
        if symbol and symbol not in seen:
            normalized.append(symbol)
            seen.add(symbol)

    if not normalized:
        raise ValueError("symbols must contain at least one non-blank ticker")
    if len(normalized) > max_symbols:
        raise ValueError(
            f"symbols is limited to {max_symbols} tickers per request "
            f"(got {len(normalized)})"
        )
    return normalized
