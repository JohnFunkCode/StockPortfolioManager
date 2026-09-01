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
holes, and each one names itself in the ``reasons``/``breaks_on`` it emits.

Analytics are delegated: spread statistics to ``quantcore.analytics.pairs``,
NAV arithmetic to ``quantcore.analytics.nav``. This module composes, scores,
and explains.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

import pandas as pd

from quantcore.analytics import nav as nav_math
from quantcore.analytics import pairs
from quantcore.analytics.market_time import market_date
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
#
# Every keyword must name *the commodity itself*, never the industry around it.
# The generic words this list used to carry ("mining", "energy", "materials",
# "industrial", "technology") were what a false link travelled on: a *bitcoin*
# miner reads as "mining" and got cointegration-tested against gold and copper,
# and "software"/"technology" on BTC-USD meant every technology company in a
# sweep was eligible to be paired with bitcoin. Measured on live profiles over a
# 28-name sample, the generic terms more than doubled the gate's pass rate (72
# symbol/reference pairs, against 29 here) while adding no true positive — a
# real gold miner says "gold", so the specific term already carried the link.
#
# Matching is word-boundary anchored, so "Goldman" no longer reads as "gold"
# (which put an investment bank against gold futures). A trailing ``*`` marks a
# stem, for the cases where the boundary is the thing in the way:
# "crypto*" must reach "cryptocurrency", "digital asset*" must reach "assets".
REFERENCE_PANEL = {
    "GC=F": ("gold", "silver", "precious metal*", "bullion"),
    "HG=F": ("copper",),
    "CL=F": ("oil", "petroleum", "crude"),
    "NG=F": ("natural gas", "lng", "midstream"),
    "BTC-USD": ("bitcoin", "crypto*", "blockchain", "digital asset*"),
    "DX-Y.NYB": ("multinational", "export*", "commodit*"),
}


def _compile_keyword(keyword: str) -> re.Pattern:
    """Compile a REFERENCE_PANEL keyword to a word-boundary-anchored pattern."""
    is_stem = keyword.endswith("*")
    body = re.escape(keyword[:-1] if is_stem else keyword)
    return re.compile(r"\b" + body + ("" if is_stem else r"\b"))


_REFERENCE_PATTERNS = {
    reference: tuple(_compile_keyword(k) for k in keywords)
    for reference, keywords in REFERENCE_PANEL.items()
}

STALE_HOLDINGS_DAYS = 45
DEFAULT_HORIZON_DAYS = 180

# Discovery caps. ``discover_pairs`` itself does not enforce these — an
# in-process caller with a deliberate list is allowed a big sweep — but every
# caller that takes its symbol list from *outside* must, because the work is
# O(symbols x references) history fetches. The constants live here rather than
# in api/routers/arbitrage.py so the REST route and the Sidekick tool handler
# (issue #208) cannot drift to different numbers; they are the same limit.
MAX_DISCOVER_SYMBOLS = 25
MAX_DISCOVER_REFERENCES = 10


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
    def get_universe(self, *, now: datetime.datetime | None = None) -> dict:
        """The curated pair list, with holdings staleness surfaced per entry."""
        entries = self._repo.load_universe()
        today = market_date(now)
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
                     days: int = 365, zscore_window: Optional[int] = None,
                     *, now: datetime.datetime | None = None) -> dict:
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
        nav_block = self._nav_block(entry, sec_closes, und_closes, now=now)
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
             days: int = 365, *, now: datetime.datetime | None = None) -> dict:
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
                result = self.analyze_pair(entry["security"], days=days, now=now)
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

    def scan_summary(self, kinds: Optional[list[str]] = None, top_n: int = 10,
                     days: int = 365, *, now: datetime.datetime | None = None) -> dict:
        """``scan`` trimmed to one row per candidate, for the chat sidekick.

        A full scan returns ten candidates each carrying complete spread
        statistics, a NAV block, factors, reasons and breaks_on — far more than
        an LLM needs to rank them, and enough to crowd a conversation's context
        window. This keeps the fields that carry the judgement (score, verdict,
        the *net* discount, convergence, hedgeability, what breaks it) and drops
        the rest; the model calls ``analyze_pair`` for the full workup on
        anything worth a closer look.

        ``discount_pct`` is deliberately the NET figure. The gross number is
        omitted here entirely rather than trimmed to it — a summary row is
        exactly where a misread would happen.
        """
        full = self.scan(kinds=kinds, top_n=top_n, days=days, now=now)
        rows = []
        for c in full["candidates"]:
            nav = c.get("nav") or {}
            stats = c.get("statistics") or {}
            rows.append({
                "security": c["security"],
                "name": c.get("name"),
                "kind": c["kind"],
                "underlying": c["underlying"],
                "score": c.get("score"),
                "verdict": c.get("verdict"),
                "basis": c.get("basis"),
                "discount_pct": nav.get("premium_discount_pct") if nav.get("available") else None,
                "spread_zscore": (stats.get("zscore") or {}).get("z"),
                "half_life_days": (stats.get("half_life") or {}).get("half_life_days"),
                "cointegrated": (stats.get("cointegration") or {}).get("cointegrated"),
                "convergence": c.get("convergence"),
                "hedge_instrument": (c.get("hedge") or {}).get("instrument"),
                "hedge_available": (c.get("hedge") or {}).get("available"),
                "breaks_on": c.get("breaks_on", []),
            })
        return {
            "scanned": full["scanned"],
            "returned": len(rows),
            "candidates": rows,
            "errors": full["errors"],
            "note": (
                "Summary rows. discount_pct is NET of senior claims. Call "
                "analyze_arbitrage_pair for the full workup on any row."
            ),
        }

    # ------------------------------------------------------------------ #
    # Chartable histories
    # ------------------------------------------------------------------ #
    def get_spread_history(self, security: str, underlying: Optional[str] = None,
                           days: int = 365) -> dict:
        """The spread series itself, with the bands and trend line to draw it.

        ``analyze_pair`` computes this series and then discards it — a payload
        of a thousand floats is noise to an LLM and a test pins that it never
        leaks there. A chart is the one caller that genuinely needs it, so it
        gets its own endpoint rather than fattening the workup.
        """
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
                    "hint": "Pass underlying= to chart an ad-hoc pair.",
                }
            resolved = underlying.strip()
            kind = "producer"
        else:
            resolved = (underlying or entry["underlying"]).strip()
            kind = entry["kind"]

        sec_closes = self._closes(security, days)
        und_closes = self._closes(resolved, days)
        if sec_closes is None or und_closes is None:
            missing = security if sec_closes is None else resolved
            return {"security": security, "underlying": resolved,
                    "error": f"No price history available for {missing}."}

        ys, xs = pairs.align(sec_closes, und_closes)
        hr = pairs.hedge_ratio(ys, xs)
        if hr["beta"] is None:
            return {"security": security, "underlying": resolved,
                    "error": "Could not fit a hedge ratio for this pair."}

        spread = pairs.spread_series(ys, xs, hr["beta"], hr["alpha"])
        stats = pairs.zscore(spread)
        mean = stats["mean"] or 0.0
        std = stats["std"] or 0.0

        points = [
            {"date": pd.Timestamp(idx).date().isoformat(), "spread": _round(value, 6)}
            for idx, value in spread.items()
        ]
        return {
            "security": security,
            "name": (entry or {}).get("name", security),
            "kind": kind,
            "underlying": resolved,
            "points": points,
            "mean": _round(mean, 6),
            "std": _round(std, 6),
            # Precomputed so the chart draws bands without doing arithmetic
            # (arch-v2 Rule 8 — displayed math stays in the backend).
            "bands": {
                "plus_one": _round(mean + std, 6),
                "minus_one": _round(mean - std, 6),
                "plus_two": _round(mean + 2 * std, 6),
                "minus_two": _round(mean - 2 * std, 6),
            },
            "latest": {
                "date": points[-1]["date"] if points else None,
                "spread": points[-1]["spread"] if points else None,
                "z": stats["z"],
            },
            "hedge_ratio": hr,
            "half_life": pairs.half_life(spread),
            "cointegration": {
                k: v for k, v in pairs.engle_granger(ys, xs).items() if k != "spread"
            },
            "trend": pairs.spread_trend(spread),
        }

    def get_premium_history(
        self, security: str, days: int = 365, *, now: datetime.datetime | None = None
    ) -> dict:
        """Discount-to-NAV over time for a NAV vehicle.

        Only the current capital structure is known (one curated snapshot, or
        the YAML bootstrap), so every point applies TODAY's holdings and senior
        claims to that date's prices. That is an approximation, not history —
        the payload says so in ``method`` and ``note``, and the chart is
        required to show it. It converges on the truth as ``arb_nav_snapshots``
        accumulates daily rows.
        """
        security = (security or "").strip().upper()
        entry = self._repo.get_entry(security)
        if entry is None:
            return {"security": security,
                    "error": f"{security} is not in the curated universe."}
        if entry["kind"] != "nav_vehicle":
            return {
                "security": security,
                "kind": entry["kind"],
                "error": f"{security} is a {entry['kind']}, not a NAV vehicle — "
                         "there is no net asset value to discount against.",
            }

        snapshot = None
        try:
            snapshot = self._repo.latest_nav_snapshot(security)
        except Exception:
            snapshot = None
        source = snapshot or {}
        units = source.get("units") or entry.get("holdings_units")
        as_of = source.get("as_of") or entry.get("holdings_as_of")
        senior = source.get("senior_claims", entry.get("senior_claims_usd")) or 0.0
        other = source.get("other_assets", entry.get("other_assets_usd")) or 0.0
        shares = source.get("diluted_shares") or entry.get("diluted_shares")
        if shares is None:
            shares = self._shares_outstanding(security)
        if not units or not shares:
            return {
                "security": security,
                "error": "No curated holdings or share count — add "
                         "holdings_units and diluted_shares to arb_universe.yaml, "
                         "or record a NAV snapshot.",
                "holdings_as_of": as_of,
            }

        sec_closes = self._closes(security, days)
        und_closes = self._closes(entry["underlying"], days)
        if sec_closes is None or und_closes is None:
            missing = security if sec_closes is None else entry["underlying"]
            return {"security": security,
                    "error": f"No price history available for {missing}."}

        ys, xs = pairs.align(sec_closes, und_closes)
        dated = [(pd.Timestamp(i).date().isoformat(), float(v)) for i, v in ys.items()]
        spots = [(pd.Timestamp(i).date().isoformat(), float(v)) for i, v in xs.items()]
        series = nav_math.premium_history(
            prices=dated, spots=spots, units=float(units),
            diluted_shares=float(shares), senior_claims=float(senior),
            other_assets=float(other),
        )
        age = self._age_days(as_of, market_date(now))
        return {
            "security": security,
            "name": entry.get("name", security),
            "underlying": entry["underlying"],
            "points": series,
            "latest": series[-1] if series else None,
            "units": float(units),
            "diluted_shares": float(shares),
            "senior_claims": float(senior),
            "holdings_as_of": as_of,
            "holdings_age_days": age,
            "holdings_stale": age is not None and age > STALE_HOLDINGS_DAYS,
            "method": "current_capital_structure",
            "note": (
                "Approximation: today's holdings and senior claims are applied to "
                "past prices, so this is not a record of the discount as it stood. "
                "It becomes exact as daily NAV snapshots accumulate."
            ),
        }

    # ------------------------------------------------------------------ #
    # Statistical discovery
    # ------------------------------------------------------------------ #
    def discover_pairs(self, symbols: list[str],
                       references: Optional[list[str]] = None,
                       days: int = 365, min_abs_correlation: float = 0.4,
                       require_economic_link: bool = True,
                       include_all: bool = False) -> dict:
        """Sweep symbols against commodity/crypto/FX references for cointegration.

        The economic-link gate is not optional decoration: over a wide enough
        sweep, cointegration tests will find statistically significant pairs
        with no causal relationship at all. Candidates whose sector/industry
        does not plausibly connect to the reference are dropped by default.

        ``include_all`` additionally returns every pair that was *tested*, each
        carrying ``passed`` and the reason it failed. A bare pass/fail list
        hides the near-misses, and those are the interesting cases: NEM vs gold
        correlates 0.73 with a 6.4-day half-life and still fails, its
        Engle-Granger statistic landing 0.17 short of the loosest critical
        value. ``pairs`` stays passes-only either way, so existing callers are
        unaffected.

        **Every requested symbol comes back in exactly one bucket.**
        ``symbols_tested`` and ``skipped`` partition ``symbols_requested``, so a
        caller can prove coverage instead of assuming it. This is not
        bookkeeping for its own sake: the return used to name only the symbols
        that *found* something, which left a caller splitting a large list
        across several calls with no way to reconcile the answers against the
        input. An agent doing exactly that (issue #208) dropped a ticker
        silently and then reported full coverage — the omission was
        undetectable from the response, so a confident wrong answer was the
        only answer available to it.
        """
        refs = [r.strip() for r in (references or list(REFERENCE_PANEL))]
        found, skipped, tested = [], [], []
        requested, swept, seen = [], [], set()

        ref_closes: dict[str, pd.Series] = {}
        for ref in refs:
            series = self._closes(ref, days)
            if series is not None:
                ref_closes[ref] = series

        for raw in symbols or []:
            symbol = (raw or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            requested.append(symbol)

            sec_closes = self._closes(symbol, days)
            if sec_closes is None:
                skipped.append({"symbol": symbol, "reason": "no price history"})
                continue
            profile = self._sector_text(symbol)
            analyzed = False

            for ref, ref_series in ref_closes.items():
                linked = self._economic_link(ref, profile)
                if require_economic_link and not linked:
                    if include_all:
                        tested.append(self._tested_row(
                            symbol, ref, linked, None, "no economic link"))
                    continue
                analyzed = True
                stats = pairs.analyze_pair(sec_closes, ref_series)
                corr = stats.get("correlation")
                cointegrated = stats["cointegration"]["cointegrated"]
                if corr is None or abs(corr) < min_abs_correlation:
                    if include_all:
                        tested.append(self._tested_row(
                            symbol, ref, linked, stats,
                            f"correlation below the {min_abs_correlation} floor"))
                    continue
                if not cointegrated:
                    if include_all:
                        tested.append(self._tested_row(
                            symbol, ref, linked, stats, "not cointegrated"))
                    continue
                if include_all:
                    tested.append(self._tested_row(symbol, ref, linked, stats, None))
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

            # A symbol the gate blocked against every reference was never run
            # through a cointegration test, so calling it "tested and found
            # nothing" would misreport it. It is excluded, with the reason —
            # and the reason distinguishes the symbol's own failing from the
            # panel having no usable series at all, which is not its fault.
            if analyzed:
                swept.append(symbol)
            else:
                skipped.append({
                    "symbol": symbol,
                    "reason": "no economic link to any reference" if ref_closes
                              else "no reference price history",
                })

        found.sort(key=lambda f: abs(f["zscore"] or 0), reverse=True)
        result = {"count": len(found), "pairs": found,
                  "symbols_requested": requested, "symbols_tested": swept,
                  "skipped": skipped, "references": list(ref_closes)}
        if include_all:
            # Most negative statistic first — the order a reader scans for
            # near-misses against the critical value.
            tested.sort(key=lambda t: (t["statistic"] is None,
                                       t["statistic"] if t["statistic"] is not None else 0))
            result["tested"] = tested
            result["critical_values"] = dict(pairs._EG_CRIT_2VAR)
        return result

    @staticmethod
    def _tested_row(symbol: str, reference: str, linked: bool,
                    stats: Optional[dict], failed_because: Optional[str]) -> dict:
        """One row of the include_all sweep: what was measured and why it fell out."""
        coint = (stats or {}).get("cointegration") or {}
        return {
            "security": symbol,
            "underlying": reference,
            "economic_link": linked,
            "correlation": (stats or {}).get("correlation"),
            "statistic": coint.get("statistic"),
            "reject_at": coint.get("reject_at"),
            "half_life_days": ((stats or {}).get("half_life") or {}).get("half_life_days"),
            "passed": failed_because is None,
            "failed_because": failed_because,
        }

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
        patterns = _REFERENCE_PATTERNS.get(reference)
        if not patterns:
            return False
        return any(p.search(profile_text) for p in patterns)

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
                   und_closes: pd.Series,
                   *, now: datetime.datetime | None = None) -> Optional[dict]:
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
        age = self._age_days(as_of, market_date(now))
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
