import json
from decimal import Decimal
import os
import sys
from pathlib import Path
from datetime import date, datetime

import requests
from dotenv import load_dotenv
from portfolio import portfolio as spm
import portfolio.money as money
from portfolio.stock import format_quantity
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAST_MCP_DIR = PROJECT_ROOT / "fastMCPTest"
if str(FAST_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_MCP_DIR))

from quantcore.analytics.sentiment import classify_flip
from quantcore.services.registry import get_services
from quantcore.repositories.options_position_repository import (
    OptionsPositionStore,
    ALERT_ITM,
    ALERT_EXPIRATION_1D,
    ALERT_EXPIRATION_7D,
    ALERT_PROFIT_TARGET,
)

_PAPER_FILL_VERBS = {
    ("market", "buy"): "bought",
    ("market", "sell"): "sold",
    ("seed", "buy"): "seeded",
    ("manual_close", "sell"): "closed by hand",
}
_PAPER_PRICE_NOTES = {
    "session_open": ", the opening price",
    None: ", the last good price",
}
# A sale a stop made: at its level, or at the open when the stock opened below it.
_PAPER_STOP_PRICE_NOTES = {
    "session_open": ", the opening price, below its stop",
    None: ", its stop level",
}


def _money(value) -> str:
    """A price in dollars. Sub-dollar prices are kept to a hundredth of a cent."""
    price = Decimal(str(value))
    return f"${price:,.4f}" if price < 1 else f"${price:,.2f}"


def _paper_fill_line(fill: dict) -> str:
    """One paper fill in words: the book, what happened, and at what price."""
    verb = _PAPER_FILL_VERBS.get((fill.get("kind"), fill.get("side")), fill.get("side"))
    notes = _PAPER_PRICE_NOTES
    if fill.get("source") == "stop":
        verb, notes = "sold by its stop", _PAPER_STOP_PRICE_NOTES
    return (
        f"{fill['book_name']}: {verb} {format_quantity(fill['filled_shares'])} "
        f"{fill['symbol']} at {_money(fill['fill_price'])}{notes.get(fill.get('price_source'), '')}"
    )


def _stop_alert_line(stop: dict) -> str:
    """One real-book stop that a closed bar reached, in words."""
    level = _money(stop["stop_level"])
    kind = _stop_kind_words(stop)
    if stop["trigger_price"] < stop["stop_level"]:
        what = f"opened at {_money(stop['trigger_price'])}, below its stop at {level}"
    else:
        what = f"traded down to its stop at {level}"
    line = f"{stop['symbol']}: {what} ({kind}) on {stop['triggered_session']}"
    if stop.get("at_broker"):
        line += f". The stop order{_broker_words(stop)} should have filled: check it"
    return line


# How a broker's order type reads in a message (plan step 4.5).
_BROKER_ORDER_WORDS = {
    "stop": "stop",
    "stop_limit": "stop-limit",
    "trailing_stop": "trailing stop",
}


def _broker_words(stop: dict) -> str:
    """What the owner said about a stop's order at a broker, as words after
    "stop order": " at Schwab for 100 shares (stop-limit)", or "" when they
    gave none of it."""
    words = ""
    if stop.get("brokerage"):
        words += f" at {stop['brokerage']}"
    if stop.get("broker_quantity") is not None:
        words += f" for {format_quantity(stop['broker_quantity'])} shares"
    order_type = stop.get("broker_order_type")
    if order_type:
        words += f" ({_BROKER_ORDER_WORDS.get(order_type, order_type)})"
    return words


def _stop_kind_words(stop: dict) -> str:
    return (f"trailing {stop['trail_pct'].normalize():f}%"
            if stop["kind"] == "trailing" else "fixed")


def _stop_approach_line(stop: dict) -> str:
    """One real-book holding that closed near its stop, in words (plan
    step 4.4). `atr_distance` is worked out by the service."""
    return (
        f"{stop['symbol']}: closed at {_money(stop['approach_close'])} on "
        f"{stop['approached_session']}, {stop['atr_distance']:f} ATR above its stop at "
        f"{_money(stop['stop_level'])} ({_stop_kind_words(stop)})"
        + (f", placed as a stop order{_broker_words(stop)}" if stop.get("at_broker") else "")
    )


def _logged_title(line: str) -> str:
    """The title in a ``notification.log`` line ("<timestamp> - <title>")."""
    return line.rstrip("\n").partition(" - ")[2]


class Notifier:
    def __init__(
        self,
        portfolio: spm.Portfolio,
        harvester_db_path: str | None = None,
        options_db_path: str | None = None,
        owner: str = "john",
    ):
        load_dotenv()
        self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        # if not self.discord_webhook_url:
        #     raise ValueError("DISCORD_WEBHOOK_URL environment variable not set.")
        self.portfolio = portfolio
        self.notification = None
        self._options_positions = OptionsPositionStore()
        # Harvest plans are owned since #147 Part H1. The daily job runs on
        # John's portfolio, so that is the default; the argument exists so a
        # second owner's run scopes to their own ladders rather than his.
        self.owner = owner

    
    def calculate_and_send_notifications(self):
        harvester = get_services().harvester
        for stock in self.portfolio.stocks.values():
            hits = harvester.harvest_hit_for_symbol(
                symbol=stock.symbol,
                current_price=float(stock.current_price.amount),
                owner=self.owner,
            )
            if hits:
                rung_ids = [hit["rung_id"] for hit in hits if hit.get("rung_id") is not None]
                harvester.mark_rungs_achieved(
                    rung_ids=rung_ids,
                    trigger_price=float(stock.current_price.amount),
                    owner=self.owner,
                )
                self.send_harvest_alert(hits)

            # Track all moving average violations for each stock
            ma_violations = []

            # Check each moving average
            # if stock.current_price.amount < stock.metrics.ten_day_moving_average:
            #     ma_violations.append(f"10-Day Moving Average: {stock.metrics.ten_day_moving_average:.2f}")

            if stock.current_price.amount < stock.metrics.thirty_day_moving_average:
                ma_violations.append(f"30-Day Moving Average: {stock.metrics.thirty_day_moving_average:.2f}")

            if stock.current_price.amount < stock.metrics.fifty_day_moving_average:
                ma_violations.append(f"50-Day Moving Average: {stock.metrics.fifty_day_moving_average:.2f}")

            if stock.current_price.amount < stock.metrics.one_hundred_day_moving_average:
                ma_violations.append(f"100-Day Moving Average: {stock.metrics.one_hundred_day_moving_average:.2f}")

            if stock.current_price.amount < stock.metrics.two_hundred_day_moving_average:
                ma_violations.append(f"200-Day Moving Average: {stock.metrics.two_hundred_day_moving_average:.2f}")

            # Send a single consolidated alert if any moving averages are violated
            if ma_violations:
                violations_text = "\n".join(ma_violations)

                embed = {
                    "content": f"Stock Warning: {datetime.now():%Y-%m-%d %H:%M:%S} {stock.symbol}",
                    "embeds": [
                        {
                            "title": f"{stock.name} ({stock.symbol}) Moving Average Alert",
                            "description": f"Current Price: {stock.current_price}\n\n"
                                           f"Below the following moving averages:\n{violations_text}\n\n"
                                           f"[Investigate](https://finance.yahoo.com/chart/{stock.symbol})",
                            "color": 16776960  # Yellow color for alert
                        }
                    ]
                }
                self.send_notifications(embed)

            self._send_loss_alert(stock)

        self.check_options_alerts()
        self.check_sentiment_flips()

    def _send_loss_alert(self, stock) -> None:
        """Alert when the price is below the purchase price of any of the
        symbol's lots.

        Evaluated per lot, so a symbol with several purchase lots doesn't hide
        an underwater lot behind a profitable one (issue #126 Step 3.4). One
        alert per symbol either way: the dedup key in send_notifications() is
        the embed title, which is symbol + alert type, not lot. Every lot is
        given the symbol's current price, whether or not it is underwater.
        """
        lots = self.portfolio.lots.get(stock.symbol) or [stock]
        underwater_lots = []
        for lot in lots:
            lot.current_price = stock.current_price
            if (
                lot.purchase_price is not None
                and stock.current_price is not None
                and stock.current_price.amount < lot.purchase_price.amount
            ):
                underwater_lots.append(lot)

        if underwater_lots:
            lot_lines = [
                f"Purchased {lot.purchase_date} @ {lot.purchase_price} x {format_quantity(lot.quantity)}: "
                f"{lot.calculate_gain_loss_percentage():.1f}% or {lot.calculate_gain_loss()} Loss"
                for lot in underwater_lots
            ]
            embed = {
                "content": f"Stock Warning: {datetime.now():%Y-%m-%d %H:%M:%S} {stock.symbol}",
                "embeds": [
                    {
                        "title": f"{stock.name} ({stock.symbol}) Loss Alert",
                        "description": f"Current Price: {stock.current_price}\n\n"
                                       + "\n".join(lot_lines),
                        "color": 16711680  # Red color for alert
                    }
                ]
            }
            self.send_notifications(embed)

    # ------------------------------------------------------------------
    # Options position alerts (issue #12)
    # ------------------------------------------------------------------

    def check_options_alerts(self) -> None:
        """
        Fetch current prices for all active options positions, evaluate
        pending alerts (ITM, expiration, profit target), and send Discord
        notifications for each.  Deduplication is handled by send_notifications()
        via the notification.log file.
        """
        # Only this run's owner (#460). The webhook is that owner's channel, so
        # this must never widen to every owner: another owner's options alerts
        # posted here would be the cross-user leak the owner column prevents.
        # Auto-expire positions whose expiration date has already passed
        expired = self._options_positions.auto_expire_past_positions(owner=self.owner)
        for pos in expired:
            embed = self._build_options_embed(
                title=f"{pos['symbol']} ${pos['strike']:.2f} {pos['kind'].upper()} EXPIRED",
                description=(
                    f"Position {pos['position_id']} — {pos['contracts']} contract(s)  "
                    f"Strike: ${pos['strike']:.2f}  Expiration: {pos['expiration']}\n"
                    f"Cost: ${pos['purchase_price'] * 100 * pos['contracts']:.0f}  "
                    f"Status: expired worthless\n\n"
                    f"[{pos['symbol']} chart](https://finance.yahoo.com/chart/{pos['symbol']})"
                ),
                color=0x808080,  # Grey for expired
                symbol=pos["symbol"],
            )
            self.send_notifications(embed)

        active = self._options_positions.get_active_positions(owner=self.owner)
        if not active:
            return

        # Fetch live prices for all unique symbols in one batch
        symbols = list({p["symbol"] for p in active})
        current_prices: dict[str, float] = {}
        # Prices via the services layer (issue #76) — adapters never import
        # yfinance; the notifier runs in-process with main.py (Rule 6).
        from quantcore.services.registry import get_services

        prices_service = get_services().prices
        for sym in symbols:
            price = prices_service.get_fast_price(sym)
            if price is not None:
                current_prices[sym] = price

        alerts = self._options_positions.get_pending_alerts(
            current_prices, owner=self.owner
        )

        _COLORS = {
            ALERT_ITM:           0x00FF00,  # Green  — in the money
            ALERT_EXPIRATION_1D: 0xFF0000,  # Red    — expires tomorrow/today
            ALERT_EXPIRATION_7D: 0xFFA500,  # Orange — expires within 7 days
            ALERT_PROFIT_TARGET: 0x00BFFF,  # Blue   — profit target reached
        }

        _TITLES = {
            ALERT_ITM:           "Options Alert: In The Money",
            ALERT_EXPIRATION_1D: "Options Alert: Expiring Very Soon",
            ALERT_EXPIRATION_7D: "Options Alert: Expiration Warning",
            ALERT_PROFIT_TARGET: "Options Alert: Profit Target Reached",
        }

        for alert in alerts:
            sym         = alert["symbol"]
            kind        = alert["kind"].upper()
            strike      = alert["strike"]
            expiry      = alert["expiration"]
            contracts   = alert["contracts"]
            cur_price   = alert["current_price"]
            intrinsic   = alert["intrinsic_value"]
            roi         = alert["roi_pct"]
            days        = alert["days_to_expiry"]
            total_cost  = alert["total_cost"]
            total_val   = alert["total_intrinsic"]
            alert_type  = alert["alert_type"]

            # Include today's date in expiration alert titles so the same alert
            # fires once per day rather than being suppressed after the first fire.
            today_str = date.today().isoformat()
            if alert_type in (ALERT_EXPIRATION_1D, ALERT_EXPIRATION_7D):
                title = f"{sym} ${strike:.2f} {kind} — {_TITLES[alert_type]} [{today_str}]"
            elif alert_type == ALERT_ITM:
                title = f"{sym} ${strike:.2f} {kind} — {_TITLES[alert_type]}"
            else:
                title = f"{sym} ${strike:.2f} {kind} — {_TITLES[alert_type]}"

            description = (
                f"**{contracts} contract(s)  |  Strike: ${strike:.2f}  |  Exp: {expiry}**\n\n"
                f"Current Price:   ${cur_price:.2f}\n"
                f"Intrinsic Value: ${intrinsic:.2f}/share\n"
                f"Est. P&L:        ${total_val:.0f} vs cost ${total_cost:.0f}\n"
                f"ROI:             {roi:+.0f}%\n"
                f"Days to Expiry:  {days}\n\n"
                f"{alert['message']}\n\n"
                f"[{sym} chart](https://finance.yahoo.com/chart/{sym})"
            )

            embed = self._build_options_embed(
                title=title,
                description=description,
                color=_COLORS.get(alert_type, 0xFFFFFF),
                symbol=sym,
            )
            self.send_notifications(embed)

    def _build_options_embed(
        self,
        title: str,
        description: str,
        color: int,
        symbol: str,
    ) -> dict:
        return {
            "content": f"Options Alert: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                }
            ],
        }

    # ------------------------------------------------------------------
    # Sentiment flip alerts
    # ------------------------------------------------------------------

    def check_sentiment_flips(self) -> None:
        """
        For each portfolio stock, compare the two most recent FinBERT sentiment
        snapshots.  If the overall_sentiment changed (positive ↔ negative, or
        anything ↔ negative), fire a Discord alert.

        Requires at least two stored snapshots per symbol; silently skips
        symbols with fewer entries or if sentiment_store is unavailable.
        """
        try:
            from quantcore.repositories.sentiment_repository import SentimentStore
            store = SentimentStore()
        except Exception:
            return  # non-fatal — sentiment store may not be installed

        symbols = list(self.portfolio.stocks.keys())
        for symbol in symbols:
            latest = store.get_latest(symbol)
            prior  = store.get_prior(symbol)

            if not latest or not prior:
                continue

            new_sentiment  = latest.get("overall_sentiment")
            prev_sentiment = prior.get("overall_sentiment")

            # The one flip rule, shared with the Signals-tab trend (#411):
            # a change counts only when negative is on one side of it.
            if classify_flip(prev_sentiment, new_sentiment) is None:
                continue

            direction_emoji = "🔴" if new_sentiment == "negative" else "🟢"
            color = 0xFF4444 if new_sentiment == "negative" else 0x00CC66

            pos   = latest.get("positive_count", 0)
            neg   = latest.get("negative_count", 0)
            neu   = latest.get("neutral_count",  0)
            total = latest.get("scored_count",   0)
            ts    = latest.get("captured_at", "")[:10]

            embed = {
                "content": f"Sentiment Flip: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
                "embeds": [
                    {
                        "title": (
                            f"{direction_emoji} {symbol} News Sentiment Flip: "
                            f"{prev_sentiment.upper()} → {new_sentiment.upper()}"
                        ),
                        "description": (
                            f"**Latest snapshot ({ts}):** "
                            f"{pos} positive · {neg} negative · {neu} neutral "
                            f"({total} articles scored)\n\n"
                            f"{'⚠️ Negative news flow may signal emerging headwinds.' if new_sentiment == 'negative' else '✅ Positive news flow may support bullish momentum.'}\n\n"
                            f"[View {symbol} chart](https://finance.yahoo.com/chart/{symbol})"
                        ),
                        "color": color,
                    }
                ],
            }
            self.send_notifications(embed)

    def send_harvest_alert(self, hits: list[dict]) -> None:
        if not hits:
            return

        symbol = hits[0]["symbol"]
        instance_id = hits[0].get("instance_id")
        current_price = hits[0].get("current_price")

        target_prices = [hit.get("target_price") for hit in hits if hit.get("target_price") is not None]
        min_target = min(target_prices) if target_prices else None
        max_target = max(target_prices) if target_prices else None

        price_line = ""
        if current_price is not None and min_target is not None:
            if max_target is not None and max_target != min_target:
                price_line = (
                    f"Current Price: {current_price:.2f}\n"
                    f"Target Range: {min_target:.2f} - {max_target:.2f}\n\n"
                )
            else:
                price_line = f"Current Price: {current_price:.2f}\nTarget Price: {min_target:.2f}\n\n"

        rung_lines = []
        for hit in hits:
            rung_id = hit.get("rung_id")
            rung_index = hit.get("rung_index")
            target_price = hit.get("target_price")
            shares_to_sell = hit.get("shares_to_sell")
            rung_label = f"rung {rung_index}" if rung_index is not None else "rung"
            rung_id_text = f" (id {rung_id})" if rung_id is not None else ""
            target_text = f"{target_price:.2f}" if target_price is not None else "n/a"
            shares_text = f"{shares_to_sell}" if shares_to_sell is not None else "n/a"
            rung_lines.append(f"- {rung_label}{rung_id_text}: target {target_text}, shares {shares_text}")

        rungs_block = "\n".join(rung_lines)
        instance_line = f"Plan Instance: {instance_id}\n" if instance_id is not None else ""
        rung_count = len(hits)
        embed = {
            "content": f"Harvest Alert: {datetime.now():%Y-%m-%d %H:%M:%S} {symbol}",
            "embeds": [
                {
                    "title": f"{symbol} Harvest Points Reached ({rung_count})",
                    "description": f"{price_line}{instance_line}Rungs Hit:\n{rungs_block}\n\n"
                                   f"[Investigate](https://finance.yahoo.com/chart/{symbol})",
                    "color": 65280  # Green color for harvest alert
                }
            ]
        }
        self.send_notifications(embed)


    def send_empty_watchlist_alert(self, portfolio_symbol_count: int) -> None:
        """Last-resort alarm for an empty watchlist table (issue #83).

        The watchlist moved from a file in the container image to the database,
        and there is deliberately no fallback to watchlist.yaml when the table
        reads back empty (plan decision 7) — a silent fallback would re-hide
        exactly the persistence failure this issue is about. So the empty case
        has to be loud instead: the report and the portfolio-symbol capture
        still run, but somebody needs to know the watchlist half is missing
        before the options open-interest history quietly develops a hole.

        The date is in the title on purpose: send_notifications() dedupes on the
        title against notification.log, and a condition that persists for a week
        should alarm on each of those days rather than only the first.
        """
        embed = {
            "content": f"Watchlist Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"Watchlist is empty ({date.today():%Y-%m-%d})",
                    "description": (
                        "The watchlist table returned no rows, so options-chain "
                        f"capture is running on the {portfolio_symbol_count} "
                        "portfolio symbol(s) only and the report has no "
                        "watchlist section.\n\n"
                        "Re-seed it with `python scripts/import_watchlist.py`."
                    ),
                    "color": 16776960,  # Yellow — degraded, not failed
                }
            ]
        }
        self.send_notifications(embed)

    def send_stale_fundamentals_alert(
        self,
        coverage: float,
        stale_count: int,
        requested: int,
        oldest_age_hours: float | None,
    ) -> None:
        """Last-resort alarm for a fundamentals cache the warmer isn't keeping up with.

        The warmer runs last in the daily job, under a wall-clock budget, and
        swallows its own failures so the notifications and options capture that
        already succeeded still decide the job's exit status. That is the right
        trade, but it means a warmer that silently does nothing looks exactly
        like a warmer that finished — and the fundamentals pages downstream
        would keep serving month-old scores with a fresh-looking timestamp on
        the row. So the degradation gets its own alarm: swallow the exception,
        then make the resulting staleness loud.

        The date is in the title on purpose: send_notifications() dedupes on the
        title against notification.log, and a cache that stays stale for a week
        should alarm on each of those days rather than only the first.
        """
        oldest_line = (
            f"Oldest entry: {oldest_age_hours:.0f}h old.\n"
            if oldest_age_hours is not None else ""
        )
        embed = {
            "content": f"Fundamentals Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"Fundamentals cache is stale ({date.today():%Y-%m-%d})",
                    "description": (
                        f"{stale_count} of {requested} symbol(s) are outside the "
                        f"cache TTL — {coverage:.0%} coverage.\n"
                        f"{oldest_line}\n"
                        "The nightly warmer is not keeping up. Check the job log "
                        "for warming errors, or raise "
                        "`FUNDAMENTALS_WARM_BUDGET_SECONDS`."
                    ),
                    "color": 16776960,  # Yellow — degraded, not failed
                }
            ]
        }
        self.send_notifications(embed)

    def send_capture_gap_alert(self, problems: list[str]) -> None:
        """Last-resort alarm for options-capture datasets that fell short.

        The capture steps swallow their own failures so the job's notifications
        are never held hostage, which means a night that captured nothing exits
        0 like any other. This is what makes that visible. The date is in the
        title on purpose: send_notifications() dedupes on the title, and a gap
        that lasts a week should alarm each day.
        """
        embed = {
            "content": f"Capture Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"Daily options capture has gaps ({date.today():%Y-%m-%d})",
                    "description": "\n".join(f"- {p}" for p in problems),
                    "color": 16776960,  # Yellow — degraded, not failed
                }
            ]
        }
        self.send_notifications(embed)

    def send_news_gap_alert(self, problems: list[str]) -> None:
        """Last-resort alarm for the nightly news/sentiment collection job.

        Same shape and same reason as ``send_capture_gap_alert``: the job
        swallows per-symbol failures, so a night that collected nothing still
        exits 0. The date is in the title so a multi-day gap alarms daily.
        """
        embed = {
            "content": f"News Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"Daily news collection has gaps ({date.today():%Y-%m-%d})",
                    "description": "\n".join(f"- {p}" for p in problems),
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_open_bars_alert(self, bar_count: int, symbols: list, symbol_count: int) -> None:
        """Alarm that daily bars from earlier sessions are still ``OPEN`` after
        the nightly close-out (#518). ``symbols`` is a sample, not the full list."""
        sample = ", ".join(symbols)
        more = f" and {symbol_count - len(symbols)} more" if symbol_count > len(symbols) else ""
        embed = {
            "content": f"OHLCV Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": "Daily bars left open after the close-out",
                    "description": (
                        f"- {bar_count} daily bar(s) from earlier sessions are still OPEN, "
                        f"across {symbol_count} symbol(s): {sample}{more}.\n"
                        "- Rankings and screens leave OPEN bars out, so these days are "
                        "missing from them until a re-fetch closes the bars."
                    ),
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_snapshots_owed_alert(self, book_count: int, reasons: dict) -> None:
        """Alarm that books are still owed a complete value snapshot for today
        after the nightly step (#431 step 5.1). ``reasons`` counts them by
        why: ``missing`` (no row), ``incomplete`` (a holding with no close or
        no FX rate), ``stale`` (a holding priced from an earlier session) or
        ``restate`` (a later change to its history is still to be rebuilt,
        #431 step 5.2).
        Names no book or owner: books are private and the channel is shared."""
        words = {
            "missing": "have no snapshot",
            "incomplete": "could not be valued: a holding has no close or no FX rate",
            "stale": "priced a holding from an earlier session's close",
            "restate": "have a change to their history still to restate; the next run retries it",
        }
        lines = [
            f"- {count} book(s) {words.get(reason, reason)}."
            for reason, count in sorted(reasons.items())
        ]
        embed = {
            "content": f"Book Snapshot Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": "Book value snapshots incomplete",
                    "description": (
                        f"{book_count} book(s) have no complete value for today's close.\n"
                        + "\n".join(lines)
                        + "\n- Performance has a hole on this day until it is filled."
                    ),
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_queued_orders_alert(
        self, order_count: int, symbols: list, symbol_count: int, oldest_session: str
    ) -> None:
        """Alarm that paper orders whose session has come are still pending
        after the daily job's fill step (#431). ``symbols`` is a sample. No
        owner is named: the channel is shared."""
        sample = ", ".join(symbols)
        more = f" and {symbol_count - len(symbols)} more" if symbol_count > len(symbols) else ""
        embed = {
            "content": f"[PAPER] Orders Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": "Queued paper orders were not filled",
                    "description": (
                        f"- {order_count} queued paper order(s) are still pending after "
                        f"the fill step, across {symbol_count} symbol(s): {sample}{more}.\n"
                        f"- The oldest was queued for the {oldest_session} session.\n"
                        "- An order waits when its session's daily bar hasn't arrived, and "
                        "is rejected after three sessions without one. Each fills at its "
                        "own session's open on the next run that finds the bar."
                    ),
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_unchecked_stops_alert(
        self, stop_count: int, symbols: list, symbol_count: int, oldest_check
    ) -> None:
        """Alarm that active stops were not tested against today's bar by the
        daily job's stop check (#431, #221). ``symbols`` is a sample;
        ``oldest_check`` is the oldest last-tested session among them, or None
        when one has never been tested. No owner is named: the channel is shared."""
        sample = ", ".join(symbols)
        more = f" and {symbol_count - len(symbols)} more" if symbol_count > len(symbols) else ""
        oldest = (f"The longest-unchecked was last tested against the {oldest_check} bar."
                  if oldest_check else "At least one has never been tested.")
        embed = {
            "content": f"Stops Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": "Stops were not checked against today's bar",
                    "description": (
                        f"- {stop_count} active stop(s) were not tested against today's "
                        f"closed bar, across {symbol_count} symbol(s): {sample}{more}.\n"
                        f"- {oldest}\n"
                        "- A stop that isn't checked can't fire. Each is tested against "
                        "every bar it missed on the next run that finds them."
                    ),
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_stop_alert(self, stops: list, stop_count: int, day: str) -> bool:
        """Alert that closed bars reached stops in the real book (#431, #221).

        ``stops`` are triggered stops, at most the number the message lists;
        ``stop_count`` is how many there were. Nothing was sold: a real-book
        stop is a record. Returns False when the message did not go out (no
        webhook is set, or Discord refused it), so the caller can leave the
        alert owed.
        """
        lines = [f"- {_stop_alert_line(stop)}" for stop in stops]
        if stop_count > len(stops):
            lines.append(f"- and {stop_count - len(stops)} more, named on the next run")
        embed = {
            "content": f"Stop Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"{stop_count} stop(s) reached, reported {day}",
                    "description": (
                        "\n".join(lines)
                        + "\nChecked against closed daily bars. Nothing was recorded as "
                        "sold: sell at your broker, or check the fill of a stop order "
                        "placed there, then record the sale."
                    ),
                    "color": 16711680,
                }
            ]
        }
        # The database records which stops were announced, so the title must
        # not decide whether this goes out (see send_notifications).
        return self.send_notifications(embed, dedupe=False) is not False

    def send_stop_approach_alert(
        self, stops: list, stop_count: int, day: str, multiple
    ) -> bool:
        """Alert that holdings in the real book closed near their stops (plan
        step 4.4, #221), the evening before a likely breach.

        ``stops`` are those stops, nearest first, at most the number the
        message lists; ``stop_count`` is how many there were; ``multiple`` is
        the approach distance in ATRs. Returns False when the message did not
        go out, so the caller can leave the alert owed.
        """
        lines = [f"- {_stop_approach_line(stop)}" for stop in stops]
        if stop_count > len(stops):
            lines.append(f"- and {stop_count - len(stops)} more, named on the next run")
        embed = {
            "content": f"Stop Approach Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"{stop_count} holding(s) closed near their stop, reported {day}",
                    "description": (
                        "\n".join(lines)
                        + f"\nNear means a close within {multiple} ATR (14-session) above "
                        "the stop. The stops have not been reached: each is checked "
                        "against the next closed bar."
                    ),
                    "color": 16753920,
                }
            ]
        }
        # As for send_stop_alert: the database decides, not the title.
        return self.send_notifications(embed, dedupe=False) is not False

    def send_paper_fills(self, fills: list, fill_count: int, day: str) -> None:
        """Report the day's paper fills (#431), marked ``[PAPER]``.

        ``fills`` are filled orders with their ``book_name``, at most the
        number the message lists; ``fill_count`` is how many there were. Sent
        to ``PAPER_DISCORD_WEBHOOK_URL`` when that is set, otherwise to the
        usual webhook.
        """
        lines = [f"- {_paper_fill_line(fill)}" for fill in fills]
        if fill_count > len(fills):
            lines.append(f"- and {fill_count - len(fills)} more")
        embed = {
            "content": f"[PAPER] Fills: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": f"[PAPER] {fill_count} paper fill(s) on {day}",
                    "description": (
                        "\n".join(lines)
                        + "\nSimulated trades in paper books. No real order was placed."
                    ),
                    "color": 3447003,
                }
            ]
        }
        self.send_notifications(
            embed, webhook_url=os.environ.get("PAPER_DISCORD_WEBHOOK_URL") or None
        )

    def send_news_stale_alert(self, age_hours, ceiling_hours: float) -> None:
        """Alarm, raised from the daily report Job, that no news has landed lately.

        Lives outside the news Job on purpose: a Job that never runs cannot
        report its own absence (#275).
        """
        if age_hours is None:
            detail = "No news articles have ever been collected."
        else:
            detail = (f"The newest stored article was fetched {age_hours:.0f}h ago "
                      f"(ceiling {ceiling_hours:.0f}h).")
        embed = {
            "content": f"News Alert: {datetime.now():%Y-%m-%d %H:%M:%S}",
            "embeds": [
                {
                    "title": "News collection looks stale",
                    "description": f"- {detail}\n- Check that the quantcore-news Job and its scheduler exist.",
                    "color": 16776960,
                }
            ]
        }
        self.send_notifications(embed)

    def send_notifications(self, embed, webhook_url=None, dedupe=True):
        """Send ``embed`` to Discord, at most once per title.

        A title already in ``notification.log`` is skipped and returns None;
        otherwise returns True when Discord took the message and False when it
        did not go out. The match is on the whole title, not a substring of
        one: "3 stop(s) reached" is not "23 stop(s) reached" (the #569 review).

        ``dedupe=False`` sends even when the title was logged before. A caller
        that records delivery itself (the stop alerts, in the database) passes
        it: a title such as "5 stop(s) reached, reported <day>" can name
        different stops on different runs, and skipping it would mark stops as
        announced that no message named.
        """
        notification_log_msg = f"{embed['embeds'][0]['title']}"

        log_path = 'notification.log'

        already_logged = False
        if dedupe and os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as log_file:
                already_logged = any(
                    _logged_title(line) == notification_log_msg for line in log_file
                )

        if already_logged:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Skipping duplicate notification.')
            return

        with open(log_path, 'a', encoding='utf-8') as log_file:
            log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {notification_log_msg}\n")
        webhook_url = webhook_url or self.discord_webhook_url
        if not webhook_url:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Discord webhook URL not set. Skipping notification.')
            # Nothing was sent, so a caller that tracks delivery must not
            # count this one as delivered.
            return False
        results = requests.post(webhook_url, json=embed)
        if 200 <= results.status_code < 300:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Notification sent successfully.')
            return True
        print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Failed to send notification. Status code: {results.status_code}, Response: {results.text}')
        # Discord answered and refused. Only a skipped duplicate returns None:
        # that message went out earlier.
        return False
