"""MarketReferenceGateway — the Portfolio Check's public reference data.

Architectural standard v2 §5.1: network access lives in a gateway. This one
owns the three non-Yahoo sources behind the Portfolio Check market state
(docs/proposals/portfolio-check-plan.md, step 2):

* **Kenneth French's data library**: the 49-industry portfolios (monthly
  value-weighted returns, number of firms, average firm size, annual
  book/market) and ``Siccodes49``, the SIC ranges that define each industry.
* **Wikipedia**: today's S&P 500 constituents.
* **SEC EDGAR**: each company's SIC code. SEC's fair-access policy requires a
  ``User-Agent`` naming who is calling, with a contact address, and at most
  10 requests a second; this gateway refuses SEC calls without one
  (``SEC_USER_AGENT``) and spaces them out. Never hard-code anyone's address.

Yahoo (share counts, closes) stays behind ``YFinanceGateway``. The parsers
are module-level pure functions so tests can feed them saved text.

Used only by ``scripts/refresh_portfolio_check_market_state.py``, an operator
run, not by any service at request time.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
from html.parser import HTMLParser
from typing import Callable, Optional

import numpy as np
import pandas as pd
import requests

FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FF49_URL = f"{FRENCH_BASE}/49_Industry_Portfolios_CSV.zip"
SIC49_URL = f"{FRENCH_BASE}/Siccodes49.zip"
SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Identifies the client to the non-SEC sources; carries no contact details.
DEFAULT_USER_AGENT = "QuantCore market-reference refresh"
SEC_MIN_INTERVAL_SECONDS = 0.12      # SEC fair access: at most 10 requests/second

# The four sections of the FF49 CSV the market state uses, by the title line
# that opens each one in Ken French's file.
FF49_SECTIONS = {
    "ret": "Average Value Weighted Returns -- Monthly",
    "nfirms": "Number of Firms in Portfolios",
    "size": "Average Firm Size",
    "bm": "Value-Weighted Average of BE/ME",   # annual, keyed by year
}
FF_MISSING = (-99.99, -999.0)


class SecUserAgentMissing(RuntimeError):
    """SEC EDGAR was called without a User-Agent naming the caller."""


# ------------------------------------------------------------- parsers ----
def parse_ff_section(text: str, title: str) -> pd.DataFrame:
    """One section of a Ken French CSV as a float frame indexed by period.

    The section starts at the line equal to ``title``; its next line is the
    header, and its rows run until the first line whose first field isn't a
    number. Ken French's missing-value codes become NaN.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == title)
    except StopIteration:
        raise ValueError(f"section not found in Ken French file: {title!r}") from None
    header = [c.strip() for c in lines[start + 1].split(",")[1:]]
    rows = []
    for line in lines[start + 2:]:
        parts = [p.strip() for p in line.split(",")]
        if not parts[0].isdigit():
            break
        rows.append(parts)
    df = pd.DataFrame([r[1:] for r in rows], index=[r[0] for r in rows], columns=header).astype(float)
    return df.replace(list(FF_MISSING), np.nan)


def parse_sic_ranges(text: str) -> list[tuple[int, int, str]]:
    """``Siccodes49.txt`` as ``(low, high, ff49 code)`` ranges, in file order."""
    ranges, code = [], None
    for line in text.splitlines():
        m = re.match(r"\s*\d+\s+(\w+)\s+", line)
        if m and not re.match(r"\s*\d{4}-\d{4}", line):
            code = m.group(1)
            continue
        m = re.match(r"\s*(\d{4})-(\d{4})", line)
        if m and code:
            ranges.append((int(m.group(1)), int(m.group(2)), code))
    return ranges


class _TableParser(HTMLParser):
    """Collects the cell text of the <table> with a given id (no lxml needed)."""

    def __init__(self, table_id: str):
        super().__init__(convert_charrefs=True)
        self._id, self._depth = table_id, 0
        self.rows: list[list[str]] = []
        self._cell: Optional[list[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._depth or dict(attrs).get("id") == self._id:
                self._depth += 1
        elif self._depth == 1 and tag == "tr":
            self.rows.append([])
        elif self._depth == 1 and tag in ("td", "th") and self.rows:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self._depth:
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_constituents_html(html: str) -> pd.DataFrame:
    """Wikipedia's S&P 500 table: ``ticker, name, sector, sub_industry, yahoo``.

    ``yahoo`` is the ticker as Yahoo spells it (``BRK.B`` → ``BRK-B``).
    """
    p = _TableParser("constituents")
    p.feed(html)
    rows = [r for r in p.rows if r]
    if not rows:
        raise ValueError("S&P 500 constituents table not found")
    header = rows[0]
    try:
        cols = {k: header.index(v) for k, v in (("ticker", "Symbol"), ("name", "Security"),
                                                ("sector", "GICS Sector"), ("sub_industry", "GICS Sub-Industry"))}
    except ValueError as exc:
        raise ValueError(f"unexpected S&P 500 table header: {header}") from exc
    width = max(cols.values()) + 1
    t = pd.DataFrame([{k: r[i] for k, i in cols.items()} for r in rows[1:] if len(r) >= width])
    t["yahoo"] = t["ticker"].str.replace(".", "-", regex=False)
    return t


# ------------------------------------------------------------- gateway ----
class MarketReferenceGateway:
    def __init__(self, sec_user_agent: str | None = None, session: requests.Session | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 timeout: float = 60.0):
        self._sec_ua = (sec_user_agent if sec_user_agent is not None
                        else os.environ.get("SEC_USER_AGENT", "")).strip()
        self._session = session or requests.Session()
        self._sleep, self._clock, self._timeout = sleep, clock, timeout
        self._last_sec = None

    @property
    def has_sec_user_agent(self) -> bool:
        return bool(self._sec_ua)

    def _get(self, url: str, headers: dict) -> requests.Response:
        resp = self._session.get(url, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp

    def _french_file(self, url: str) -> str:
        resp = self._get(url, {"User-Agent": DEFAULT_USER_AGENT})
        z = zipfile.ZipFile(io.BytesIO(resp.content))
        return z.read(z.namelist()[0]).decode("latin1")

    def ff49_tables(self) -> dict[str, pd.DataFrame]:
        """The FF49 ``ret`` (percent), ``nfirms``, ``size`` and annual ``bm`` tables."""
        text = self._french_file(FF49_URL)
        return {key: parse_ff_section(text, title) for key, title in FF49_SECTIONS.items()}

    def ff49_sic_ranges(self) -> list[tuple[int, int, str]]:
        """The SIC code ranges that define each FF49 industry."""
        return parse_sic_ranges(self._french_file(SIC49_URL))

    def sp500_constituents(self) -> pd.DataFrame:
        """Today's S&P 500 members (``ticker, name, sector, sub_industry, yahoo``)."""
        return parse_constituents_html(self._get(SP500_URL, {"User-Agent": DEFAULT_USER_AGENT}).text)

    # --- SEC EDGAR ---
    def _sec_get(self, url: str) -> requests.Response:
        if not self._sec_ua:
            raise SecUserAgentMissing(
                "SEC EDGAR requires a User-Agent naming the caller with a contact address; "
                "set SEC_USER_AGENT (e.g. 'QuantCore refresh <name> <email>')")
        if self._last_sec is not None:
            wait = SEC_MIN_INTERVAL_SECONDS - (self._clock() - self._last_sec)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._session.get(url, headers={"User-Agent": self._sec_ua}, timeout=self._timeout)
        finally:
            self._last_sec = self._clock()

    def sec_company_tickers(self) -> dict[str, int]:
        """Ticker → SEC CIK for every company SEC lists."""
        resp = self._sec_get(SEC_TICKERS_URL)
        resp.raise_for_status()
        return {v["ticker"].upper(): int(v["cik_str"]) for v in json.loads(resp.text).values()}

    def sec_sic(self, cik: int) -> dict:
        """``{"sic", "sic_desc", "name"}`` for one company; ``sic`` None when SEC has none."""
        resp = self._sec_get(SEC_SUBMISSIONS_URL.format(cik=int(cik)))
        if resp.status_code in (403, 429):
            # Blocked or rate-limited: stop the refresh rather than quietly
            # recording a company as unclassified.
            resp.raise_for_status()
        if not resp.ok:
            return {"sic": None, "sic_desc": "", "name": ""}
        j = resp.json()
        return {"sic": int(j["sic"]) if j.get("sic") else None,
                "sic_desc": j.get("sicDescription", "") or "", "name": j.get("name", "") or ""}
