"""Pure Portfolio Check math: industry wind labels and per-holding decisions.

Ported from the Market Casting prototype (``rules.py``, ``industry_backtest.py``
and ``portfolio_check.py``) for the per-user Portfolio Check
(docs/proposals/portfolio-check-plan.md, step 1). No I/O: the caller supplies
the Fama-French industry series, the S&P 500 peer table, the owner's lots and
prices, and today's date. ``tests/test_analytics_portfolio_check.py`` pins
parity with the prototype on its made-up sample portfolio.

Two halves, which change at very different rates:

* **Market state** (monthly, the same for everyone): ``industry_inputs`` →
  ``industry_state`` labels each of the 49 industries from its 12-month wind
  (the change in its log share of total market value, skipping the latest
  month); ``peer_table`` holds S&P 500 market caps at the two window dates.
* **Per holding** (every request): ``check_portfolio`` weighs the owner's
  lots, compares each stock with its industry peers (``relative_wind``) and
  calls ``decide`` for an action, its ``reasons`` and a tax note.

Like the arbitrage scorer, every rule that fires adds its own reason string,
so the output explains itself. Research output, not financial advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

# ------------------------------------------------------------ the rules ----
# Industries are ranked each review by 12-month wind (latest month skipped), as
# a percentile among all industries (1.0 = strongest). Buffer zones and the
# two-strike sell keep trading, and taxes, down.
BUY_P = 0.80          # Buy zone at or above this percentile
SELL_P = 0.40         # Watch (first strike) / Sell (second strike) at or below
REVIEW_MONTHS = 3     # quarterly reviews

# Trading days back for the stock-vs-peers wind: 12 months, skipping the latest.
WIN_A, WIN_B = 253, 22
MIN_PEERS = 3         # fewer S&P 500 peers than this → no stock-vs-peers measure

# The 12-month gap after a Sell label is measured over this period by the
# prototype's label backtest; the "since 2000" caveat comes from the same run.
DEFAULT_SELL_EDGE_PERIOD = "1931–2026"

# Industry labels, and the actions a holding can get, in report order.
LABELS = ("Buy zone", "Hold", "Watch", "Sell")
ACTION_ORDER = {"Sell": 0, "Review": 1, "Hold, on watch": 2, "Hold": 3,
                "Hold (at size limit)": 4, "Buy / add": 5}
SELL_AFTER_ORDER = 0.5  # "Sell after <date>" sorts between Sell and Review

NAMES49 = {
    "Agric": "Agriculture", "Food": "Food Products", "Soda": "Soft Drinks", "Beer": "Beer & Liquor",
    "Smoke": "Tobacco", "Toys": "Recreation", "Fun": "Entertainment", "Books": "Printing & Publishing",
    "Hshld": "Consumer Goods", "Clths": "Apparel", "Hlth": "Healthcare Services", "MedEq": "Medical Equipment",
    "Drugs": "Pharmaceuticals", "Chems": "Chemicals", "Rubbr": "Rubber & Plastics", "Txtls": "Textiles",
    "BldMt": "Construction Materials", "Cnstr": "Construction", "Steel": "Steel", "FabPr": "Fabricated Products",
    "Mach": "Machinery", "ElcEq": "Electrical Equipment", "Autos": "Autos & Trucks", "Aero": "Aircraft",
    "Ships": "Shipbuilding & Railroad Eq.", "Guns": "Defense", "Gold": "Precious Metals",
    "Mines": "Industrial Mining", "Coal": "Coal", "Oil": "Oil & Gas", "Util": "Utilities",
    "Telcm": "Telecom", "PerSv": "Personal Services", "BusSv": "Business Services", "Hardw": "Computers",
    "Softw": "Software", "Chips": "Semiconductors & Electronics", "LabEq": "Measuring & Control Eq.",
    "Paper": "Business Supplies", "Boxes": "Shipping Containers", "Trans": "Transportation",
    "Whlsl": "Wholesale", "Rtail": "Retail", "Meals": "Restaurants & Hotels", "Banks": "Banking",
    "Insur": "Insurance", "RlEst": "Real Estate", "Fin": "Trading & Finance", "Other": "Other",
}


@dataclass(frozen=True)
class CheckParams:
    """The owner's tax rates and size limits (the prototype's defaults)."""
    st_rate: float = 0.32         # short-term gains tax rate
    lt_rate: float = 0.15         # long-term gains tax rate
    position_cap: float = 0.10    # max weight of one stock
    industry_cap: float = 0.25    # max weight of one industry
    wait_days: int = 120          # wait for long-term treatment if it is this close


# -------------------------------------------------------- market state ----
def industry_inputs(ret_pct: pd.DataFrame, size: pd.DataFrame, nfirms: pd.DataFrame,
                    bm_annual: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Monthly returns, starting market caps and book/market from the FF49 tables.

    ``ret_pct``, ``size`` (average firm size) and ``nfirms`` are monthly, indexed
    by ``YYYYMM``, returns in percent; ``bm_annual`` is indexed by year. Book/market
    formed in June of year y applies from July y to June y+1. Returns
    ``(ret, cap_start, bm)`` on a monthly ``PeriodIndex``.
    """
    ret = ret_pct / 100
    ret.index = pd.PeriodIndex([str(p) for p in ret.index], freq="M")
    size, nfirms = size.copy(), nfirms.copy()
    size.index = nfirms.index = ret.index
    cap_start = (size * nfirms).where(ret.notna())     # market cap entering each month
    bm = bm_annual.copy()
    bm.index = [int(y) for y in bm.index]
    years = [p.year if p.month >= 7 else p.year - 1 for p in ret.index]
    bm_m = bm.reindex(index=years, columns=ret.columns).astype(float)
    bm_m.index = ret.index
    return ret, cap_start, bm_m.where(bm_m > 0)


def relative_log_share(ret: pd.DataFrame, cap_start: pd.DataFrame) -> pd.DataFrame:
    """Cumulative change in each industry's log share of total market value.

    Each month adds ln(1 + r_industry) - ln(1 + r_market), the market being the
    cap-weighted average. Only differences of this series mean anything, which is
    why a window computed from a truncated history equals one from the full history.
    """
    w = cap_start.div(cap_start.sum(axis=1), axis=0)
    mkt = (ret * w).sum(axis=1, min_count=1)
    lr = np.log1p(ret).sub(np.log1p(mkt), axis=0)
    return lr.fillna(0).cumsum().where(ret.notna())


def wind(rel: pd.DataFrame, months: int, skip: int = 0) -> pd.DataFrame:
    """Change in log share over ``months``, ending ``skip`` months ago."""
    return rel.shift(skip) - rel.shift(months)


def percentile_ranks(sig: pd.DataFrame) -> pd.DataFrame:
    """Percentile rank across industries for each month (1.0 = strongest)."""
    return sig.rank(axis=1, pct=True)


def label_industries(p12: pd.DataFrame, t, prev_t) -> dict[str, str]:
    """Label every industry at review ``t``, given the previous review ``prev_t``."""
    now, prev = p12.loc[t], p12.loc[prev_t]
    out = {}
    for ind, p in now.dropna().items():
        if p >= BUY_P:
            out[ind] = "Buy zone"
        elif p <= SELL_P and prev.get(ind, 1) <= SELL_P:
            out[ind] = "Sell"
        elif p <= SELL_P:
            out[ind] = "Watch"
        else:
            out[ind] = "Hold"
    return out


def industry_state(ret: pd.DataFrame, cap_start: pd.DataFrame, bm: pd.DataFrame,
                   names: Mapping[str, str] = NAMES49,
                   review_months: int = REVIEW_MONTHS) -> tuple[pd.DataFrame, str]:
    """Label, percentile and winds of every industry at the latest month.

    Returns the industries strongest first (columns ``code, industry, label, pct,
    pct_prev, wind12, wind60, bm, rank``) and the month they are as of (``YYYY-MM``).
    Needs at least 61 months of history for the 5-year wind.
    """
    rel = relative_log_share(ret, cap_start)
    w12, w60 = wind(rel, 12, 1), wind(rel, 60)
    p12 = percentile_ranks(w12)
    t = p12.dropna(how="all").index[-1]
    prev = t - review_months
    rows = []
    for code, lab in label_industries(p12, t, prev).items():
        rows.append({"code": code, "industry": names.get(code, code), "label": lab,
                     "pct": float(p12.loc[t, code]), "pct_prev": float(p12.loc[prev, code]),
                     "wind12": float(np.expm1(w12.loc[t, code])),
                     "wind60": float(np.expm1(w60.loc[t, code])) if pd.notna(w60.loc[t, code]) else np.nan,
                     "bm": float(bm.loc[t, code]) if pd.notna(bm.loc[t, code]) else np.nan})
    ind = pd.DataFrame(rows).sort_values("pct", ascending=False).reset_index(drop=True)
    ind["rank"] = np.arange(1, len(ind) + 1)
    return ind, str(t)


def peer_table(constituents: pd.DataFrame, close_a: Mapping[str, float], close_b: Mapping[str, float],
               close_now: Mapping[str, float], codes: Mapping[str, Optional[str]]) -> pd.DataFrame:
    """S&P 500 members with their industry and market cap at both window dates.

    ``constituents`` has ``ticker, name, yahoo, shares``; the ``close_*`` maps are
    keyed by the Yahoo symbol; ``codes`` maps a ticker to its FF49 code. Share counts
    are held fixed. Yahoo reports a company's whole share count on every share class
    (GOOGL/GOOG), so only the first class of a company is kept.
    """
    cons = constituents.assign(
        base=constituents["name"].str.replace(r"\s*\(Class [A-Z]\)", "", regex=True)
    ).drop_duplicates("base")
    cons["code"] = cons["ticker"].map(lambda t: codes.get(t))
    for col, closes in (("capA", close_a), ("capB", close_b), ("cap", close_now)):
        cons[col] = cons["yahoo"].map(lambda y, c=closes: c.get(y, np.nan)) * cons["shares"]
    return cons.dropna(subset=["code", "capA", "capB"]).reset_index(drop=True)


# --------------------------------------------------------- per holding ----
def relative_wind(ticker: str, code: Optional[str], price_a: float, price_b: float,
                  peers: pd.DataFrame) -> tuple[float, int]:
    """A stock's wind minus its cap-weighted S&P 500 industry peers' (itself excluded).

    Returns ``(log wind, peer count)``; the wind is NaN with fewer than
    ``MIN_PEERS`` peers or a missing price.
    """
    g = peers[(peers["code"] == code) & (peers["ticker"] != ticker)]
    if len(g) < MIN_PEERS or not (price_a > 0 and price_b > 0):
        return np.nan, len(g)
    return np.log(price_b / price_a) - np.log(g["capB"].sum() / g["capA"].sum()), len(g)


def _money(x: float) -> str:
    return ("−" if x < 0 else "") + f"${abs(x):,.0f}"


def decide(h: Mapping[str, Any], lots: pd.DataFrame, ind_row: Optional[Mapping[str, Any]],
           n_industries: int, params: CheckParams, *, today: date, sell_edge: float,
           sell_edge_period: str = DEFAULT_SELL_EDGE_PERIOD) -> tuple[str, list[str], str]:
    """``(action, reasons, tax note)`` for one holding.

    ``h`` is the holding (``value, weight, ind_weight, code, sw, sw_peers, sw_pct``),
    ``lots`` its lots (``value, cost, acquired``), ``ind_row`` its industry's row from
    ``industry_state`` (None when unclassified). ``sell_edge`` is how far Sell-labelled
    industries trailed the market over the next 12 months in the label backtest.
    The tax note needs a cost and a purchase date on every lot.
    """
    reasons, tax = [], ""
    if ind_row is None:
        return "Review", ["Could not match this stock to one of the 49 tested industries."], ""
    lab = ind_row["label"]
    rank_txt = (f"{ind_row['industry']} ranks {ind_row['rank']} of {n_industries} industries by "
                f"12-month wind ({ind_row['wind12']:+.0%} vs market)")
    now = pd.Timestamp(today)
    has_tax = lots["cost"].notna().all() and lots["acquired"].notna().all()
    if has_tax:
        lots = lots.assign(gain=lots["value"] - lots["cost"],
                           lt_date=lots["acquired"] + pd.Timedelta(days=366))
        lots["is_lt"] = lots["lt_date"] <= now
        st_gain = lots[(~lots.is_lt) & (lots.gain > 0)]
        lt_gain = lots.loc[lots.is_lt, "gain"].sum()
        st_total = lots.loc[~lots.is_lt, "gain"].sum()

    if lab == "Sell":
        reasons.append(f"{rank_txt}. It has been in the bottom {SELL_P:.0%} at two reviews in a row.")
        reasons.append(f"Historically, industries with this label trailed the market by about {abs(sell_edge):.1%} "
                       f"over the next 12 months ({sell_edge_period}). Since 2000 the gap has been small and not reliable.")
        action = "Sell"
        if has_tax:
            if not st_gain.empty:
                wait = (st_gain["lt_date"].max() - now).days
                extra = st_gain["gain"].sum() * (params.st_rate - params.lt_rate)
                expected = abs(sell_edge) * h["value"]
                if wait <= params.wait_days or extra > expected:
                    action = f"Sell after {st_gain['lt_date'].max():%b %d, %Y}"
                    reasons.append(f"Some lots are short-term gains. Waiting {wait} days until they turn long-term saves about "
                                   f"${extra:,.0f} in tax, versus an expected cost of waiting of about ${expected:,.0f}.")
            tax = (f"Selling now realizes {_money(lt_gain)} long-term and {_money(st_total)} short-term gain/loss; "
                   f"estimated tax {_money(max(lt_gain, 0) * params.lt_rate + max(st_total, 0) * params.st_rate)}.")
            if lots["gain"].sum() < 0:
                tax += " This position is at a loss, so selling also harvests a tax loss (avoid rebuying within 30 days: wash-sale rule)."
    elif lab == "Watch":
        action = "Hold, on watch"
        reasons.append(f"{rank_txt}. First review in the bottom {SELL_P:.0%}; it becomes a Sell if it is still there at the next review.")
    elif lab == "Buy zone":
        reasons.append(f"{rank_txt}, in the top {1 - BUY_P:.0%}.")
        if h["weight"] >= params.position_cap:
            action = "Hold (at size limit)"
            reasons.append(f"Already {h['weight']:.1%} of the portfolio, at or above the {params.position_cap:.0%} position limit.")
        elif h["ind_weight"] >= params.industry_cap:
            action = "Hold (at size limit)"
            reasons.append(f"This industry is already {h['ind_weight']:.1%} of the portfolio (limit {params.industry_cap:.0%}).")
        else:
            action = "Buy / add"
    else:
        action = "Hold"
        reasons.append(f"{rank_txt}, in the middle of the pack.")

    if h["weight"] > params.position_cap * 1.5 and not action.startswith("Sell"):
        reasons.append(f"Large position: {h['weight']:.1%} of the portfolio. Consider trimming for diversification.")
    if pd.notna(h["sw"]):
        pct = h["sw_pct"]
        where = "top 20%" if pct >= 0.8 else "bottom 20%" if pct <= 0.2 else "middle"
        reasons.append(f"Against its {h['sw_peers']} S&P 500 industry peers, the stock is {np.expm1(h['sw']):+.0%} over 12 months "
                       f"(latest month skipped), in the {where} of S&P 500 stocks on this measure. "
                       f"A quick 2021–2026 test suggests this helps pick stocks within an industry, but it isn't confirmed yet.")
    elif h["code"]:
        reasons.append("Too few S&P 500 peers in this industry to compare the stock with its industry.")
    return action, reasons, tax


def ff49_for_sic(sic: Optional[int], ranges: Iterable[tuple[int, int, str]]) -> Optional[str]:
    """The FF49 industry of a SIC code: the first matching range, ``Other`` if none, None without a code."""
    if sic is None:
        return None
    for lo, hi, code in ranges:
        if lo <= sic <= hi:
            return code
    return "Other"


def label_forward_edge(p12: pd.DataFrame, rel: pd.DataFrame, *, label: str = "Sell", horizon: int = 12,
                       first: str = "1931-09", since: str = "1931-01", min_industries: int = 40,
                       review_months: int = REVIEW_MONTHS) -> float:
    """How industries given ``label`` did against the market over the next ``horizon`` months.

    The prototype's label event study: at each quarterly review (from ``first``,
    once ``min_industries`` have a percentile), label the industries, take each
    labelled one's change in log share over the next ``horizon`` months, average
    across industries per review, then across reviews since ``since``. Returned as
    a simple return (``expm1``); negative means they trailed. NaN without data.
    """
    months = rel.index[(rel.index >= pd.Period(first, "M")) & (p12.notna().sum(axis=1) >= min_industries)]
    reviews = [t for t in months if t.month % review_months == 0]
    since_p = pd.Period(since, "M")
    per_review = []
    for i, t in enumerate(reviews[1:], 1):
        j = rel.index.get_loc(t) + horizon
        if t < since_p or j >= len(rel):
            continue
        vals = [rel[ind].iloc[j] - rel[ind].loc[t]
                for ind, lab in label_industries(p12, t, reviews[i - 1]).items() if lab == label]
        vals = [v for v in vals if pd.notna(v)]
        if vals:
            per_review.append(float(np.mean(vals)))
    return float(np.expm1(np.mean(per_review))) if per_review else float("nan")


# ----------------------------------------------------- market state file ----
MARKET_STATE_SCHEMA = 1
MIN_MARKET_STATE_INDUSTRIES = 40
MIN_MARKET_STATE_PEERS = 300     # a full S&P 500 is ~500; fewer means a broken scrape


def window_dates(trading_days: pd.Index) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The stock-vs-peers window: ``WIN_A`` and ``WIN_B`` trading days before the last."""
    return trading_days[-WIN_A], trading_days[-WIN_B]


def build_market_state(ret: pd.DataFrame, cap_start: pd.DataFrame, bm: pd.DataFrame,
                       constituents: pd.DataFrame, closes: pd.DataFrame,
                       classes: Mapping[str, Mapping[str, Any]], *, generated: date) -> dict[str, Any]:
    """The monthly market-state file: everything ``check_portfolio`` needs that isn't the owner's.

    ``ret``/``cap_start``/``bm`` come from ``industry_inputs`` over the full FF49
    history (the Sell edge is measured from 1931). ``constituents`` has
    ``ticker, name, yahoo, shares``; ``closes`` is daily closes, one column per
    Yahoo symbol, at least ``WIN_A`` trading days long. ``classes`` maps a ticker to
    ``{"ff49", "sic", "sic_desc", "name"}`` for every constituent and any extra
    tickers worth pre-classifying. The window dates are fixed here, so the service
    must price the owner's stocks at those same two dates.
    """
    rel = relative_log_share(ret, cap_start)
    ind, asof = industry_state(ret, cap_start, bm)
    edge = label_forward_edge(percentile_ranks(wind(rel, 12, 1)), rel)
    closes = closes.sort_index().ffill()
    a, b = window_dates(closes.index)
    codes = {t: c.get("ff49") for t, c in classes.items()}
    peers = peer_table(constituents, closes.loc[a], closes.loc[b], closes.iloc[-1], codes)
    return {
        "schema": MARKET_STATE_SCHEMA,
        "generated": generated.isoformat(),
        "industries_asof": asof,
        "industries": [{k: _clean(v) for k, v in row.items()} for row in ind.to_dict("records")],
        "sell_edge": {"value": edge, "label": "Sell", "horizon_months": 12,
                      "period": f"1931–{ret.index[-1].year}"},
        "window": {"a": pd.Timestamp(a).date().isoformat(), "b": pd.Timestamp(b).date().isoformat(),
                   "prices_asof": pd.Timestamp(closes.index[-1]).date().isoformat()},
        "peers": [{"ticker": r.ticker, "name": r.name, "code": r.code, "shares": float(r.shares),
                   "capA": float(r.capA), "capB": float(r.capB), "cap": _clean(float(r.cap))}
                  for r in peers.itertuples()],
        "classes": {t: {"ff49": c.get("ff49"), "sic": c.get("sic"), "sic_desc": c.get("sic_desc") or "",
                        "name": c.get("name") or ""} for t, c in sorted(classes.items())},
    }


def validate_market_state(state: Mapping[str, Any], *, min_industries: int = MIN_MARKET_STATE_INDUSTRIES,
                          min_peers: int = MIN_MARKET_STATE_PEERS) -> Mapping[str, Any]:
    """Raise ``ValueError`` naming the first problem with a market-state file; return it when sound."""
    def fail(msg):
        raise ValueError(f"portfolio check market state: {msg}")

    if state.get("schema") != MARKET_STATE_SCHEMA:
        fail(f"schema {state.get('schema')!r}, expected {MARKET_STATE_SCHEMA}")
    inds = state.get("industries") or []
    if len(inds) < min_industries:
        fail(f"{len(inds)} industries, expected at least {min_industries}")
    codes = [i.get("code") for i in inds]
    if len(set(codes)) != len(codes):
        fail("duplicate industry codes")
    if sorted(i.get("rank") for i in inds) != list(range(1, len(inds) + 1)):
        fail("industry ranks are not 1..n")
    bad = sorted({i.get("label") for i in inds} - set(LABELS))
    if bad:
        fail(f"unknown industry labels {bad}")
    edge = (state.get("sell_edge") or {}).get("value")
    if not isinstance(edge, (int, float)) or not math.isfinite(edge):
        fail("sell edge is missing or not a finite number")
    w = state.get("window") or {}
    try:
        a, b, asof = (date.fromisoformat(w[k]) for k in ("a", "b", "prices_asof"))
    except (KeyError, TypeError, ValueError):
        fail("window dates missing or malformed")
    if not a < b <= asof:
        fail(f"window dates out of order: {a} / {b} / {asof}")
    peers = state.get("peers") or []
    if len(peers) < min_peers:
        fail(f"{len(peers)} S&P 500 peers, expected at least {min_peers}")
    for p in peers:
        if not (p.get("code") and all(isinstance(p.get(k), (int, float)) and p[k] > 0 for k in ("shares", "capA", "capB"))):
            fail(f"peer {p.get('ticker')!r} lacks an industry or a positive share count / cap")
    if not isinstance(state.get("classes"), Mapping):
        fail("classes missing")
    return state


def market_state_inputs(state: Mapping[str, Any]) -> dict[str, Any]:
    """A market-state file as ``check_portfolio``'s market arguments.

    Returns ``industries`` and ``peers`` frames, ``codes`` (ticker → FF49 code),
    ``sell_edge``, ``sell_edge_period`` and the ``window`` dates ``(a, b)`` at which
    the caller must price the owner's stocks for ``window_prices``.
    """
    w = state["window"]
    return {
        "industries": pd.DataFrame(state["industries"]),
        "peers": pd.DataFrame(state["peers"]),
        "codes": {t: c.get("ff49") for t, c in state["classes"].items()},
        "sell_edge": float(state["sell_edge"]["value"]),
        "sell_edge_period": state["sell_edge"].get("period", DEFAULT_SELL_EDGE_PERIOD),
        "window": (date.fromisoformat(w["a"]), date.fromisoformat(w["b"])),
    }


MAX_WINDOW_GAP_DAYS = 10    # a close older than this before a window date is no price for it


def close_on_or_before(closes: pd.Series, day: date, max_gap_days: int = MAX_WINDOW_GAP_DAYS) -> Optional[float]:
    """The last close on or before ``day`` (``closes`` indexed by trading date).

    The prototype forward-filled each stock's closes onto the window dates. A stock
    with no close in the ``max_gap_days`` before ``day`` (not yet listed, or a gap in
    the stored history) gets None, so it has no stock-vs-peers measure rather than a
    stale one.
    """
    s = closes.dropna()
    s = s[[d <= day for d in s.index]]
    if s.empty or (day - s.index[-1]).days > max_gap_days:
        return None
    v = float(s.iloc[-1])
    return v if v > 0 else None


def action_sort_key(action: str) -> float:
    """Where an action sorts in the report: sells first, buys last."""
    return SELL_AFTER_ORDER if action.startswith("Sell after") else ACTION_ORDER.get(action, 3)


def _clean(v: Any) -> Any:
    """A JSON-friendly scalar: numpy types unwrapped, NaN/NaT as None."""
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def check_portfolio(lots: Iterable[Mapping[str, Any]], industries: pd.DataFrame, peers: pd.DataFrame,
                    codes: Mapping[str, Optional[str]], window_prices: Mapping[str, tuple[float, float]],
                    *, today: date, sell_edge: float, sell_edge_period: str = DEFAULT_SELL_EDGE_PERIOD,
                    params: CheckParams = CheckParams()) -> dict[str, Any]:
    """The Portfolio Check for one owner's lots.

    ``lots``: one mapping per lot with ``ticker, shares, price`` and optional
    ``cost`` (total for the lot) and ``acquired`` (purchase date). ``industries``:
    ``industry_state``'s frame. ``peers``: ``peer_table``'s frame. ``codes``: ticker
    → FF49 code (None when unclassified). ``window_prices``: ticker → its prices
    ``WIN_A`` and ``WIN_B`` trading days back.

    Returns ``{"total_value", "holdings", "ideas", "action_counts"}``. Holdings are
    sorted sells first; ideas are Buy-zone industries the owner doesn't hold, with
    their three largest S&P 500 members; ``action_counts`` is how many holdings got
    each action, in the same order.
    """
    lots = pd.DataFrame(list(lots), columns=["ticker", "shares", "price", "cost", "acquired"])
    lots["ticker"] = lots["ticker"].astype(str).str.strip().str.upper()
    for col in ("shares", "price", "cost"):
        lots[col] = pd.to_numeric(lots[col], errors="coerce")
    lots["acquired"] = pd.to_datetime(lots["acquired"], errors="coerce")
    lots = lots[lots["shares"] > 0].reset_index(drop=True)
    lots["value"] = lots["shares"] * lots["price"]

    # A rank read back from JSON or a float column would print as "3.0" in the reasons.
    industries = industries.assign(rank=industries["rank"].astype(int))
    ind_by_code = industries.set_index("code")
    sp_rel = pd.Series({r.ticker: relative_wind(r.ticker, r.code, r.capA / r.shares, r.capB / r.shares, peers)[0]
                        for r in peers.itertuples()}, dtype=float).dropna()

    hold = lots.groupby("ticker").agg(shares=("shares", "sum"), value=("value", "sum"),
                                      cost=("cost", lambda s: s.sum(min_count=len(s)))).reset_index()
    total = hold["value"].sum()
    hold["weight"] = hold["value"] / total
    hold["code"] = hold["ticker"].map(lambda t: codes.get(t))
    hold["industry"] = hold["code"].map(lambda c: NAMES49.get(c, "Unclassified") if c else "Unclassified")
    hold["ind_weight"] = hold.groupby("code")["value"].transform("sum") / total
    sw = [relative_wind(t, c, *window_prices[t], peers) if t in window_prices and c else (np.nan, 0)
          for t, c in zip(hold["ticker"], hold["code"])]
    hold["sw"] = [w for w, _ in sw]
    hold["sw_peers"] = [n for _, n in sw]
    hold["sw_pct"] = hold["sw"].map(lambda w: (sp_rel < w).mean() if len(sp_rel) and pd.notna(w) else np.nan)

    holdings = []
    for _, h in hold.iterrows():
        row = ind_by_code.loc[h["code"]] if h["code"] in ind_by_code.index else None
        action, reasons, tax = decide(h, lots[lots.ticker == h["ticker"]].copy(), row, len(industries),
                                      params, today=today, sell_edge=sell_edge, sell_edge_period=sell_edge_period)
        holdings.append({
            "ticker": h["ticker"], "action": action,
            "industry_code": _clean(h["code"]), "industry": h["industry"],
            "industry_label": row["label"] if row is not None else None,
            "industry_rank": int(row["rank"]) if row is not None else None,
            "industry_pct": float(row["pct"]) if row is not None else None,
            "shares": _clean(h["shares"]), "value": _clean(h["value"]), "cost": _clean(h["cost"]),
            "weight": _clean(h["weight"]), "industry_weight": _clean(h["ind_weight"]),
            "vs_peers_12m": _clean(np.expm1(h["sw"])), "vs_peers_percentile": _clean(h["sw_pct"]),
            "peer_count": int(h["sw_peers"]),
            "reasons": reasons, "tax_note": tax,
        })
    holdings.sort(key=lambda r: (action_sort_key(r["action"]),
                                 r["industry_rank"] if r["industry_rank"] is not None else math.inf))

    ideas = []
    held_codes = set(hold["code"].dropna())
    big = peers.sort_values("cap", ascending=False)
    for _, r in industries[(industries.label == "Buy zone") & ~industries.code.isin(held_codes)].iterrows():
        top = big[big.code == r["code"]].head(3)
        ideas.append({"industry": r["industry"], "industry_code": r["code"], "rank": int(r["rank"]),
                      "wind12": _clean(r["wind12"]),
                      "examples": [{"ticker": t, "name": n,
                                    "vs_peers_12m": _clean(np.expm1(sp_rel[t])) if t in sp_rel else None}
                                   for t, n in zip(top["ticker"], top["name"])]})
    counts: dict[str, int] = {}
    for r in holdings:                    # holdings are already in report order
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    return {"total_value": _clean(total), "holdings": holdings, "ideas": ideas,
            "action_counts": [{"action": a, "count": n} for a, n in counts.items()]}
