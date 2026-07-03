"""Chat sidekick tool schemas and UI-directive validation.

Pure data + pure functions — no service imports, no I/O, no Anthropic SDK.
The Anthropic tool list for the /api/chat agent loop lives here, alongside the
backend copy of the UI component registry that ``show_component`` directives
are validated against before they are streamed to the browser. The frontend
holds its own registry (frontend/src/chat/componentRegistry.tsx) and
re-validates independently; keep the two vocabularies in sync.
"""
from __future__ import annotations

# Component name -> allowed props. Every prop listed is required; extra props
# are strictly rejected so a hallucinated prop can never reach the browser.
BACKEND_COMPONENT_REGISTRY: dict[str, dict[str, type]] = {
    "signals": {"ticker": str},
    "live_price": {"ticker": str},
    "price_chart": {"ticker": str},
}


def validate_directive(component: str, props: object) -> tuple[bool, str]:
    """Validate a show_component call. Returns (ok, reason-if-rejected)."""
    spec = BACKEND_COMPONENT_REGISTRY.get(component)
    if spec is None:
        return False, (
            f"Unknown component '{component}'. "
            f"Valid components: {sorted(BACKEND_COMPONENT_REGISTRY)}"
        )
    if not isinstance(props, dict):
        return False, f"props must be an object, got {type(props).__name__}"
    extra = set(props) - set(spec)
    if extra:
        return False, f"Unexpected props {sorted(extra)} for '{component}'"
    for name, expected_type in spec.items():
        if name not in props:
            return False, f"Missing required prop '{name}' for '{component}'"
        value = props[name]
        if not isinstance(value, expected_type):
            return False, (
                f"Prop '{name}' must be {expected_type.__name__}, "
                f"got {type(value).__name__}"
            )
        if expected_type is str and not value.strip():
            return False, f"Prop '{name}' must be a non-empty string"
    return True, ""


_SYMBOL_PROP = {
    "type": "object",
    "properties": {"symbol": {"type": "string", "description": "Ticker symbol, e.g. 'INTC'"}},
    "required": ["symbol"],
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "get_stock_price",
        "description": (
            "Current stock price, Bollinger Bands (20-day, 2 std dev), and an "
            "options chain summary for a ticker symbol."
        ),
        "input_schema": _SYMBOL_PROP,
    },
    {
        "name": "get_technical_signals",
        "description": (
            "Synthesized technical signal summary for a ticker: trend, RSI, MACD, "
            "OBV, VWAP posture, and an overall interpretation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker symbol"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_rsi",
        "description": "Relative Strength Index for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "period": {"type": "integer", "description": "RSI period (default 14)"},
                "interval": {
                    "type": "string",
                    "description": "Bar interval: '1d' (default), '1wk', or '1mo'",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_macd",
        "description": "MACD (12/26/9) with crossover state for a ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "interval": {
                    "type": "string",
                    "description": "Bar interval: '1d' (default), '1wk', or '1mo'",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fundamental_score",
        "description": (
            "Composite fundamental score (-14 to +14) with per-metric breakdown: "
            "revenue CAGR, margins, FCF, valuation, momentum."
        ),
        "input_schema": _SYMBOL_PROP,
    },
    {
        "name": "get_news_sentiment",
        "description": (
            "Recent news articles and aggregate FinBERT sentiment signal for a ticker."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "days": {"type": "integer", "description": "Lookback window in days (default 7)"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "show_component",
        "description": (
            "Render a live, data-bound UI component inline in the conversation. "
            "Use this after discussing a ticker so the user can see the real data. "
            "'signals' shows the full technical/options/risk signal panel, "
            "'live_price' shows a compact auto-refreshing price chip, "
            "'price_chart' shows the price history chart with moving averages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "enum": sorted(BACKEND_COMPONENT_REGISTRY),
                    "description": "Which registered component to render",
                },
                "props": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Ticker symbol"}
                    },
                    "required": ["ticker"],
                },
            },
            "required": ["component", "props"],
        },
    },
]
