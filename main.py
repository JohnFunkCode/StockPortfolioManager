#!/usr/bin/env python3

import csv
from datetime import datetime
import stock_portfolio_manager as spm
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np


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

    # # Plot portfolio starting value
    # labels = []
    # values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     total_value = (stock.purchase_price * stock.quantity).amount
    #     values.append(float(total_value))
    #
    # # Create pie chart
    # plt.figure(figsize=(8, 8))
    # plt.pie(values, labels=labels, autopct='%1.1f%%', startangle=140)
    # plt.title("Portfolio Distribution by Purchase Value")
    # plt.axis('equal')
    # plt.show()
    #
    # # Plot the current value of each stock
    # # Pie chart: Portfolio distribution by current value
    # labels = []
    # current_values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     current_value = stock.get_current_value()
    #     current_values.append(float(current_value.amount) if current_value else 0)
    #
    # plt.figure(figsize=(8, 8))
    # plt.pie(current_values, labels=labels, autopct='%1.1f%%', startangle=140)
    # plt.title("Portfolio Distribution by Current Value")
    # plt.axis('equal')
    # plt.show()

    # # Prepare data for both pie charts
    # labels = []
    # purchase_values = []
    # current_values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     purchase_value = (stock.purchase_price * stock.quantity).amount
    #     purchase_values.append(float(purchase_value))
    #     current_value = stock.get_current_value()
    #     current_values.append(float(current_value.amount) if current_value else 0)
    #
    # plt.figure(figsize=(16, 6))
    #
    # # Pie chart for purchase value
    # plt.subplot(1, 2, 1)
    # plt.pie(purchase_values, labels=labels, autopct='%1.1f%%', startangle=140)
    # plt.title("Portfolio Distribution by Purchase Value")
    # plt.axis('equal')
    #
    # # Pie chart for current value
    # plt.subplot(1, 2, 2)
    # plt.pie(current_values, labels=labels, autopct='%1.1f%%', startangle=140)
    # plt.title("Portfolio Distribution by Current Value")
    # plt.axis('equal')
    #
    # plt.tight_layout()
    # plt.show()

    # two pie charts with proportional radii
    # import numpy as np
    #
    # labels = []
    # purchase_values = []
    # current_values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     purchase_value = (stock.purchase_price * stock.quantity).amount
    #     purchase_values.append(float(purchase_value))
    #     current_value = stock.get_current_value()
    #     current_values.append(float(current_value.amount) if current_value else 0)
    #
    # # Calculate totals and proportional radii
    # total_purchase = sum(purchase_values)
    # total_current = sum(current_values)
    # max_total = max(total_purchase, total_current)
    # radius_purchase = np.sqrt(total_purchase / max_total) if max_total > 0 else 1
    # radius_current = np.sqrt(total_current / max_total) if max_total > 0 else 1
    #
    # plt.figure(figsize=(16, 6))
    #
    # # Pie chart for purchase value
    # plt.subplot(1, 2, 1)
    # plt.pie(purchase_values, labels=labels, autopct='%1.1f%%', startangle=140, radius=radius_purchase)
    # plt.title(f"Portfolio Distribution by Purchase Value\nTotal: ${total_purchase:,.2f}")
    # plt.axis('equal')
    #
    # # Pie chart for current value
    # plt.subplot(1, 2, 2)
    # plt.pie(current_values, labels=labels, autopct='%1.1f%%', startangle=140, radius=radius_current)
    # plt.title(f"Portfolio Distribution by Current Value\nTotal: ${total_current:,.2f}")
    # plt.axis('equal')
    #
    # plt.tight_layout()
    # plt.show()

    # # Overlay pie chart with proportional radii
    # labels = []
    # purchase_values = []
    # current_values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     purchase_value = (stock.purchase_price * stock.quantity).amount
    #     purchase_values.append(float(purchase_value))
    #     current_value = stock.get_current_value()
    #     current_values.append(float(current_value.amount) if current_value else 0)
    #
    # # Calculate totals and proportional radii
    # total_purchase = sum(purchase_values)
    # total_current = sum(current_values)
    # max_total = max(total_purchase, total_current)
    # radius_current = 1.0  # background (full size)
    # radius_purchase = np.sqrt(total_purchase / max_total) if max_total > 0 else 1  # foreground
    #
    # fig, ax = plt.subplots(figsize=(8, 8))
    #
    # # Plot current value (background)
    # wedges_current, _ = ax.pie(
    #     current_values,
    #     labels=None,
    #     autopct=None,
    #     startangle=140,
    #     radius=radius_current,
    #     colors=plt.cm.Blues(np.linspace(0.5, 1, len(current_values))),
    #     wedgeprops=dict(width=radius_current, alpha=0.5)
    # )
    #
    # # Plot purchase value (foreground)
    # wedges_purchase, texts, autotexts = ax.pie(
    #     purchase_values,
    #     labels=labels,
    #     autopct='%1.1f%%',
    #     startangle=140,
    #     radius=radius_purchase,
    #     colors=plt.cm.Oranges(np.linspace(0.5, 1, len(purchase_values))),
    #     wedgeprops=dict(width=radius_purchase, edgecolor='w')
    # )
    #
    # ax.set_title(
    #     f"Overlay: Initial Purchase (front, orange, total ${total_purchase:,.2f})\n"
    #     f"Current Value (back, blue, total ${total_current:,.2f})"
    # )
    # ax.axis('equal')
    # plt.tight_layout()
    # plt.show()
    #
    # # Stacked bar chart of purchase vs current value
    # import matplotlib.pyplot as plt
    # import numpy as np
    #
    # labels = []
    # purchase_values = []
    # current_values = []
    # for stock in portfolio.list_stocks():
    #     labels.append(f"{stock.name} ({stock.symbol})")
    #     purchase_value = (stock.purchase_price * stock.quantity).amount
    #     purchase_values.append(float(purchase_value))
    #     current_value = stock.get_current_value()
    #     current_values.append(float(current_value.amount) if current_value else 0)
    #
    # # Calculate gain/loss for stacking
    # gain_loss = [curr - purch for curr, purch in zip(current_values, purchase_values)]
    #
    # x = np.arange(len(labels))
    #
    # fig, ax = plt.subplots(figsize=(10, 6))
    # ax.bar(x, purchase_values, label="Purchase Value")
    # ax.bar(x, gain_loss, bottom=purchase_values, label="Gain/Loss (Current - Purchase)")
    #
    # ax.set_xticks(x)
    # ax.set_xticklabels(labels, rotation=45, ha="right")
    # ax.set_ylabel("Value ($)")
    # ax.set_title("Stock Purchase vs Current Value (Stacked Bar)")
    # ax.legend()
    # plt.tight_layout()
    # plt.show()
    import matplotlib.pyplot as plt
    import numpy as np

    labels = []
    purchase_values = []
    current_values = []
    for stock in portfolio.list_stocks():
        labels.append(f"{stock.name} ({stock.symbol})")
        purchase_value = (stock.purchase_price * stock.quantity).amount
        purchase_values.append(float(purchase_value))
        current_value = stock.get_current_value()
        current_values.append(float(current_value.amount) if current_value else 0)

    # Pie chart data
    total_purchase = sum(purchase_values)
    total_current = sum(current_values)
    max_total = max(total_purchase, total_current)
    radius_current = 1.0
    radius_purchase = np.sqrt(total_purchase / max_total) if max_total > 0 else 1

    # Bar chart data
    gain_loss = [curr - purch for curr, purch in zip(current_values, purchase_values)]
    x = np.arange(len(labels))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

    # Stacked bar chart (left)
    bars_purchase = ax1.bar(x, purchase_values, label="Purchase Value", color="orange")
    bars_gain_loss = ax1.bar(x, gain_loss, bottom=purchase_values, label="Gain/Loss (Current - Purchase)", color="green")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right")
    ax1.set_ylabel("Value ($)")
    ax1.set_title("Stock Purchase vs Current Value (Stacked Bar)")
    ax1.legend()

    # Add value labels inside the bars
    ax1.bar_label(bars_purchase, labels=[f"${v:,.2f}" for v in purchase_values], label_type="center", fontsize=10)
    ax1.bar_label(bars_gain_loss, labels=[f"${v:,.2f}" for v in gain_loss], label_type="center", fontsize=10)

    # Overlayed pie chart (right)
    wedges_current, _ = ax2.pie(
        current_values,
        labels=None,
        autopct=None,
        startangle=140,
        radius=radius_current,
        colors=plt.cm.Greens(np.linspace(0.5, 1, len(current_values))),
        wedgeprops=dict(width=radius_current, alpha=0.5)
    )
    wedges_purchase, texts, autotexts = ax2.pie(
        purchase_values,
        labels=labels,
        autopct='%1.1f%%',
        startangle=140,
        radius=radius_purchase,
        colors=plt.cm.Oranges(np.linspace(0.5, 1, len(purchase_values))),
        wedgeprops=dict(width=radius_purchase, edgecolor='w')
    )
    ax2.set_title(
        f"Overlay: Initial Purchase (front, orange, total ${total_purchase:,.2f})\n"
        f"Current Value (back, blue, total ${total_current:,.2f})"
    )
    ax2.axis('equal')

    plt.tight_layout()
    plt.show()