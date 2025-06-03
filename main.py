#!/usr/bin/env python3

import csv
from datetime import datetime
import stock_portfolio_manager as spm
from pathlib import Path

def read_stocks_from_csv(file_path):
    """
    Read stock data from a CSV file and return a list of Stock objects.

    Expected CSV format:
    name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price
    """
    stocks = []

    try:
        with open(file_path, 'r', newline='') as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                # Parse required fields
                name = row['name']
                symbol = row['symbol']
                purchase_price = float(row['purchase_price'])
                quantity = int(row['quantity'])
                purchase_date = datetime.strptime(row['purchase_date'], '%Y-%m-%d').date()

                # Parse optional fields
                currency = row.get('currency', 'USD')

                sale_price = None
                if row.get('sale_price') and row['sale_price'].strip():
                    sale_price = float(row['sale_price'])

                sale_date = None
                if row.get('sale_date') and row['sale_date'].strip():
                    sale_date = datetime.strptime(row['sale_date'], '%Y-%m-%d').date()

                current_price = None
                if row.get('current_price') and row['current_price'].strip():
                    current_price = float(row['current_price'])

                # Create Stock object
                stock = spm.Stock(
                    name=name,
                    symbol=symbol,
                    purchase_price=purchase_price,
                    quantity=quantity,
                    purchase_date=purchase_date,
                    currency=currency,
                    sale_price=sale_price,
                    sale_date=sale_date,
                    current_price=current_price
                )

                stocks.append(stock)

        return stocks

    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return []
    except (KeyError, ValueError) as e:
        print(f"Error parsing CSV data: {e}")
        return []

if __name__ == "__main__":
    # Create a portfolio
    portfolio = spm.Portfolio()

    # Get the directory where the script is located
    script_dir = Path(__file__).parent

    # Read stocks from CSV file
    csv_file = script_dir / "stocks.csv"
    stocks = read_stocks_from_csv(csv_file)

    if not stocks:
        print("No stocks loaded. Please check the CSV file.")
        exit(1)

    # Add stocks to portfolio
    for stock in stocks:
        portfolio.add_stock(stock)

    # Update current prices
    portfolio.update_all_prices()

    # Print portfolio information
    for stock in portfolio.list_stocks():
        print(f"\nStock: {stock.name} ({stock.symbol})")
        print(f"Purchase Price: {stock.purchase_price}")
        print(f"Current Price: {stock.current_price}")
        print(f"Quantity: {stock.quantity}")
        print(f"Gain/Loss: {stock.calculate_gain_loss()}")

        gain_loss_pct = stock.calculate_gain_loss_percentage()
        if gain_loss_pct is not None:
            print(f"Gain/Loss %: {gain_loss_pct:.2f}%")
        else:
            print("Gain/Loss %: N/A")

    print(f"\nTotal Invested: {portfolio.get_total_investment()}")
    print(f"Total Portfolio Current Value: {portfolio.get_total_current_value()}")
    print(f"Total Portfolio Gain/Loss: {portfolio.get_total_gain_loss()}")
    print(f"Total Portfolio Gain/Loss %: {portfolio.get_total_gain_loss_percentage():.2f}%")

    # Example of converting total values to another currency
    # print(f"\nPortfolio value in EUR: {portfolio.get_total_current_value('EUR')}")