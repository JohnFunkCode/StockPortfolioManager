import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from portfolio import stock_portfolio_manager as spm

embed = {
    "content": "This is a test message for Discord notification.",
    "embeds": [
        {
            "title": "Test Notification",
            "description": "This is a test description for the Discord notification.",
            "color": 5620992
        }
    ]
}

if __name__ == "__main__":
    load_dotenv()
    csv_file = os.environ.get("STOCK_FILE","sample_stocks.csv")
    discord_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")

    # Create a portfolio
    portfolio = spm.Portfolio()

    # Get the directory where the script is located
    script_dir = Path(__file__).parent.parent

    # Read stocks from CSV file
    csv_file = script_dir / csv_file
    stocks=portfolio.read_stocks_from_csv(csv_file)

    # Update current prices
    portfolio.update_all_prices()

    s = portfolio.list_stocks()

    stock_info = ''
    for stock in s:
        stock_info += f"{stock.symbol}: {stock.get_current_value()}\n"

        # Create Discord embed with stock information
    embed = {
        "content": "Stock Portfolio Update",
        "embeds": [
            {
                "title": "Current Stock Values",
                "description": stock_info,
                "color": 5620992
            }
        ]
    }

    import yfinance as yf
    aapl = yf.Ticker("aapl")
    aapl_historical = aapl.history(period="30d", interval="1d")
    print(aapl_historical)
    five_day_average = aapl_historical['Close'].mean()
    five_day_return = (aapl_historical['Close'][-1] - aapl_historical['Close'][0]) / aapl_historical['Close'][0] * 100

    data = yf.download("AMZN AAPL GOOG", start="2017-01-01",
                       end="2017-04-30", group_by="ticker")
    print(data)

    # Calculate the average price for each stock in the data
    stock_info += "\nAverage Prices:\n"
    if isinstance(data.columns, pd.MultiIndex):  # Check if we have multi-index data
        for symbol in data.columns.levels[1]:  # Access second level of MultiIndex which contains ticker symbols
            avg_price = data['Close'][symbol].mean()
            stock_info += f"{symbol} Avg: ${avg_price:.2f}\n"
    else:
        # Handle single stock case
        avg_price = data['Close'].mean()
        stock_info += f"Avg: ${avg_price:.2f}\n"



    print(f"Previous 5 Day Average: {five_day_average}")
    print(f"Previous 5 Day Return: {five_day_return}%")

    results = requests.post(discord_webhook_url, json=embed)
    if 200 <= results.status_code < 300:
        print("Notification sent successfully.")
    else:
        print(f"Failed to send notification. Status code: {results.status_code}, Response: {results.text}")

    # Create a portfolio
    portfolio = spm.Portfolio()

