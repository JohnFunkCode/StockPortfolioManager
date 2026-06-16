#!/usr/bin/env python3
"""Refresh watchlist OHLCV/fundamental caches and generate a sortable HTML report."""

from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
FAST_MCP_DIR = ROOT / "fastMCPTest"
for _path in (ROOT, FAST_MCP_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from quantcore.services.registry import get_services  # noqa: E402
from quantcore.repositories.ohlcv_repository import get_history  # noqa: E402


SORT_TABLE_JS = """
const tableSortState = {};

function sortTable(table_id, column_number, is_numeric = false) {
  const table = document.getElementById(table_id);
  const tbody = table.tBodies[0];
  const headers = Array.from(table.tHead.rows[0].cells);
  const keyIndex = column_number;
  const existingState = tableSortState[table_id] || [];
  const existingKey = existingState.find((key) => key.column === keyIndex);
  const nextDir = existingKey && existingKey.dir === "asc" ? "desc" : "asc";

  tableSortState[table_id] = [
    { column: keyIndex, isNumeric: is_numeric, dir: nextDir },
    ...existingState.filter((key) => key.column !== keyIndex),
  ].slice(0, 3);

  const parseNumeric = (text) => {
    const cleaned = text.replace(/[$,%\\s]/g, "").replace(/,/g, "");
    const match = cleaned.match(/^(-?\\d+(?:\\.\\d+)?)([KMBT])?$/i);
    if (!match) {
      return NaN;
    }
    const value = parseFloat(match[1]);
    const suffix = (match[2] || "").toUpperCase();
    const multiplier = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[suffix] || 1;
    return value * multiplier;
  };

  const compareText = (left, right, key) => {
    const leftCell = left.cells[key.column];
    const rightCell = right.cells[key.column];
    const leftText = leftCell ? leftCell.textContent.trim() : "";
    const rightText = rightCell ? rightCell.textContent.trim() : "";

    let comparison;
    if (key.isNumeric) {
      const leftValue = parseNumeric(leftText);
      const rightValue = parseNumeric(rightText);
      const leftMissing = Number.isNaN(leftValue);
      const rightMissing = Number.isNaN(rightValue);

      if (leftMissing || rightMissing) {
        if (leftMissing && rightMissing) {
          comparison = leftText.localeCompare(rightText);
        } else {
          comparison = leftMissing ? 1 : -1;
        }
      } else {
        comparison = leftValue - rightValue;
      }
    } else {
      comparison = leftText.localeCompare(rightText);
    }

    return key.dir === "asc" ? comparison : -comparison;
  };

  const rows = Array.from(tbody.rows);
  rows.sort((left, right) => {
    for (const key of tableSortState[table_id]) {
      const comparison = compareText(left, right, key);
      if (comparison !== 0) {
        return comparison;
      }
    }
    return 0;
  });

  const fragment = document.createDocumentFragment();
  rows.forEach((row) => fragment.appendChild(row));
  tbody.appendChild(fragment);

  headers.forEach((header) => {
    if (!header.dataset.baseLabel) {
      header.dataset.baseLabel = header.textContent.trim();
    }
    header.textContent = header.dataset.baseLabel;
  });

  tableSortState[table_id].forEach((key, index) => {
    const header = headers[key.column];
    if (!header) {
      return;
    }
    const indicator = document.createElement("span");
    indicator.className = "sort-indicator";
    indicator.textContent = `${key.dir === "asc" ? "\\u25B2" : "\\u25BC"}${index + 1}`;
    header.appendChild(indicator);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("table thead th").forEach((header) => {
    header.dataset.baseLabel = header.textContent.trim();
  });
});
"""


def load_watchlist(path: Path) -> list[dict[str, Any]]:
    with path.open() as fh:
        rows = yaml.safe_load(fh) or []

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        key = symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def pct_return(history: pd.DataFrame, bars: int) -> float | None:
    if history.empty or len(history) <= bars:
        return None
    close = history["Close"].dropna()
    if len(close) <= bars:
        return None
    start = float(close.iloc[-bars - 1])
    end = float(close.iloc[-1])
    if start == 0:
        return None
    return (end - start) / start * 100


def latest_close(history: pd.DataFrame) -> float | None:
    if history.empty:
        return None
    close = history["Close"].dropna()
    return None if close.empty else float(close.iloc[-1])


def metric(score: dict[str, Any], name: str) -> dict[str, Any]:
    return (score.get("metric_scores") or {}).get(name) or {}


def fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_int(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_market_cap(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        cap = float(value)
    except (TypeError, ValueError):
        return "N/A"
    for suffix, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(cap) >= divisor:
            return f"{cap / divisor:.2f}{suffix}"
    return f"{cap:,.0f}"


def cls_for_pct(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    return "gain" if numeric >= 0 else "loss"


def collect_row(entry: dict[str, Any]) -> dict[str, Any]:
    symbol = str(entry["symbol"]).strip()
    name = str(entry.get("name", symbol)).strip()
    tags = ", ".join(str(tag) for tag in (entry.get("tags") or []) if tag)

    price_error = ""
    history = pd.DataFrame()
    try:
        history = get_history(symbol, "1d", 120)
    except Exception as exc:  # noqa: BLE001
        price_error = str(exc)

    fundamentals_error = ""
    profile: dict[str, Any] = {}
    try:
        profile = get_services().fundamentals.get_full_fundamental_profile(symbol)
    except Exception as exc:  # noqa: BLE001
        fundamentals_error = str(exc)

    score = profile.get("fundamental_score") or {}
    revenue = profile.get("revenue_growth") or {}
    eps = profile.get("earnings_acceleration") or {}
    earnings = profile.get("earnings_calendar") or {}

    return {
        "name": name,
        "symbol": symbol,
        "currency": entry.get("currency", ""),
        "tags": tags,
        "current_price": latest_close(history),
        "return_5d": pct_return(history, 5),
        "return_30d": pct_return(history, 30),
        "return_60d": pct_return(history, 60),
        "composite_score": score.get("composite_score"),
        "fundamental_label": score.get("fundamental_label"),
        "coverage": score.get("coverage"),
        "sector": score.get("sector"),
        "market_cap": score.get("market_cap"),
        "rev_cagr_3y": metric(score, "RevCAGR3Y").get("value"),
        "rev_cagr_score": metric(score, "RevCAGR3Y").get("score"),
        "rev_accel_score": metric(score, "RevAccel").get("score"),
        "op_margin_score": metric(score, "OpMargin3Y").get("score"),
        "fcf_margin_score": metric(score, "FCFMargin3Y").get("score"),
        "valuation_score": metric(score, "ValMetric").get("score"),
        "momentum_score": metric(score, "Mom12_1").get("score"),
        "revenue_trajectory": revenue.get("trajectory"),
        "earnings_date": earnings.get("earnings_date"),
        "eps_acceleration": eps.get("acceleration_label"),
        "price_error": price_error,
        "fundamentals_error": fundamentals_error,
    }


def td(text: Any, css_class: str = "") -> str:
    class_attr = f' class="{css_class}"' if css_class else ""
    return f"<td{class_attr}>{html.escape(str(text))}</td>"


def render_report(rows: list[dict[str, Any]], output: Path) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    headers = [
        ("Name", False),
        ("Symbol", False),
        ("Currency", False),
        ("Price", True),
        ("5 Day Return", True),
        ("30 Day Return", True),
        ("60 Day Return", True),
        ("Composite Score", True),
        ("Fundamental Label", False),
        ("Coverage", True),
        ("Sector", False),
        ("Market Cap", True),
        ("Rev CAGR 3Y", True),
        ("Rev CAGR Score", True),
        ("Rev Accel Score", True),
        ("Op Margin Score", True),
        ("FCF Margin Score", True),
        ("Valuation Score", True),
        ("Momentum Score", True),
        ("Revenue Trajectory", False),
        ("Earnings Date", False),
        ("EPS Acceleration", False),
        ("Tags", False),
        ("Errors", False),
    ]

    header_html = "\n".join(
        f'<th onclick="sortTable(\'watchlistFundamentals\',{idx}, {str(is_numeric).lower()})">{html.escape(label)}</th>'
        for idx, (label, is_numeric) in enumerate(headers)
    )

    body_rows = []
    for row in rows:
        errors = "; ".join(
            item for item in [row.get("price_error"), row.get("fundamentals_error")] if item
        )
        symbol = html.escape(str(row["symbol"]))
        symbol_link = (
            f'<a href="https://finance.yahoo.com/quote/{symbol}/" target="_blank" rel="noopener">{symbol}</a>'
        )
        cells = [
            td(row["name"]),
            f"<td>{symbol_link}</td>",
            td(row["currency"]),
            td(fmt_num(row["current_price"])),
            td(fmt_pct(row["return_5d"]), cls_for_pct(row["return_5d"])),
            td(fmt_pct(row["return_30d"]), cls_for_pct(row["return_30d"])),
            td(fmt_pct(row["return_60d"]), cls_for_pct(row["return_60d"])),
            td(fmt_int(row["composite_score"])),
            td(row.get("fundamental_label") or "N/A"),
            td(fmt_num(row.get("coverage"), 2)),
            td(row.get("sector") or "N/A"),
            td(fmt_market_cap(row.get("market_cap"))),
            td(fmt_pct(None if row.get("rev_cagr_3y") is None else row["rev_cagr_3y"] * 100)),
            td(fmt_int(row.get("rev_cagr_score"))),
            td(fmt_int(row.get("rev_accel_score"))),
            td(fmt_int(row.get("op_margin_score"))),
            td(fmt_int(row.get("fcf_margin_score"))),
            td(fmt_int(row.get("valuation_score"))),
            td(fmt_int(row.get("momentum_score"))),
            td(row.get("revenue_trajectory") or "N/A"),
            td(row.get("earnings_date") or "N/A"),
            td(row.get("eps_acceleration") or "N/A"),
            td(row.get("tags") or ""),
            td(errors or ""),
        ]
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    report = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Watchlist Returns and Fundamentals</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; color: #222; }}
    h1 {{ color: #333; margin-bottom: 0.25rem; }}
    .summary {{ background: #f5f5f5; border-radius: 5px; margin: 16px 0; padding: 12px 15px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 7px 8px; text-align: left; white-space: nowrap; }}
    th {{ background: #f2f2f2; cursor: pointer; position: sticky; top: 0; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    .gain {{ color: green; }}
    .loss {{ color: red; }}
    a {{ color: #0645ad; text-decoration: none; }}
    .sort-indicator {{ color: #555; font-size: 11px; margin-left: 4px; }}
  </style>
</head>
<body>
  <h1>Watchlist Returns and Fundamentals</h1>
  <div class="summary">
    <p><strong>Generated:</strong> {html.escape(generated_at)}</p>
    <p><strong>Rows:</strong> {len(rows)}</p>
    <p>Returns are calculated from cached daily closes. Fundamental fields come from the fundamentals cache after running the full profile analysis for each symbol.</p>
  </div>
  <div class="table-wrap">
    <table id="watchlistFundamentals">
      <thead>
        <tr>{header_html}</tr>
      </thead>
      <tbody>
        {''.join(body_rows)}
      </tbody>
    </table>
  </div>
  <script>
{SORT_TABLE_JS}
  </script>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watchlist", type=Path, default=ROOT / "watchlist.yaml")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "analysis results" / f"Watchlist_Returns_Fundamentals_{datetime.now():%Y-%m-%d}.html",
    )
    parser.add_argument(
        "--use-fresh-fundamentals",
        action="store_true",
        help="Disable fundamentals cache reads so every symbol is recomputed and cached.",
    )
    args = parser.parse_args()

    if args.use_fresh_fundamentals:
        os.environ["FUNDAMENTALS_CACHE_TTL_HOURS"] = "0"

    entries = load_watchlist(args.watchlist)
    rows = []
    total = len(entries)
    for idx, entry in enumerate(entries, 1):
        symbol = entry.get("symbol")
        print(f"[{idx}/{total}] {symbol}", flush=True)
        rows.append(collect_row(entry))

    rows.sort(
        key=lambda row: (
            row["composite_score"] is not None,
            row["composite_score"] if row["composite_score"] is not None else -999,
            row["return_30d"] if row["return_30d"] is not None else -999,
        ),
        reverse=True,
    )
    render_report(rows, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
