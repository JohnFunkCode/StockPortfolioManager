"""Pure news-sentiment aggregation (issue #411) — no I/O.

The single home for the news-sentiment rules every caller shares:

- ``net_score`` — (positive − negative) ÷ articles, the number every trend shows.
- ``classify_flip`` — what counts as a sentiment flip. The Discord alert
  (``notifier.check_sentiment_flips``) applies it to consecutive snapshots and
  the weekly trend to consecutive weeks; the unit differs, the rule must not.
- ``daily_counts`` / ``weekly_buckets`` — articles bucketed by their **Eastern**
  calendar date. The old SQL grouped on the UTC date of
  ``COALESCE(published_at, fetched_at)`` but windowed on ``fetched_at``, so an
  old article fetched recently landed in a bucket outside the window. Here the
  same date decides both.
- ``window_sentiment`` — whether the news over a fundamental score move's own
  window agreed with the move (issue #433), on the window's net score.

``article_count`` is capped by the collector (~10 per symbol per run), so it
measures collection, not news volume: use it to flag a bucket as thin, never as
a volume series.
"""
from __future__ import annotations

import datetime
from typing import Iterable, Optional

import pytz

from quantcore.analytics.market_time import ET

# A week with fewer articles than this is drawn as thin: a handful of headlines
# can swing its net score from one end to the other.
THIN_WEEK_ARTICLES = 10

IMPROVING = "improving"
DETERIORATING = "deteriorating"


def net_score(positive: int, negative: int, total: int) -> Optional[float]:
    """(positive − negative) ÷ total, rounded to 3 places; None with no articles."""
    if total <= 0:
        return None
    return round((positive - negative) / total, 3)


def majority_label(positive: int, negative: int, neutral: int) -> Optional[str]:
    """The most common label; ties go positive, then negative (``get_news``'s order)."""
    counts = {"positive": positive, "negative": negative, "neutral": neutral}
    if sum(counts.values()) == 0:
        return None
    return max(counts, key=lambda k: counts[k])


def classify_flip(prev_label: Optional[str], new_label: Optional[str]) -> Optional[str]:
    """``DETERIORATING`` / ``IMPROVING`` when the label flipped, else None.

    A change only counts when negative is on one side of it: positive ↔ neutral
    is noise, anything ↔ negative is a flip. Moving *to* negative deteriorates;
    moving away from it (to neutral or positive) improves.
    """
    if not prev_label or not new_label or prev_label == new_label:
        return None
    if "negative" not in (prev_label, new_label):
        return None
    return DETERIORATING if new_label == "negative" else IMPROVING


def article_date(published_at: Optional[str], fetched_at: Optional[str]) -> Optional[datetime.date]:
    """The Eastern calendar date an article belongs to.

    ``published_at`` when it parses, else ``fetched_at``; None when neither does.
    A naive stamp is read as UTC (what the collector writes).
    """
    for stamp in (published_at, fetched_at):
        parsed = _parse_stamp(stamp)
        if parsed is not None:
            return parsed.astimezone(ET).date()
    return None


def _parse_stamp(stamp: Optional[str]) -> Optional[datetime.datetime]:
    if not stamp:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = pytz.utc.localize(parsed)
    return parsed


def _tally(articles: Iterable[dict], start: datetime.date, end: datetime.date) -> dict:
    """{date: [positive, negative, neutral]} for scored articles dated start..end."""
    days: dict[datetime.date, list[int]] = {}
    index = {"positive": 0, "negative": 1, "neutral": 2}
    for art in articles:
        slot = index.get(art.get("sentiment"))
        if slot is None:
            continue
        day = article_date(art.get("published_at"), art.get("fetched_at"))
        if day is None or not start <= day <= end:
            continue
        days.setdefault(day, [0, 0, 0])[slot] += 1
    return days


def daily_counts(
    articles: Iterable[dict], *, start: datetime.date, end: datetime.date
) -> list[dict]:
    """Per-day counts for the days in start..end that have scored articles, oldest first.

    Same row shape the trend endpoint has always returned.
    """
    rows = []
    for day, (pos, neg, neu) in sorted(_tally(articles, start, end).items()):
        total = pos + neg + neu
        rows.append({
            "date":           day.isoformat(),
            "article_count":  total,
            "positive_count": pos,
            "negative_count": neg,
            "neutral_count":  neu,
            "net_score":      net_score(pos, neg, total),
        })
    return rows


def week_start(day: datetime.date) -> datetime.date:
    """The Monday of ``day``'s week."""
    return day - datetime.timedelta(days=day.weekday())


def _week_bucket(
    start: datetime.date,
    days: dict,
    *,
    today: datetime.date,
    history_start: Optional[datetime.date],
) -> dict:
    """One Monday-start week summed from the per-day tally, with its flags (no flip)."""
    end = start + datetime.timedelta(days=6)
    week = [days.get(start + datetime.timedelta(days=i), (0, 0, 0)) for i in range(7)]
    pos, neg, neu = (sum(day[k] for day in week) for k in range(3))
    total = pos + neg + neu
    return {
        "week_start":     start.isoformat(),
        "week_end":       end.isoformat(),
        "article_count":  total,
        "positive_count": pos,
        "negative_count": neg,
        "neutral_count":  neu,
        "net_score":      net_score(pos, neg, total),
        "label":          majority_label(pos, neg, neu),
        "thin":           0 < total < THIN_WEEK_ARTICLES,
        "in_progress":    start <= today <= end,
        "before_history": total == 0 and (history_start is None or end < history_start),
        "flip":           None,
    }


def weekly_buckets(
    articles: Iterable[dict],
    *,
    today: datetime.date,
    weeks: int,
    history_start: Optional[datetime.date],
) -> dict:
    """Every Monday-start week in the window ending with ``today``'s week, filled.

    A week with no articles is an explicit bucket (``article_count`` 0), so a
    reader never has to work out which weeks are missing. ``before_history``
    marks an empty week that ends before the symbol's first stored article —
    unmeasured, not quiet. ``flip`` compares each week with articles against the
    previous week with articles, by ``classify_flip``.
    """
    weeks = max(1, int(weeks))
    first = week_start(today) - datetime.timedelta(weeks=weeks - 1)
    days = _tally(articles, first, today)
    buckets = [
        _week_bucket(first + datetime.timedelta(weeks=i), days,
                     today=today, history_start=history_start)
        for i in range(weeks)
    ]

    labelled = [b for b in buckets if b["label"]]
    for prev, cur in zip(labelled, labelled[1:]):
        cur["flip"] = classify_flip(prev["label"], cur["label"])

    return {
        "weeks":               weeks,
        "history_start":       history_start.isoformat() if history_start else None,
        "weeks_with_articles": len(labelled),
        "total_articles":      sum(b["article_count"] for b in buckets),
        "thin_threshold":      THIN_WEEK_ARTICLES,
        "buckets":             buckets,
    }


# --- Score-change agreement (issue #433) -----------------------------------
#
# Whether the news over a fundamental score move's own window agreed with it.
# Decided on the window's net score, not its majority label: a busy window is
# usually majority neutral (WDC's week of Oct 12 on prod: 19 neutral, 14
# negative, net −0.38), so the label would rarely say anything. The net is
# read through a dead band so a handful of headlines either way is "mixed".

AGREES = "agrees"
DIVERGES = "diverges"
MIXED = "mixed"
THIN = "thin"
NO_NEWS = "none"

# |net| at or below this is mixed news, neither for nor against the move.
MIXED_NET_BAND = 0.10
# Fewer articles than this in the window can't carry a verdict.
MIN_WINDOW_ARTICLES = THIN_WEEK_ARTICLES
# Nor can a window less than this share of which news history covers. A
# quarter, not a half: prod's history starts 2026-09-30 while score windows
# start in August, so at a half every mover would read thin until mid-November.
MIN_WINDOW_COVERAGE = 0.25


def news_tone(net: Optional[float]) -> Optional[str]:
    """``positive`` / ``negative`` outside the dead band, ``mixed`` inside it."""
    if net is None:
        return None
    if net > MIXED_NET_BAND:
        return "positive"
    if net < -MIXED_NET_BAND:
        return "negative"
    return MIXED


def _coverage_days(
    start: datetime.date, end: datetime.date, history_start: Optional[datetime.date]
) -> int:
    """Days of start..end on or after the symbol's first stored article."""
    if history_start is None or history_start > end:
        return 0
    return (end - max(start, history_start)).days + 1


def _verdict(tone: Optional[str], total: int, coverage: float, score_direction: str) -> str:
    if total == 0:
        return NO_NEWS
    if total < MIN_WINDOW_ARTICLES or coverage < MIN_WINDOW_COVERAGE:
        return THIN
    if tone == MIXED:
        return MIXED
    wanted = "positive" if score_direction == IMPROVING else "negative"
    return AGREES if tone == wanted else DIVERGES


def window_sentiment(
    articles: Iterable[dict],
    *,
    start: datetime.date,
    end: datetime.date,
    score_direction: str,
    history_start: Optional[datetime.date],
) -> dict:
    """The news over one score move's window (start..end, Eastern dates) and its verdict.

    ``verdict`` is ``agrees`` / ``diverges`` / ``mixed`` / ``thin`` / ``none``.
    Coverage counts every day since news history began, quiet or uncollected:
    without a per-run collection log the two can't be told apart (#372).
    """
    days = _tally(articles, start, end).values()
    pos = sum(d[0] for d in days)
    neg = sum(d[1] for d in days)
    neu = sum(d[2] for d in days)
    total = pos + neg + neu
    net = net_score(pos, neg, total)
    tone = news_tone(net)
    window_days = (end - start).days + 1
    covered = _coverage_days(start, end, history_start)
    return {
        "verdict":        _verdict(tone, total, covered / window_days, score_direction),
        "tone":           tone,
        "net_score":      net,
        "article_count":  total,
        "positive_count": pos,
        "negative_count": neg,
        "neutral_count":  neu,
        "days_covered":   covered,
        "window_days":    window_days,
    }


def window_rule() -> dict:
    """The thresholds behind ``window_sentiment``'s verdict, for a reader's legend."""
    return {
        "mixed_net_band": MIXED_NET_BAND,
        "min_articles":   MIN_WINDOW_ARTICLES,
        "min_coverage":   MIN_WINDOW_COVERAGE,
    }
