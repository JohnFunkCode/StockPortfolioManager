import json
import os
import sys
from pathlib import Path
from datetime import date, datetime

import requests
import yfinance as yf
from dotenv import load_dotenv
from portfolio import portfolio as spm
import portfolio.money as money
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAST_MCP_DIR = PROJECT_ROOT / "fastMCPTest"
if str(FAST_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_MCP_DIR))

from experiments.HarvesterPlanStore import HarvesterPlanDB
from options_position_store import (
    OptionsPositionStore,
    ALERT_ITM,
    ALERT_EXPIRATION_1D,
    ALERT_EXPIRATION_7D,
    ALERT_PROFIT_TARGET,
)

class Notifier:
    def __init__(
        self,
        portfolio: spm.Portfolio,
        harvester_db_path: str | None = None,
        options_db_path: str | None = None,
    ):
        load_dotenv()
        self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        # if not self.discord_webhook_url:
        #     raise ValueError("DISCORD_WEBHOOK_URL environment variable not set.")
        self.portfolio = portfolio
        self.notification = None
        self._options_positions = OptionsPositionStore(
            Path(options_db_path) if options_db_path else None
        )

    
    def calculate_and_send_notifications(self):
        harvester_db = HarvesterPlanDB()
        for stock in self.portfolio.stocks.values():
            hits = harvester_db.harvest_hit_for_symbol(
                symbol=stock.symbol,
                current_price=float(stock.current_price.amount),
            )
            if hits:
                rung_ids = [hit["rung_id"] for hit in hits if hit.get("rung_id") is not None]
                harvester_db.mark_rungs_achieved(
                    rung_ids=rung_ids,
                    trigger_price=float(stock.current_price.amount),
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

            # Keep separate notification for price below purchase price
            if stock.current_price.amount < stock.purchase_price.amount:
                embed = {
                    "content": f"Stock Warning: {datetime.now():%Y-%m-%d %H:%M:%S} {stock.symbol}",
                    "embeds": [
                        {
                            "title": f"{stock.name} ({stock.symbol}) Loss Alert",
                            "description": f"Current Price: {stock.current_price}\n"
                                           f"Purchase Price: {stock.purchase_price}\n"
                                           f"{stock.calculate_gain_loss_percentage():.1f}% or {stock.calculate_gain_loss()} Loss",
                            "color": 16711680  # Red color for alert
                        }
                    ]
                }
                self.send_notifications(embed)

        self.check_options_alerts()


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
        # Auto-expire positions whose expiration date has already passed
        expired = self._options_positions.auto_expire_past_positions()
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

        active = self._options_positions.get_active_positions()
        if not active:
            return

        # Fetch live prices for all unique symbols in one batch
        symbols = list({p["symbol"] for p in active})
        current_prices: dict[str, float] = {}
        for sym in symbols:
            try:
                info = yf.Ticker(sym).fast_info
                price = getattr(info, "last_price", None)
                if price is not None and price > 0:
                    current_prices[sym] = float(price)
            except Exception:
                pass

        alerts = self._options_positions.get_pending_alerts(current_prices)

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


    def send_notifications(self, embed):
        notification_log_msg = f"{embed['embeds'][0]['title']}"

        log_path = 'notification.log'

        already_logged = False
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as log_file:
                already_logged = notification_log_msg in log_file.read()

        if already_logged:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Skipping duplicate notification.')
            return

        with open(log_path, 'a', encoding='utf-8') as log_file:
            log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {notification_log_msg}\n")
        if not self.discord_webhook_url:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Discord webhook URL not set. Skipping notification.')
            return
        results = requests.post(self.discord_webhook_url, json=embed)
        if 200 <= results.status_code < 300:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Notification sent successfully.')
        else:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Failed to send notification. Status code: {results.status_code}, Response: {results.text}')
