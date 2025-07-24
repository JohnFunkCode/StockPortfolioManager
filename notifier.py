import json
import os
from datetime import datetime

import requests
from dotenv import load_dotenv
from portfolio import stock_portfolio_manager as spm
import portfolio.money as money

class Notifier:
    def __init__(self, portfolio: spm.Portfolio):
        load_dotenv()
        self.discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        self.portfolio = portfolio
        self.notification = None

    def calculate_and_send_notifications(self):
        for stock in self.portfolio.stocks.values():
            # moving_avg = money.Money(stock.metrics.five_day_moving_average, 'USD')
            if stock.current_price.amount < stock.metrics.fifty_day_moving_average and \
                stock.current_price.amount < stock.metrics.thirty_day_return :
                embed = {
                    "content": f"Stock Warning: {stock.symbol}",
                    "embeds": [
                        {
                            "title": f"{stock.name} ({stock.symbol})",
                            "description": f"Current Price: {stock.current_price}\n"
                                           f"5-Day Moving Average: {stock.metrics.five_day_moving_average:.2f}%",
                            "color": 16776960  # Yellow color for alert
                        }
                    ]
                }
                self.send_notifications(embed)

            if stock.current_price.amount < stock.purchase_price.amount:
                loss_percentage = stock.calculate_gain_loss_percentage()
                embed = {
                    "content": f"Stock Warning: {datetime.now()::%Y-%m-%d %H:%M:%S} {stock.symbol}",
                    "embeds": [
                        {
                            "title": f"{stock.name} ({stock.symbol})",
                            "description": f"Current Price: {stock.current_price}\n"
                                           f"Purchase Price: {stock.purchase_price}\n"
                                           f"{stock.calculate_gain_loss_percentage():.1f}% or {stock.calculate_gain_loss()} Loss",
                            "color": 16711680  # Red color for alert
                        }
                    ]
                }
                self.send_notifications(embed)



    def send_notifications(self, embed):
        results = requests.post(self.discord_webhook_url, json=embed)
        if 200 <= results.status_code < 300:
            print("Notification sent successfully.")
        else:
            print(f"Failed to send notification. Status code: {results.status_code}, Response: {results.text}")
