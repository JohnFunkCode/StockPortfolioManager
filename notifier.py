import json
import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from portfolio import portfolio as spm
import portfolio.money as money

class Notifier:
    def __init__(self, portfolio: spm.Portfolio):
        load_dotenv()
        self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        self.portfolio = portfolio
        self.notification = None

    
    def calculate_and_send_notifications(self):
        for stock in self.portfolio.stocks.values():
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


    def send_notifications(self, embed):
        notification_log_msg = f"{embed['embeds'][0]['title']}"

        log_path = 'notificaiton.log'

        already_logged = False
        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as log_file:
                already_logged = notification_log_msg in log_file.read()

        if already_logged:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Skipping duplicate notification.')
            return

        with open(log_path, 'a', encoding='utf-8') as log_file:
            log_file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {notification_log_msg}\n")
        results = requests.post(self.discord_webhook_url, json=embed)
        if 200 <= results.status_code < 300:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Notification sent successfully.')
        else:
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Failed to send notification. Status code: {results.status_code}, Response: {results.text}')
