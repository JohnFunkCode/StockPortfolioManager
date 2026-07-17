"""
Shared exact-options lookup and vertical-spread pricing helpers.

Relocated from fastMCPTest/options_contract_tools.py during Phase 1 Step 5 so
the services layer owns its own dependencies. The MCP/CLI surfaces expose these
as tools, but the business logic lives here (OptionsService delegates to it).

``_safe_int`` and the full-chain side summary now come from
quantcore.analytics.options_math (single home — see that module), and live
yfinance access goes through the injected/default YFinanceGateway rather than a
direct yfinance import.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from quantcore.analytics.options_math import (
    RISK_FREE_RATE,
    chain_side_full,
    safe_int as _safe_int,
    vertical_spread_curves,
)
from quantcore.gateways.yfinance_gateway import YFinanceGateway
from quantcore.repositories.options_repository import OptionsStore


VALID_KINDS = {"call", "put"}


def _years_to_expiration(expiration: str) -> float:
    """Year fraction to the ~4pm-ET close of the expiration date — the same
    convention the payoff card has always used for its value-today curve."""
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").replace(
            hour=21, tzinfo=timezone.utc
        )
    except ValueError:
        return 0.0
    seconds = (exp - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds / (365.0 * 86_400.0))


def _parse_ts(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _snapshot_age_minutes(snapshot: Optional[dict]) -> Optional[float]:
    if not snapshot:
        return None
    captured_at = _parse_ts(snapshot.get("captured_at"))
    if captured_at is None:
        return None
    return (datetime.now(timezone.utc) - captured_at).total_seconds() / 60.0


def _normalize_contract(row: dict, symbol: str, expiration: str, price: float | None) -> dict:
    strike = round(float(row.get("strike", 0.0)), 2)
    bid = float(row.get("bid") or 0.0)
    ask = float(row.get("ask") or 0.0)
    last = row.get("last_price", row.get("last", 0.0))
    iv = row.get("implied_vol", row.get("iv", 0.0))
    volume = _safe_int(row.get("volume"))
    oi = _safe_int(row.get("open_interest"))
    mid = round((bid + ask) / 2.0, 2) if bid > 0 and ask > 0 else None
    spread_pct = None
    if mid and mid > 0:
        spread_pct = round((ask - bid) / mid * 100.0, 2)

    return {
        "symbol": symbol.upper(),
        "expiration": expiration,
        "kind": row.get("kind"),
        "strike": strike,
        "last": round(float(last or 0.0), 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "mid": mid,
        "iv": round(float(iv or 0.0), 2),
        "volume": volume,
        "open_interest": oi,
        "in_the_money": bool(row.get("in_the_money", False)),
        "bid_ask_spread_pct": spread_pct,
    }


def _contracts_from_snapshot(
    snapshot: dict,
    symbol: str,
    expirations: list[str],
    strikes: list[float],
    kind: str,
) -> tuple[list[dict], list[dict]]:
    price = snapshot.get("price")
    expiration_set = {str(exp) for exp in expirations}
    strike_set = {round(float(strike), 2) for strike in strikes}
    found: dict[tuple[str, float], dict] = {}

    for exp in snapshot.get("expirations", []):
        exp_date = exp.get("expiration")
        if exp_date not in expiration_set:
            continue
        for contract in exp.get("contracts", []):
            contract_kind = contract.get("kind")
            strike = round(float(contract.get("strike", 0.0)), 2)
            if contract_kind == kind and strike in strike_set:
                found[(exp_date, strike)] = _normalize_contract(
                    contract, symbol, exp_date, price
                )

    missing = [
        {"expiration": exp, "strike": strike, "kind": kind}
        for exp in expirations
        for strike in strike_set
        if (exp, strike) not in found
    ]
    contracts = [found[key] for key in sorted(found)]
    return contracts, missing


def fetch_and_store_full_chain(symbol: str, store: OptionsStore, gateway=None) -> dict:
    """Fetch the live full chain from yfinance and persist it."""
    gw = gateway or YFinanceGateway()
    info = gw.fast_info(symbol.upper())
    price = info.last_price
    if price is None:
        raise ValueError(f"Could not retrieve price for symbol: {symbol}")

    expirations_data = []
    total_contracts = 0
    for exp_date in gw.expirations(symbol.upper()):
        try:
            chain = gw.option_chain(symbol.upper(), exp_date)
        except Exception:
            continue

        calls = chain_side_full(chain.calls, iv_decimals=2)
        puts = chain_side_full(chain.puts, iv_decimals=2)
        total_call_oi = calls["total_open_interest"]
        total_put_oi = puts["total_open_interest"]
        put_call_ratio = round(total_put_oi / total_call_oi, 2) if total_call_oi else None
        expirations_data.append({
            "expiration": exp_date,
            "put_call_ratio": put_call_ratio,
            "calls": calls,
            "puts": puts,
        })
        total_contracts += len(calls["contracts"]) + len(puts["contracts"])

    snapshot_id = store.save_full_chain(
        symbol=symbol.upper(),
        price=float(price),
        bollinger_bands=None,
        expirations_data=expirations_data,
    )
    snapshot = store.get_full_chain(symbol.upper())
    return {
        "snapshot": snapshot,
        "snapshot_id": snapshot_id,
        "total_contracts": total_contracts,
        "expiration_count": len(expirations_data),
    }


def get_option_contracts_data(
    symbol: str,
    expirations: list[str],
    strikes: list[float],
    kind: str = "call",
    max_snapshot_age_minutes: int = 15,
    allow_live_fetch: bool = True,
    store: Optional[OptionsStore] = None,
    live_fetcher: Optional[Callable[[str, OptionsStore], dict]] = None,
) -> dict:
    """Return exact contracts by expiration and strike, using cache then live fetch."""
    symbol = symbol.upper()
    kind = kind.lower()
    if kind not in VALID_KINDS:
        raise ValueError("kind must be 'call' or 'put'")
    if not expirations:
        raise ValueError("at least one expiration is required")
    if not strikes:
        raise ValueError("at least one strike is required")

    store = store or OptionsStore()
    live_fetcher = live_fetcher or fetch_and_store_full_chain
    warnings: list[str] = []
    storage_status: dict = {}

    snapshot = store.get_full_chain(symbol)
    age = _snapshot_age_minutes(snapshot)
    cache_fresh = age is not None and age <= max_snapshot_age_minutes
    cache_available = snapshot is not None

    if snapshot and cache_fresh:
        contracts, missing = _contracts_from_snapshot(
            snapshot, symbol, expirations, strikes, kind
        )
        if not missing:
            return {
                "symbol": symbol,
                "price": round(float(snapshot.get("price", 0.0)), 2),
                "source": "cache",
                "captured_at": snapshot.get("captured_at"),
                "snapshot_age_minutes": round(age, 2) if age is not None else None,
                "contracts": contracts,
                "missing": [],
                "storage_status": {"cache_hit": True},
                "warnings": warnings,
            }
        warnings.append("Fresh cache did not contain every requested contract.")
    elif snapshot:
        warnings.append("Cached full-chain snapshot is stale.")
    else:
        warnings.append("No cached full-chain snapshot found.")

    if allow_live_fetch:
        fetched = live_fetcher(symbol, store)
        snapshot = fetched.get("snapshot")
        storage_status = {
            "cache_hit": False,
            "live_fetch_attempted": True,
            "snapshot_id": fetched.get("snapshot_id"),
            "expiration_count": fetched.get("expiration_count"),
            "total_contracts": fetched.get("total_contracts"),
            "persisted": fetched.get("snapshot_id") is not None,
        }
        age = _snapshot_age_minutes(snapshot)
    else:
        storage_status = {
            "cache_hit": bool(cache_available and cache_fresh),
            "cache_stale": bool(cache_available and not cache_fresh),
            "live_fetch_attempted": False,
        }

    contracts, missing = ([], [])
    if snapshot:
        contracts, missing = _contracts_from_snapshot(
            snapshot, symbol, expirations, strikes, kind
        )

    if missing:
        warnings.append("One or more requested contracts were not found.")

    return {
        "symbol": symbol,
        "price": round(float(snapshot.get("price", 0.0)), 2) if snapshot else None,
        "source": "live" if storage_status.get("live_fetch_attempted") else "cache",
        "captured_at": snapshot.get("captured_at") if snapshot else None,
        "snapshot_age_minutes": round(age, 2) if age is not None else None,
        "contracts": contracts,
        "missing": missing,
        "storage_status": storage_status,
        "warnings": warnings,
    }


def _liquidity_label(legs: list[dict]) -> str:
    if any(leg["bid"] <= 0 or leg["ask"] <= 0 or leg["ask"] < leg["bid"] for leg in legs):
        return "bad_quote"
    worst_spread = max(leg.get("bid_ask_spread_pct") or 999.0 for leg in legs)
    min_oi = min(leg.get("open_interest") or 0 for leg in legs)
    min_volume = min(leg.get("volume") or 0 for leg in legs)
    if worst_spread <= 10 and min_oi >= 100 and min_volume >= 25:
        return "good"
    if worst_spread <= 25 and min_oi >= 25:
        return "acceptable"
    return "thin"


def price_vertical_spread_data(
    symbol: str,
    expiration: str,
    long_strike: float,
    short_strike: float,
    kind: str = "call",
    max_snapshot_age_minutes: int = 15,
    allow_live_fetch: bool = True,
    store: Optional[OptionsStore] = None,
    live_fetcher: Optional[Callable[[str, OptionsStore], dict]] = None,
    include_curves: bool = False,
) -> dict:
    """Price a two-leg debit vertical spread from exact option contracts.

    ``include_curves`` is opt-in (the UI asks for it) so LLM-facing tool
    results never carry hundreds of chart samples.
    """
    kind = kind.lower()
    if kind not in VALID_KINDS:
        raise ValueError("kind must be 'call' or 'put'")
    if float(long_strike) == float(short_strike):
        raise ValueError("long_strike and short_strike must differ")

    lookup = get_option_contracts_data(
        symbol=symbol,
        expirations=[expiration],
        strikes=[float(long_strike), float(short_strike)],
        kind=kind,
        max_snapshot_age_minutes=max_snapshot_age_minutes,
        allow_live_fetch=allow_live_fetch,
        store=store,
        live_fetcher=live_fetcher,
    )
    warnings = list(lookup.get("warnings", []))
    by_strike = {round(c["strike"], 2): c for c in lookup.get("contracts", [])}
    long_leg = by_strike.get(round(float(long_strike), 2))
    short_leg = by_strike.get(round(float(short_strike), 2))

    result = {
        "symbol": lookup["symbol"],
        "expiration": expiration,
        "kind": kind,
        "price": lookup.get("price"),
        "source": lookup.get("source"),
        "captured_at": lookup.get("captured_at"),
        "snapshot_age_minutes": lookup.get("snapshot_age_minutes"),
        "storage_status": lookup.get("storage_status"),
        "missing": lookup.get("missing", []),
        "warnings": warnings,
    }

    if not long_leg or not short_leg:
        result.update({
            "strategy": None,
            "legs": {"long": long_leg, "short": short_leg},
            "liquidity": "bad_quote",
        })
        return result

    width = abs(float(short_strike) - float(long_strike))
    debit = round(long_leg["ask"] - short_leg["bid"], 2)
    mid_debit = None
    if long_leg.get("mid") is not None and short_leg.get("mid") is not None:
        mid_debit = round(long_leg["mid"] - short_leg["mid"], 2)

    if kind == "call":
        strategy = "bull_call_spread" if long_strike < short_strike else "bear_call_debit_spread"
        breakeven = round(float(long_strike) + debit, 2)
    else:
        strategy = "bear_put_spread" if long_strike > short_strike else "bull_put_debit_spread"
        breakeven = round(float(long_strike) - debit, 2)

    max_profit = round(width - debit, 2)
    max_loss = debit
    if debit <= 0:
        warnings.append("Non-positive debit from bid/ask quotes; spread is not cleanly priceable.")
    if max_profit <= 0:
        warnings.append("Debit is greater than or equal to spread width; risk/reward is invalid.")

    risk_reward = None
    if max_loss > 0 and max_profit > 0:
        risk_reward = round(max_profit / max_loss, 2)

    liquidity = _liquidity_label([long_leg, short_leg])
    if liquidity in {"thin", "bad_quote"}:
        warnings.append(f"Liquidity is {liquidity}.")

    result.update({
        "strategy": strategy,
        "long_strike": round(float(long_strike), 2),
        "short_strike": round(float(short_strike), 2),
        "width": round(width, 2),
        "debit": debit,
        "mid_debit": mid_debit,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "breakeven": breakeven,
        "risk_reward": risk_reward,
        "liquidity": liquidity,
        "legs": {"long": long_leg, "short": short_leg},
    })

    if include_curves:
        curve_debit = mid_debit if mid_debit is not None else debit
        T = _years_to_expiration(expiration)
        spot = float(lookup.get("price") or 0.0)
        curves = vertical_spread_curves(
            kind=kind,
            long_strike=float(long_strike),
            short_strike=float(short_strike),
            long_iv=long_leg.get("iv") or 0.0,
            short_iv=short_leg.get("iv") or 0.0,
            debit=curve_debit,
            spot=spot,
            T=T,
        )
        curves["params"] = {"T": T, "r": RISK_FREE_RATE, "spot": spot,
                            "debit": curve_debit}
        result["curves"] = curves

    return result
