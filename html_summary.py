#!/usr/bin/env python3

import csv
from datetime import datetime
from portfolio import stock_portfolio_manager as spm
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import io
import base64
import webbrowser
import os

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

def fig_to_base64(fig):
    """Convert matplotlib figure to base64 string for HTML embedding"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    img_str = base64.b64encode(buf.read()).decode('utf-8')
    return img_str

def create_portfolio_html(portfolio):
    """Create HTML content for the portfolio"""
    # Get current date and time
    current_datetime = datetime.now().strftime("%Y-%m-%d at %-I:%M%p").lower()

    # Portfolio summary data
    total_investment = portfolio.get_total_investment()
    total_current_value = portfolio.get_total_current_value()
    total_gain_loss = portfolio.get_total_gain_loss()
    total_gain_loss_pct = portfolio.get_total_gain_loss_percentage()

    # Prepare data for charts
    labels = []
    purchase_values = []
    current_values = []
    stock_details = []

    for stock in portfolio.list_stocks():
        labels.append(f"{stock.name} ({stock.symbol})")
        purchase_value = (stock.purchase_price * stock.quantity).amount
        purchase_values.append(float(purchase_value))
        current_value = stock.get_current_value()
        current_values.append(float(current_value.amount) if current_value else 0)

        gain_loss = stock.calculate_gain_loss()
        gain_loss_pct = stock.calculate_gain_loss_percentage()

        stock_details.append({
            'name': stock.name,
            'symbol': stock.symbol,
            'purchase_price': float(stock.purchase_price.amount),
            'current_price': float(stock.current_price.amount) if stock.current_price else "N/A",
            'quantity': stock.quantity,
            'gain_loss': float(gain_loss.amount) if gain_loss else "N/A",
            'gain_loss_pct': f"{gain_loss_pct:.2f}%" if gain_loss_pct is not None else "N/A"
        })

    # Create charts
    # Pie chart data
    total_purchase = sum(purchase_values)
    total_current = sum(current_values)
    max_total = max(total_purchase, total_current)
    radius_current = 1.0
    radius_purchase = np.sqrt(total_purchase / max_total) if max_total > 0 else 1

    # Bar chart data
    gain_loss_values = [curr - purch for curr, purch in zip(current_values, purchase_values)]
    x = np.arange(len(labels))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

    # Stacked bar chart (left)
    bars_purchase = ax1.bar(x, purchase_values, label="Purchase Value", color="orange")
    bars_gain_loss = ax1.bar(x, gain_loss_values, bottom=purchase_values, label="Gain/Loss (Current - Purchase)", color="green")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right")
    ax1.set_ylabel("Value ($)")
    ax1.set_title("Stock Purchase vs Current Value (Stacked Bar)")
    ax1.legend()

    # Add value labels inside the bars
    ax1.bar_label(bars_purchase, labels=[f"${v:,.2f}" for v in purchase_values], label_type="center", fontsize=10)
    ax1.bar_label(bars_gain_loss, labels=[f"${v:,.2f}" for v in gain_loss_values], label_type="center", fontsize=10)

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
        f"Current Value (back, green, total ${total_current:,.2f})"
    )
    ax2.axis('equal')

    plt.tight_layout()

    # Convert plots to base64 for embedding in HTML
    chart_img = fig_to_base64(fig)
    plt.close(fig)

    # Create HTML content
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Stock Portfolio Report</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h1, h2 {{ color: #333; }}
            .summary {{ background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .chart-container {{ margin: 20px 0; text-align: center; }}
            .gain {{ color: green; }}
            .loss {{ color: red; }}
        </style>
    </head>
    <body>
        <h1>Stock Portfolio Report Created on {current_datetime}</h1>
        
        <div class="summary">
            <h2>Portfolio Summary</h2>
            <p><strong>Total Investment:</strong> {total_investment}</p>
            <p><strong>Total Current Value:</strong> {total_current_value}</p>
            <p><strong>Total Gain/Loss:</strong> <span class="{'gain' if float(total_gain_loss.amount) >= 0 else 'loss'}">{total_gain_loss}</span></p>
            <p><strong>Total Gain/Loss %:</strong> <span class="{'gain' if total_gain_loss_pct >= 0 else 'loss'}">{total_gain_loss_pct:.2f}%</span></p>
        </div>
        
        <h2>Individual Stock Details</h2>
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Symbol</th>
                    <th>Purchase Price</th>
                    <th>Current Price</th>
                    <th>Quantity</th>
                    <th>Gain/Loss</th>
                    <th>Gain/Loss %</th>
                </tr>
            </thead>
            <tbody>
    """

    # Add rows for each stock
    for stock in stock_details:
        gain_loss_class = ""
        if stock['gain_loss'] != "N/A":
            gain_loss_class = "gain" if stock['gain_loss'] >= 0 else "loss"

        html_content += f"""
                <tr>
                    <td>{stock['name']}</td>
                    <td>{stock['symbol']}</td>
                    <td>${stock['purchase_price']:.2f}</td>
                    <td>${stock['current_price']:.2f}</td>
                    <td>{stock['quantity']}</td>
                    <td class="{gain_loss_class}">${stock['gain_loss']:.2f}</td>
                    <td class="{gain_loss_class}">{stock['gain_loss_pct']}</td>
                </tr>
        """

    html_content += """
            </tbody>
        </table>
        
        <div class="chart-container">
            <h2>Portfolio Visualization</h2>
            <img src="data:image/png;base64,""" + chart_img + """" alt="Portfolio Charts">
        </div>
    </body>
    </html>
    """

    return html_content

if __name__ == "__main__":
    # Create a portfolio
    portfolio = spm.Portfolio()

    # Get the directory where the script is located
    script_dir = Path(__file__).parent

    # Read stocks from CSV file
    csv_file = script_dir / "sample_stocks.csv"
    stocks = read_stocks_from_csv(csv_file)

    if not stocks:
        print("No stocks loaded. Please check the CSV file.")
        exit(1)

    # Add stocks to portfolio
    for stock in stocks:
        portfolio.add_stock(stock)

    # Update current prices
    portfolio.update_all_prices()

    # Create HTML report
    html_content = create_portfolio_html(portfolio)

    # Write HTML to file
    html_file = script_dir / "portfolio_report.html"
    with open(html_file, 'w') as f:
        f.write(html_content)

    # Open HTML in default browser
    webbrowser.open('file://' + os.path.abspath(html_file))

    print(f"Portfolio report generated and opened in your browser: {html_file}")