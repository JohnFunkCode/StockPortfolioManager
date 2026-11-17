"""Regenerate parity.json: the Market Casting prototype's Portfolio Check, frozen.

Runs the prototype's own ``portfolio_check.main()`` unchanged on its made-up
``holdings_example.csv``, wrapping the functions it calls so every input it
gathered (prices, SIC classes, industry state, S&P 500 peers) and every result
it produced (each holding's action, reasons and tax note; the ideas) is
recorded. ``tests/test_analytics_portfolio_check.py`` then requires
``quantcore.analytics.portfolio_check`` to reproduce those results from those
inputs. It also stores the tail of the Fama-French 49-industry tables, so the
industry state can be recomputed from raw data and checked too.

Not part of the test run (it needs the prototype, its data folder and the
network for Yahoo prices). Run it with the prototype's own interpreter::

    <prototype>/.venv/Scripts/python tests/fixtures/portfolio_check/make_fixture.py <prototype dir>

Only ever point it at made-up holdings: the fixture is committed.
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
FF_MONTHS = 72      # >= 61 for the 5-year wind, plus the previous review
BM_YEARS = 8


def main(proto_dir: str) -> None:
    proto = Path(proto_dir).resolve()
    sys.path.insert(0, str(proto))
    import pandas as pd
    import portfolio_check as p

    seen = {}

    def wrap(name):
        real = getattr(p, name)

        def spy(*args, **kwargs):
            out = real(*args, **kwargs)
            seen.setdefault(name, []).append((args, out))
            return out
        setattr(p, name, spy)

    for name in ("prices", "classify", "industry_state", "peer_table"):
        wrap(name)

    def capture_render(hold, ind, ideas, *args, **kwargs):
        seen["render"] = (hold, ind, ideas)
        return ""
    p.render_html = capture_render

    holdings = proto / "holdings_example.csv"
    today = date.today()
    with tempfile.TemporaryDirectory() as tmp:
        p.REPORTS = Path(tmp)                    # never overwrite a real report
        sys.argv = ["portfolio_check.py", str(holdings)]
        p.main()

    px = seen["prices"][0][1]
    pxf = px.ffill()
    last = pxf.iloc[-1]
    a_date, b_date = pxf.index[-p.WIN_A], pxf.index[-p.WIN_B]
    lots = p.read_holdings(holdings)
    tickers = sorted(lots["ticker"].unique())
    held_cls = next(out for (args, out) in seen["classify"] if list(args[0]) == tickers)
    ind, asof = seen["industry_state"][0][1]
    peers = seen["peer_table"][0][1]
    hold, _, ideas = seen["render"]
    lb = json.loads((proto / "data" / "label_backtest.json").read_text(encoding="utf-8"))

    def num(v):
        return None if pd.isna(v) else float(v)

    def ff_tail(name, n):
        df = pd.read_csv(proto / "data" / f"ff49_{name}.csv", index_col=0).tail(n)
        return {"index": [str(i) for i in df.index], "columns": list(df.columns),
                "data": [[num(v) for v in row] for row in df.to_numpy()]}

    fixture = {
        "generated": today.isoformat(),
        "source": "Market Casting prototype portfolio_check.py on holdings_example.csv (made-up)",
        "today": today.isoformat(),
        "sell_edge": lb["events"]["1931–2026"]["Sell"]["12"]["rel"],
        "lots": [{"ticker": r.ticker, "shares": float(r.shares), "cost": num(r.cost),
                  "acquired": None if pd.isna(r.acquired) else r.acquired.date().isoformat(),
                  "price": num(last.get(r.ticker))} for r in lots.itertuples()],
        "codes": {t: held_cls.get(t, {}).get("ff49") for t in tickers},
        "window_prices": {t: [num(pxf[t].get(a_date)), num(pxf[t].get(b_date))] for t in tickers if t in pxf},
        "peers": [{"ticker": r.ticker, "name": r.name, "code": r.code, "shares": float(r.shares),
                   "capA": float(r.capA), "capB": float(r.capB), "cap": num(r.cap)} for r in peers.itertuples()],
        "industry_asof": asof,
        "industries": [{k: (num(v) if k not in ("code", "industry", "label") else v) for k, v in row.items()}
                       for row in ind.to_dict("records")],
        "ff49": {"ret": ff_tail("ret", FF_MONTHS), "size": ff_tail("size", FF_MONTHS),
                 "nfirms": ff_tail("nfirms", FF_MONTHS), "bm": ff_tail("bm", BM_YEARS)},
        "expected": {
            "total_value": float(hold["value"].sum()),
            "holdings": [{"ticker": h["ticker"], "action": h["action"], "reasons": list(h["reasons"]),
                          "tax": h["tax"], "weight": num(h["weight"]), "ind_weight": num(h["ind_weight"]),
                          "value": num(h["value"]), "sw": num(h["sw"]), "sw_peers": int(h["sw_peers"]),
                          "sw_pct": num(h["sw_pct"]), "label": h["label"],
                          "ind_rank": None if pd.isna(h["ind_rank"]) else int(h["ind_rank"])}
                         for _, h in hold.iterrows()],
            "ideas": ideas,
        },
    }
    out = HERE / "parity.json"
    out.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e3:.0f} kB): {len(fixture['lots'])} lots, "
          f"{len(fixture['peers'])} peers, industries as of {asof}, today {today}")


if __name__ == "__main__":
    main(sys.argv[1])
