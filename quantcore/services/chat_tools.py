"""Chat sidekick tool schemas and UI-directive validation.

Pure data + pure functions — no service imports, no I/O, no Anthropic SDK.
The Anthropic tool list for the /api/chat agent loop lives here, alongside the
backend copy of the UI component registry that ``show_component`` directives
are validated against before they are streamed to the browser. The frontend
holds its own registry (frontend/src/chat/componentRegistry.tsx) and
re-validates independently; keep the two vocabularies in sync.
"""
from __future__ import annotations

# Component name -> allowed props (value = accepted type or tuple of types).
# Every prop listed is required; extra props are strictly rejected so a
# hallucinated prop can never reach the browser.
_NUMBER = (int, float)

BACKEND_COMPONENT_REGISTRY: dict[str, dict[str, object]] = {
    "signals": {"ticker": str},
    "live_price": {"ticker": str},
    "price_chart": {"ticker": str},
    "spread_payoff": {
        "ticker": str,
        "expiration": str,
        "long_strike": _NUMBER,
        "short_strike": _NUMBER,
        "kind": str,
    },
}


# Component name -> action name -> payload spec (same typing convention as
# prop specs). Components absent here are display-only. This is the UI->model
# backchannel vocabulary: the frontend registry declares the same actions and
# both sides validate (defense in depth, mirroring the directive registries).
BACKEND_INTERACTION_REGISTRY: dict[str, dict[str, dict[str, object]]] = {
    "spread_payoff": {
        "select_strike": {"strike": _NUMBER},
        "reprice_leg": {"leg": str, "strike": _NUMBER},
    },
    "price_chart": {
        "select_point": {"date": str, "price": _NUMBER},
    },
}


def _type_name(expected) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def _check_fields(spec: dict, values: dict, label: str, owner: str) -> tuple[bool, str]:
    """Strict field check shared by directives and interactions: every spec
    field required, extras rejected, bool never passes as a number."""
    extra = set(values) - set(spec)
    if extra:
        return False, f"Unexpected {label}s {sorted(extra)} for '{owner}'"
    for name, expected_type in spec.items():
        if name not in values:
            return False, f"Missing required {label} '{name}' for '{owner}'"
        value = values[name]
        # bool is an int subclass — never a valid stand-in for a number.
        if isinstance(value, bool) and expected_type is not bool:
            return False, (
                f"{label.capitalize()} '{name}' must be "
                f"{_type_name(expected_type)}, got bool"
            )
        if not isinstance(value, expected_type):
            return False, (
                f"{label.capitalize()} '{name}' must be {_type_name(expected_type)}, "
                f"got {type(value).__name__}"
            )
        if expected_type is str and not value.strip():
            return False, f"{label.capitalize()} '{name}' must be a non-empty string"
    return True, ""


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
    return _check_fields(spec, props, "prop", component)


def validate_interaction(interaction: object) -> tuple[bool, str]:
    """Validate one UI interaction from the request body before it is folded
    into the model conversation. Returns (ok, reason-if-rejected)."""
    if not isinstance(interaction, dict):
        return False, f"interaction must be an object, got {type(interaction).__name__}"
    component_id = interaction.get("component_id")
    if not isinstance(component_id, str) or not component_id.strip():
        return False, "interaction requires a non-empty 'component_id'"
    component = interaction.get("component", "")
    actions = BACKEND_INTERACTION_REGISTRY.get(component)
    if actions is None:
        return False, (
            f"Component '{component}' accepts no interactions. "
            f"Interactive components: {sorted(BACKEND_INTERACTION_REGISTRY)}"
        )
    action = interaction.get("action", "")
    payload_spec = actions.get(action)
    if payload_spec is None:
        return False, (
            f"Unknown action '{action}' for '{component}'. "
            f"Valid actions: {sorted(actions)}"
        )
    payload = interaction.get("payload", {})
    if not isinstance(payload, dict):
        return False, f"payload must be an object, got {type(payload).__name__}"
    ok, reason = _check_fields(payload_spec, payload, "payload field", action)
    if not ok:
        return False, reason
    props = interaction.get("props")
    if props is not None:
        ok, reason = validate_directive(component, props)
        if not ok:
            return False, f"interaction props snapshot invalid: {reason}"
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
        "name": "price_vertical_spread",
        "description": (
            "Price a two-leg vertical option spread from real contracts: returns "
            "conservative and mid debit, max profit, max loss, breakeven, "
            "risk/reward, per-leg bid/ask/IV, and liquidity warnings. A bull call "
            "spread is long the lower strike; a bear call spread is long the "
            "higher strike; use kind='put' for put spreads. After pricing, render "
            "it with show_component('spread_payoff', {same parameters})."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "expiration": {
                    "type": "string",
                    "description": "Expiration date, YYYY-MM-DD",
                },
                "long_strike": {"type": "number", "description": "Strike you buy"},
                "short_strike": {"type": "number", "description": "Strike you sell"},
                "kind": {
                    "type": "string",
                    "enum": ["call", "put"],
                    "description": "Option type for both legs (default call)",
                },
            },
            "required": ["symbol", "expiration", "long_strike", "short_strike"],
        },
    },
    {
        "name": "show_component",
        "description": (
            "Render a live, data-bound UI component inline in the conversation. "
            "Use this after discussing a ticker so the user can see the real data. "
            "'signals' shows the full technical/options/risk signal panel, "
            "'live_price' shows a compact auto-refreshing price chip, "
            "'price_chart' shows the price history chart with moving averages, "
            "'spread_payoff' shows an interactive risk graph for a vertical "
            "spread (requires ticker, expiration, long_strike, short_strike, "
            "kind — the same parameters you passed to price_vertical_spread)."
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
                        "ticker": {"type": "string", "description": "Ticker symbol"},
                        "expiration": {
                            "type": "string",
                            "description": "spread_payoff only: expiration YYYY-MM-DD",
                        },
                        "long_strike": {
                            "type": "number",
                            "description": "spread_payoff only: strike you buy",
                        },
                        "short_strike": {
                            "type": "number",
                            "description": "spread_payoff only: strike you sell",
                        },
                        "kind": {
                            "type": "string",
                            "enum": ["call", "put"],
                            "description": "spread_payoff only: option type",
                        },
                    },
                    "required": ["ticker"],
                },
            },
            "required": ["component", "props"],
        },
    },
]
