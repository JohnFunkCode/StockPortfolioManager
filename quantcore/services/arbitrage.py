"""ArbitrageService — relative-value candidate scanner.

Finds securities whose price has stretched against the underlying they are
structurally linked to, across three families:

  nav_vehicle    treasury companies, trusts, closed-end funds — the only
                 family with a computable fair value (units x spot, net of
                 everything senior to the common)
  commodity_etf  fund vs its reference future — real convergence force, but
                 the futures leg is unreachable from an equity-only account
  producer       miners/E&Ps vs the commodity they sell — no fair value, no
                 convergence force, statistical relative value at best

The scoring is deliberately inverted relative to the obvious design: **spread
width only qualifies a candidate; the convergence mechanism ranks it.** A
screen that sorted by discount alone would have put MSTR at the top of the
list in July 2026 on a 37% headline gap that was really ~10% once $16.5B of
converts and preferred were netted out — against negative carry, no redemption
right, and the GBTC precedent where the identical structure widened from -15%
to -50% over three years. Every penalty in ``_score`` traces to one of those
holes; see docs/analysis results/MSTR_BTC_arbitrage_assessment_2026-07-14.md.

Analytics are delegated: spread statistics to ``quantcore.analytics.pairs``,
NAV arithmetic to ``quantcore.analytics.nav``. This module composes, scores,
and explains.
"""

from __future__ import annotations

import datetime
from typing import Optional

import pandas as pd

from quantcore.analytics import nav as nav_math
from quantcore.analytics import pairs
from quantcore.error_text import safe_error_text

# How much each convergence mechanism is trusted to actually close a gap.
# `none` is not zero — a gap can still close on sentiment — but a trade with
# no forcing mechanism is a directional bet wearing a hedge costume.
CONVERGENCE_WEIGHTS = {
    "redemption": 1.0,
    "conversion": 1.0,
    "deal_terms": 1.0,
    "buyback": 0.7,
    "index_event": 0.7,
    "none": 0.35,
}

CONVERGENCE_NOTES = {
    "redemption": "Authorised participants can create/redeem, arbitraging the gap directly.",
    "conversion": "A structural conversion event collapses the vehicle into its assets.",
    "deal_terms": "Contractual deal terms fix the exchange ratio.",
    "buyback": "Issuer buybacks are accretive but discretionary and usually small.",
    "index_event": "Index inclusion/exclusion forces passive flow, but timing is unknowable.",
    "none": "Nothing forces this gap to close. It can stay wide, or widen, indefinitely.",
}

# Reference series the statistical sweep tests candidates against, with the
# sector/industry keywords that make an economic link plausible. Without this
# gate a wide enough sweep will always manufacture cointegrated pairs.
REFERENCE_PANEL = {
    "GC=F": ("gold", "silver", "precious", "mining", "miner", "materials"),
    "HG=F": ("copper", "mining", "miner", "materials", "industrial"),
    "CL=F": ("oil", "petroleum", "energy", "refin", "drilling", "exploration"),
    "NG=F": ("gas", "energy", "utilities", "exploration", "pipeline"),
    "BTC-USD": ("crypto", "bitcoin", "blockchain", "digital asset", "capital markets",
                "software", "technology"),
    "DX-Y.NYB": ("multinational", "export", "materials", "commodit"),
}

STALE_HOLDINGS_DAYS = 45
DEFAULT_HORIZON_DAYS = 180


def _round(val, digits: int = 2):
    try:
        return round(float(val), digits) if val is not None else None
    except (TypeError, ValueError):
        return None


class ArbitrageService:
    """Scans curated and discovered pairs for stretched structural spreads."""

    def __init__(self, arbitrage_repository, prices, yfinance_gateway,
                 horizon_days: int = DEFAULT_HORIZON_DAYS) -> None:
        self._repo = arbitrage_repository
        self._prices = prices
        self._yf = yfinance_gateway
        self._horizon_days = horizon_days

    # ------------------------------------------------------------------ #
    # Universe
    # ------------------------------------------------------------------ #
    def get_universe(self) -> dict:
        """The curated pair list, with holdings staleness surfaced per entry."""
        entries = self._repo.load_universe()
        today = datetime.date.today()
        out = []
        for e in entries:
            item = dict(e)
            item["holdings_age_days"] = self._age_days(e.get("holdings_as_of"), today)
            item["holdings_stale"] = (
                item["holdings_age_days"] is not None
                and item["holdings_age_days"] > STALE_HOLDINGS_DAYS
            )
            item["hedge_available"] = bool(e.get("hedge_instrument"))
            out.append(item)
        return {"count": len(out), "entries": out}

    @staticmethod
    def _age_days(as_of: Optional[str], today: datetime.date) -> Optional[int]:
        if not as_of:
            return None
        try:
            return (today - datetime.date.fromisoformat(str(as_of)[:10])).days
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    # Single pair
    # ------------------------------------------------------------------ #
    def analyze_pair(self, security: str, underlying: Optional[str] = None,
                     days: int = 365, zscore_window: Optional[int] = None) -> dict:
        """Full workup for one pair: spread stats, NAV math, score, verdict."""
        security = (security or "").strip().upper()
        if not security:
            return {"error": "security is required"}

        entry = self._repo.get_entry(security)
        if entry is None:
            if not underlying:
                return {
                    "security": security,
                    "error": f"{security} is not in the curated universe and no "
                             "underlying was supplied.",
                    "hint": "Pass underlying= to analyse an ad-hoc pair, or add "
                            "an entry to arb_universe.yaml for NAV coverage.",
                }
            entry = {
                "security": security, "name": security, "kind": "producer",
                "underlying": underlying.strip(), "hedge_instrument": None,
                "convergence_mechanism": "none", "holdings_units": None,
                "holdings_as_of": None, "senior_claims_usd": 0.0,
                "annual_senior_cost_usd": 0.0, "other_assets_usd": 0.0,
                "diluted_shares": None, "source": "ad-hoc", "notes": None,
                "ad_hoc": True,
            }
        elif underlying:
            entry = {**entry, "underlying": underlying.strip()}

        sec_closes = self._closes(security, days)
        und_closes = self._closes(entry["underlying"], days)
        if sec_closes is None or und_closes is None:
            missing = security if sec_closes is None else entry["underlying"]
            return {
                "security": security,
                "underlying": entry["underlying"],
                "error": f"No price history available for {missing}.",
            }

        stats = pairs.analyze_pair(sec_closes, und_closes,
                                   zscore_window=zscore_window)
        nav_block = self._nav_block(entry, sec_closes, und_closes)
        hedge = self._hedge_block(entry)
        scored = self._score(entry, stats, nav_block, hedge)

        return {
            "security": security,
            "name": entry.get("name", security),
            "kind": entry["kind"],
            "underlying": entry["underlying"],
            "as_of": self._last_date(sec_closes),
            "price": _round(sec_closes.iloc[-1]),
            "underlying_price": _round(und_closes.iloc[-1], 4),
            "statistics": stats,
            "nav": nav_block,
            "hedge": hedge,
            **scored,
            "notes": entry.get("notes"),
            "source": entry.get("source"),
        }

    # ------------------------------------------------------------------ #
    # Scan
    # ------------------------------------------------------------------ #
    def scan(self, kinds: Optional[list[str]] = None, top_n: int = 20,
             days: int = 365) -> dict:
        """Rank the curated universe. Errors on one pair never sink the scan.

        ``top_n`` is clamped at or above zero: the REST edge rejects
        non-positive values outright, but a negative limit reaching the slice
        directly would silently drop the last candidate and report a negative
        count, so in-process callers get the same guarantee.
        """
        top_n = max(0, int(top_n))
        wanted = {k.strip().lower() for k in kinds} if kinds else None
        candidates, errors = [], []
        for entry in self._repo.load_universe():
            if wanted and entry["kind"] not in wanted:
                continue
            try:
                result = self.analyze_pair(entry["security"], days=days)
            except Exception as exc:  # one bad symbol must not sink the scan
                errors.append({"security": entry["security"],
                               "error": safe_error_text(exc)})
                continue
            if result.get("error"):
                errors.append({"security": entry["security"],
                               "error": result["error"]})
                continue
            candidates.append(result)

        candidates.sort(key=lambda c: (c.get("score") or 0), reverse=True)
        returned = candidates[:top_n]
        return {
            "scanned": len(candidates) + len(errors),
            # Counted off the actual slice so it can never contradict the list.
            "returned": len(returned),
            "candidates": returned,
            "errors": errors,
        }

    # ------------------------------------------------------------------ #
    # Statistical discovery
    # ------------------------------------------------------------------ #
    def discover_pairs(self, symbols: list[str],
                       references: Optional[list[str]] = None,
                       days: int = 365, min_abs_correlation: float = 0.4,
                       require_economic_link: bool = True) -> dict:
        """Sweep symbols against commodity/crypto/FX references for cointegration.

        The economic-link gate is not optional decoration: over a wide enough
        sweep, cointegration tests will find statistically significant pairs
        with no causal relationship at all. Candidates whose sector/industry
        does not plausibly connect to the reference are dropped by default.
        """
        refs = [r.strip() for r in (references or list(REFERENCE_PANEL))]
        found, skipped = [], []

        ref_closes: dict[str, pd.Series] = {}
        for ref in refs:
            series = self._closes(ref, days)
            if series is not None:
                ref_closes[ref] = series

        for raw in symbols or []:
            symbol = (raw or "").strip().upper()
            if not symbol:
                continue
            sec_closes = self._closes(symbol, days)
            if sec_closes is None:
                skipped.append({"symbol": symbol, "reason": "no price history"})
                continue
            profile = self._sector_text(symbol)

            for ref, ref_series in ref_closes.items():
                linked = self._economic_link(ref, profile)
                if require_economic_link and not linked:
                    continue
                stats = pairs.analyze_pair(sec_closes, ref_series)
                corr = stats.get("correlation")
                if corr is None or abs(corr) < min_abs_correlation:
                    continue
                if not stats["cointegration"]["cointegrated"]:
                    continue
                found.append({
                    "security": symbol,
                    "underlying": ref,
                    "economic_link": linked,
                    "correlation": corr,
                    "cointegration": stats["cointegration"],
                    "hedge_ratio": stats["hedge_ratio"],
                    "zscore": stats["zscore"]["z"],
                    "half_life_days": stats["half_life"]["half_life_days"],
                    "widening": stats["trend"]["widening"],
                    "note": "Discovered statistically — no curated capital "
                            "structure, so no NAV math and no convergence claim.",
                })

        found.sort(key=lambda f: abs(f["zscore"] or 0), reverse=True)
        return {"count": len(found), "pairs": found, "skipped": skipped,
                "references": list(ref_closes)}

    def _sector_text(self, symbol: str) -> str:
        try:
            info = self._yf.ticker_info(symbol, timeout=15.0) or {}
        except Exception:
            return ""
        parts = [info.get("sector"), info.get("industry"),
                 info.get("longBusinessSummary", "")[:400]]
        return " ".join(str(p) for p in parts if p).lower()

    @staticmethod
    def _economic_link(reference: str, profile_text: str) -> bool:
        keywords = REFERENCE_PANEL.get(reference)
        if not keywords:
            return False
        return any(k in profile_text for k in keywords)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _closes(self, symbol: str, days: int) -> Optional[pd.Series]:
        try:
            df = self._prices.get_history(symbol, "1d", days)
        except Exception:
            return None
        if df is None or getattr(df, "empty", True) or "Close" not in df:
            return None
        series = df["Close"].dropna()
        return series if len(series) >= 30 else None

    @staticmethod
    def _last_date(series: pd.Series) -> Optional[str]:
        try:
            return pd.Timestamp(series.index[-1]).date().isoformat()
        except Exception:
            return None

    def _nav_block(self, entry: dict, sec_closes: pd.Series,
                   und_closes: pd.Series) -> Optional[dict]:
        """NAV workup for nav_vehicle entries with enough curated data."""
        if entry["kind"] != "nav_vehicle":
            return None

        snapshot = None
        try:
            snapshot = self._repo.latest_nav_snapshot(entry["security"])
        except Exception:
            snapshot = None

        if snapshot:
            units = snapshot.get("units")
            as_of = snapshot.get("as_of")
            senior = snapshot.get("senior_claims") or 0.0
            annual = snapshot.get("annual_senior_cost") or 0.0
            other = snapshot.get("other_assets") or 0.0
            shares = snapshot.get("diluted_shares")
            source = snapshot.get("source")
        else:
            units = entry.get("holdings_units")
            as_of = entry.get("holdings_as_of")
            senior = entry.get("senior_claims_usd") or 0.0
            annual = entry.get("annual_senior_cost_usd") or 0.0
            other = entry.get("other_assets_usd") or 0.0
            shares = entry.get("diluted_shares")
            source = entry.get("source")

        if shares is None:
            shares = self._shares_outstanding(entry["security"])
        if not units or not shares:
            return {
                "available": False,
                "reason": "No curated holdings/share count — NAV math skipped. "
                          "Add holdings_units and diluted_shares to "
                          "arb_universe.yaml or record a NAV snapshot.",
                "holdings_as_of": as_of,
            }

        result = nav_math.analyze_nav_vehicle(
            price=float(sec_closes.iloc[-1]),
            units=float(units),
            spot=float(und_closes.iloc[-1]),
            diluted_shares=float(shares),
            senior_claims=float(senior),
            annual_senior_cost=float(annual),
            other_assets=float(other),
        )
        age = self._age_days(as_of, datetime.date.today())
        result.update({
            "available": True,
            "units": float(units),
            "diluted_shares": float(shares),
            "holdings_as_of": as_of,
            "holdings_age_days": age,
            "holdings_stale": age is not None and age > STALE_HOLDINGS_DAYS,
            "source": source,
        })
        return result

    def _shares_outstanding(self, symbol: str) -> Optional[float]:
        try:
            info = self._yf.ticker_info(symbol, timeout=15.0) or {}
        except Exception:
            return None
        for key in ("impliedSharesOutstanding", "sharesOutstanding"):
            val = info.get(key)
            try:
                if val and float(val) > 0:
                    return float(val)
            except (TypeError, ValueError):
                continue
        return None

    def _hedge_block(self, entry: dict) -> dict:
        """Can the underlying leg actually be shorted from an equity account?"""
        hedge = entry.get("hedge_instrument")
        underlying = entry.get("underlying", "")
        futures_only = underlying.endswith("=F") and not hedge
        return {
            "instrument": hedge,
            "available": bool(hedge),
            "futures_only": futures_only,
            "note": (
                f"Hedge the underlying leg with {hedge}."
                if hedge else
                "No equity/ETF hedge is mapped for this underlying — the short "
                "leg would require futures, which this account cannot trade. "
                "Treat any gap as information, not an executable spread."
            ),
        }

    # ------------------------------------------------------------------ #
    # Scoring — every term here traces to a documented failure mode
    # ------------------------------------------------------------------ #
    def _score(self, entry: dict, stats: dict, nav_block: Optional[dict],
               hedge: dict) -> dict:
        reasons: list[str] = []
        breaks_on: list[str] = []

        # 1. Opportunity: how wide is the gap, on the best measure available.
        nav_discount = None
        if nav_block and nav_block.get("available"):
            nav_discount = nav_block.get("premium_discount_pct")
        z = stats.get("zscore", {}).get("z")

        if nav_discount is not None:
            opportunity = min(abs(nav_discount), 40.0) / 40.0 * 100.0
            basis = "nav_discount"
            gross = nav_block.get("gross_premium_discount_pct")
            if gross is not None and nav_discount is not None:
                overstatement = abs(gross) - abs(nav_discount)
                if overstatement > 5:
                    reasons.append(
                        f"Headline gap to gross assets is {gross:.1f}%, but only "
                        f"{nav_discount:.1f}% survives netting out senior claims "
                        f"({overstatement:.0f} points of the discount are an "
                        "accounting illusion)."
                    )
        elif z is not None:
            opportunity = min(abs(z), 3.0) / 3.0 * 100.0
            basis = "spread_zscore"
        else:
            return {
                "score": 0.0, "verdict": "insufficient_data", "basis": None,
                "reasons": ["Not enough overlapping history to measure a spread."],
                "breaks_on": [], "convergence": entry.get("convergence_mechanism"),
            }

        # 2. Evidence of mean reversion — a wide gap that never reverts is not
        #    an opportunity, it is a trend.
        coint = stats.get("cointegration", {})
        reject_at = coint.get("reject_at")
        evidence = {0.01: 1.0, 0.05: 0.85, 0.10: 0.7}.get(reject_at, 0.35)
        if reject_at is None:
            reasons.append("Spread is not statistically cointegrated — the two "
                           "legs may simply drift apart.")

        hl = stats.get("half_life", {}).get("half_life_days")
        if hl is None:
            evidence *= 0.5
            reasons.append("No measurable mean reversion (half-life undefined).")
        elif hl > self._horizon_days:
            evidence *= 0.6
            reasons.append(f"Half-life of {hl:.0f} days exceeds the "
                           f"{self._horizon_days}-day horizon.")

        beta = stats.get("hedge_ratio", {}).get("beta")
        stability = stats.get("hedge_ratio", {}).get("beta_stability")
        if beta and stability and abs(beta) > 0 and stability / abs(beta) > 0.5:
            evidence *= 0.8
            reasons.append("Hedge ratio is unstable across sub-periods — the "
                           "ratio you size with today may not hold.")
            breaks_on.append("Unstable hedge ratio")

        # 3. Convergence mechanism — the ranking term.
        mechanism = entry.get("convergence_mechanism", "none")
        weight = CONVERGENCE_WEIGHTS.get(mechanism, 0.35)
        reasons.append(CONVERGENCE_NOTES.get(mechanism, ""))
        if mechanism == "none":
            breaks_on.append("No forcing mechanism — the gap can widen indefinitely")

        # 4. Hedgeability under an equity-only mandate.
        hedge_factor = 1.0 if hedge["available"] else 0.5
        if not hedge["available"]:
            reasons.append(hedge["note"])
            breaks_on.append("Short leg not executable without futures")

        # 5. Carry: what fraction of the gap survives the wait. Expressed as a
        #    factor rather than a flat deduction so that ordering is preserved
        #    among candidates that all bleed — a subtractive penalty large
        #    enough to matter at the top of the range flattens the bottom of it
        #    to zero and destroys the ranking the scan exists to produce.
        carry = (nav_block or {}).get("carry_drag_pct")
        carry_factor = 1.0
        if carry and nav_discount:
            horizon_years = self._horizon_days / 365.0
            eroded = (carry * horizon_years) / abs(nav_discount)
            carry_factor = max(0.0, 1.0 - eroded)
            years = (nav_block or {}).get("years_of_burn")
            reasons.append(
                f"Carry drag of {carry:.2f}%/yr erodes the NAV while you wait"
                + (f" — the discount funds about {years:.1f} years of it."
                   if years else ".")
            )
            breaks_on.append("Negative carry (senior claims serviced out of NAV)")

        # 6. Trend and data freshness.
        trend_factor = 1.0
        if stats.get("trend", {}).get("widening"):
            trend_factor = 0.75
            reasons.append("Spread has been widening, not converging, over the "
                           "sample — catching it assumes a trend break.")
            breaks_on.append("Spread trending wider")

        freshness_factor = 1.0
        if nav_block and nav_block.get("holdings_stale"):
            freshness_factor = 0.85
            age = nav_block.get("holdings_age_days")
            reasons.append(f"Curated holdings are {age} days old; the NAV figure "
                           "may not reflect current positions.")
            breaks_on.append("Stale holdings data")

        factors = {
            "opportunity": _round(opportunity, 1),
            "evidence": _round(evidence, 3),
            "convergence": _round(weight, 2),
            "hedge": hedge_factor,
            "carry": _round(carry_factor, 3),
            "trend": trend_factor,
            "freshness": freshness_factor,
        }
        score = (opportunity * evidence * weight * hedge_factor
                 * carry_factor * trend_factor * freshness_factor)
        score = max(0.0, min(100.0, score))
        if score >= 60:
            verdict = "candidate"
        elif score >= 35:
            verdict = "watch"
        else:
            verdict = "reject"

        return {
            "score": _round(score, 1),
            "verdict": verdict,
            "basis": basis,
            "convergence": mechanism,
            "factors": factors,
            "reasons": [r for r in reasons if r],
            "breaks_on": breaks_on,
        }
