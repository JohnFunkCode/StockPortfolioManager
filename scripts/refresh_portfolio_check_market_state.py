#!/usr/bin/env python3
"""Rebuild the Portfolio Check's monthly market-state file.

The per-user Portfolio Check (docs/proposals/portfolio-check-plan.md) needs a
market picture that is the same for everyone and changes monthly: the 49
Fama-French industries' wind labels, the S&P 500 peers' market caps at the two
window dates, and each company's FF49 industry from its SEC SIC code. This
script fetches all of it and writes one reviewed JSON file,
``quantcore/data/portfolio_check_market_state.json`` by default:

* Ken French's data library: FF49 returns, firm counts, sizes, book/market, and
  the SIC ranges that define each industry
* Wikipedia: today's S&P 500 constituents
* Yahoo (through ``YFinanceGateway``): each constituent's share count and about
  14 months of daily closes
* SEC EDGAR: each company's SIC code

Usage:
    SEC_USER_AGENT="QuantCore refresh <name> <email>" \\
        python scripts/refresh_portfolio_check_market_state.py
    python scripts/refresh_portfolio_check_market_state.py --extra-tickers CRSP,ARWR,GH
    python scripts/refresh_portfolio_check_market_state.py --check      # validate the file, no network

SEC requires a User-Agent naming who is calling with a contact address; the
script stops without ``SEC_USER_AGENT``. ``--extra-tickers`` pre-classifies
holdings outside the S&P 500, so the service needn't call SEC for them.

Run monthly (Ken French publishes each month's data a few weeks after it ends),
then open a PR with the changed file. The new file is validated before it is
written, so a broken scrape or a Yahoo outage fails loudly instead of replacing
a good file. One Yahoo quote call per constituent and one SEC call per company:
a full run takes a few minutes. No database is touched.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from quantcore.analytics import portfolio_check as pc  # noqa: E402

DEFAULT_OUTPUT = ROOT / "quantcore" / "data" / "portfolio_check_market_state.json"
PRICE_PERIOD = "14mo"       # comfortably more than WIN_A (253) trading days
SHARE_WORKERS = 8


def _shares(yf_gateway, symbol: str) -> float:
    try:
        return float(yf_gateway.fast_info(symbol)["shares"])
    except Exception:  # a delisted or odd symbol: leave it out, as the prototype does
        return float("nan")


def _closes(yf_gateway, symbols: list[str]) -> pd.DataFrame:
    """Daily closes per Yahoo symbol, retrying the ones a first pass missed."""
    out, todo = {}, list(symbols)
    for _ in range(2):
        if not todo:
            break
        df = yf_gateway.download(todo, period=PRICE_PERIOD, auto_adjust=False)
        close = df["Close"] if df is not None and not df.empty and "Close" in df else pd.DataFrame()
        if isinstance(close, pd.Series):          # a one-symbol download
            close = close.to_frame(todo[0])
        for s in todo:
            if s in close and close[s].notna().sum() > pc.WIN_A:
                out[s] = close[s]
        todo = [s for s in todo if s not in out]
    if todo:
        print(f"No usable price history for {len(todo)} symbols: {', '.join(todo[:20])}", file=sys.stderr)
    return pd.DataFrame(out)


def _classes(reference, tickers: Iterable[str]) -> dict[str, dict]:
    ranges = reference.ff49_sic_ranges()
    ciks = reference.sec_company_tickers()
    out = {}
    for t in tickers:
        key = t.upper().replace(".", "-")
        cik = ciks.get(key) or ciks.get(key.replace("-", ""))
        info = reference.sec_sic(cik) if cik else {"sic": None, "sic_desc": "", "name": ""}
        out[t] = {**info, "ff49": pc.ff49_for_sic(info["sic"], ranges)}
    return out


def refresh(reference, yf_gateway, *, today, extra_tickers: Iterable[str] = ()) -> dict:
    """Fetch everything and build the market state (validated). No file is written."""
    tables = reference.ff49_tables()
    ret, cap, bm = pc.industry_inputs(tables["ret"], tables["size"], tables["nfirms"], tables["bm"])

    cons = reference.sp500_constituents()
    with ThreadPoolExecutor(SHARE_WORKERS) as ex:
        cons["shares"] = list(ex.map(lambda s: _shares(yf_gateway, s), cons["yahoo"]))
    closes = _closes(yf_gateway, cons["yahoo"].tolist())
    cons = cons[cons["yahoo"].isin(closes.columns) & cons["shares"].notna() & (cons["shares"] > 0)]
    cons = cons.reset_index(drop=True)

    tickers = sorted(set(cons["ticker"]) | {t.strip().upper() for t in extra_tickers if t.strip()})
    classes = _classes(reference, tickers)
    state = pc.build_market_state(ret, cap, bm, cons, closes[cons["yahoo"].tolist()], classes, generated=today)
    return pc.validate_market_state(state)


def write_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(state, indent=1, ensure_ascii=False, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


def summary(state: dict) -> str:
    labels = pd.Series([i["label"] for i in state["industries"]]).value_counts()
    unclassified = sum(1 for c in state["classes"].values() if not c.get("ff49"))
    return (f"industries as of {state['industries_asof']} ({', '.join(f'{n} {k}' for k, n in labels.items())}); "
            f"{len(state['peers'])} S&P 500 peers; window {state['window']['a']} → {state['window']['b']} "
            f"(prices to {state['window']['prices_asof']}); {len(state['classes'])} classified tickers, "
            f"{unclassified} without an industry; sell edge {state['sell_edge']['value']:+.2%} "
            f"({state['sell_edge']['period']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="where to write the file")
    parser.add_argument("--extra-tickers", default="",
                        help="comma-separated tickers outside the S&P 500 to pre-classify")
    parser.add_argument("--check", action="store_true",
                        help="validate the existing file and print its summary; no network")
    args = parser.parse_args(argv)

    if args.check:
        try:
            state = pc.validate_market_state(json.loads(args.output.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print(f"OK: {summary(state)}")
        return 0

    from quantcore.analytics.market_time import market_date
    from quantcore.gateways.market_reference_gateway import MarketReferenceGateway, SecUserAgentMissing
    from quantcore.gateways.yfinance_gateway import YFinanceGateway

    reference = MarketReferenceGateway()
    if not reference.has_sec_user_agent:
        # Before any download: otherwise the run spends minutes on Ken French,
        # Wikipedia and Yahoo only to stop at its first SEC call.
        print("FAIL: set SEC_USER_AGENT to a string naming you with a contact address "
              "(SEC EDGAR fair-access policy), e.g. 'QuantCore refresh <name> <email>'", file=sys.stderr)
        return 2
    try:
        state = refresh(reference, YFinanceGateway(), today=market_date(),
                        extra_tickers=args.extra_tickers.split(","))
    except SecUserAgentMissing as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    write_state(state, args.output)
    print(f"Wrote {args.output}: {summary(state)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
