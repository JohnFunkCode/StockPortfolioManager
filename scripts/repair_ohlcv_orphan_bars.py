"""Remove the cross-written OHLCV bars the 2026-07 repair could not reach.

The 2026-06-30/07-02 misalignment wrote one ticker's download under another
symbol. ``repair_ohlcv_misalignment.py`` re-fetched each symbol and upserted it,
which corrected every date the real symbol traded. An upsert cannot remove a
row, so a stolen bar on a date the real symbol has *no* bar (its market was
closed, or it had not listed yet) was left behind: JCI's 341.10 on Memorial Day
is Stockholm's ASSA-B.ST. No later refresh can fix these either.

``ohlcv_orphan_bars_20261106.json`` lists the rows found by comparing every
watchlist symbol's stored bars with Yahoo's dates on 2026-11-06. Each row names
the symbol whose bar it really is. Why, and the evidence:
docs/proposals/ohlcv-orphan-bars-repair-plan.md

Dry run by default: it changes nothing, reports each row's state and when it was
ingested, and lists any other identical bars the manifest doesn't cover.

    python scripts/repair_ohlcv_orphan_bars.py
    python scripts/repair_ohlcv_orphan_bars.py --apply --confirm-target HOST:PORT/NAME

Targets whatever QUANTCORE_DB_DSN points at, read from the environment or from
the .env beside quantcore/ when quantcore.db is imported (a worktree has none, so
set it there). ``--apply`` needs ``--confirm-target``
to name that database, and removes nothing unless every row still holds the
manifest's values or is already gone.
"""
from __future__ import annotations

import argparse
import calendar
import collections
import datetime as dt
import json
import math
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quantcore.db import describe_dsn, get_connection  # noqa: E402

MANIFEST = Path(__file__).with_name("ohlcv_orphan_bars_20261106.json")

MATCH = "match"      # still holds the manifest's values: safe to remove
GONE = "gone"        # already removed
CHANGED = "changed"  # something rewrote it since the manifest: stop and look

# `close` and `volume` are REAL (float4), so an exact comparison would call an
# untouched row changed. The manifest's closes also came through the API's rounding
# to 4 decimals, which at a low price is more than the float4 noise: 19.943926 is
# stored for a manifest 19.9439.
_REL_TOLERANCE = 1e-6
_CLOSE_ROUNDING = 5e-5


def bar_ts(date_str: str) -> int:
    """Midnight UTC of the bar's date: how both ohlcv writers key a daily bar."""
    return calendar.timegm(dt.date.fromisoformat(date_str).timetuple())


def load_manifest(path: Path = MANIFEST) -> list[dict]:
    return json.loads(path.read_text())


def classify(expected: dict, stored) -> str:
    """Compare one manifest row with what the table holds now (None = no row)."""
    if stored is None:
        return GONE
    same_close = math.isclose(
        float(stored["close"]), expected["close"],
        rel_tol=_REL_TOLERANCE, abs_tol=_CLOSE_ROUNDING,
    )
    same_volume = math.isclose(float(stored["volume"]), expected["volume"], rel_tol=_REL_TOLERANCE)
    return MATCH if same_close and same_volume else CHANGED


def read_rows(conn, rows: list[dict]) -> list[tuple[dict, object, str]]:
    """``(manifest row, stored row or None, state)`` for every manifest row."""
    plan = []
    for row in rows:
        stored = conn.execute(
            "SELECT close, volume, ingested_at FROM ohlcv "
            "WHERE symbol = %s AND interval = '1d' AND ts = %s",
            (row["symbol"], bar_ts(row["date"])),
        ).fetchone()
        plan.append((row, stored, classify(row, stored)))
    return plan


def apply_plan(conn, plan: list[tuple[dict, object, str]]) -> int:
    """Delete the MATCH rows in one transaction. Refuses if any row CHANGED.

    Each DELETE also binds the ``ingested_at`` just read, so a refresh landing
    between the read and the delete leaves its row alone and fails the count.
    """
    changed = [row for row, _, state in plan if state == CHANGED]
    if changed:
        raise RuntimeError(f"{len(changed)} row(s) changed since the manifest; nothing removed")
    removed = 0
    for row, stored, state in plan:
        if state != MATCH:
            continue
        cursor = conn.execute(
            "DELETE FROM ohlcv WHERE symbol = %s AND interval = '1d' AND ts = %s "
            "AND ingested_at = %s",
            (row["symbol"], bar_ts(row["date"]), stored["ingested_at"]),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(f"{row['symbol']} {row['date']} moved during the repair; rolled back")
        removed += 1
    conn.commit()
    return removed


def identical_bars_outside(conn, rows: list[dict]) -> list:
    """Bars byte-identical across symbols that the manifest doesn't account for.

    The manifest covers the watchlist; ``ohlcv`` also holds symbols looked up ad
    hoc. Report only: a duplicate isn't proof of which side is wrong.
    """
    known = {(r["symbol"], bar_ts(r["date"])) for r in rows}
    groups = conn.execute(
        """
        SELECT interval, ts, array_agg(symbol ORDER BY symbol) AS symbols
        FROM ohlcv
        WHERE volume > 0
        GROUP BY interval, ts, open, high, low, close, volume
        HAVING COUNT(*) > 1
        ORDER BY ts, interval
        """
    ).fetchall()
    # One symbol outside the manifest is the listed rows' real source, so it needs
    # no report; two or more means a pair nobody has looked at.
    return [
        g for g in groups
        if sum((symbol, g["ts"]) not in known for symbol in g["symbols"]) > 1
    ]


def report(plan: list[tuple[dict, object, str]], extras: list) -> None:
    states = collections.Counter(state for _, _, state in plan)
    print(f"manifest rows: {len(plan)}  " + "  ".join(f"{k}={v}" for k, v in sorted(states.items())))
    days = collections.Counter(
        dt.datetime.fromtimestamp(stored["ingested_at"], dt.timezone.utc).date().isoformat()
        for _, stored, _ in plan if stored is not None
    )
    print("ingested on:", dict(sorted(days.items())))
    for row, stored, state in plan:
        if state == CHANGED:
            print(f"  CHANGED {row['symbol']} {row['date']}: manifest close {row['close']}, "
                  f"stored {stored['close']}")
    print(f"identical bars outside the manifest: {len(extras)}")
    for group in extras:
        day = dt.datetime.fromtimestamp(group["ts"], dt.timezone.utc).date()
        print(f"  {day} {group['interval']}: {', '.join(group['symbols'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="remove the rows (default: dry run)")
    parser.add_argument("--confirm-target", help="host:port/name the --apply is meant for")
    args = parser.parse_args()

    target = describe_dsn()
    print(f"target: {target}  mode: {'APPLY' if args.apply else 'dry run'}")
    if args.apply and args.confirm_target != target:
        print(f"refusing: --confirm-target must be exactly {target}", file=sys.stderr)
        return 2

    rows = load_manifest()
    with closing(get_connection()) as conn:
        plan = read_rows(conn, rows)
        report(plan, identical_bars_outside(conn, rows))
        if not args.apply:
            return 1 if any(state == CHANGED for _, _, state in plan) else 0
        print(f"removed: {apply_plan(conn, plan)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
