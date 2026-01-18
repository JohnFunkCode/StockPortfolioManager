#!/usr/bin/env python3

from datetime import datetime
from portfolio import portfolio
from portfolio import watch_list
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import io
import base64
import webbrowser
import os
from jinja2 import Environment, FileSystemLoader
from notifier import Notifier
from dotenv import load_dotenv

def fig_to_base64(fig):
    """Convert matplotlib figure to base64 string for HTML embedding"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    img_str = base64.b64encode(buf.read()).decode('utf-8')
    return img_str

def create_portfolio_charts(portfolio):
    """Create charts for the portfolio and return as base64"""
    # Prepare data for charts
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
    gain_loss_values = [curr - purch for curr, purch in zip(current_values, purchase_values)]
    x = np.arange(len(labels))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

    # Stacked bar chart (left)
    bars_purchase = ax1.bar(x, purchase_values, label="Purchase Value", color="orange")

    # Split gain/loss into positive (green) and negative (red)
    gain_loss_pos = [max(v, 0) for v in gain_loss_values]
    gain_loss_neg = [min(v, 0) for v in gain_loss_values]

    bars_gain_loss_pos = ax1.bar(
        x,
        gain_loss_pos,
        bottom=purchase_values,
        label="Gain (positive)",
        color="green",
    )

    bars_gain_loss_neg = ax1.bar(
        x,
        gain_loss_neg,
        bottom=purchase_values,
        label="Loss (negative)",
        color="red",
    )

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right")
    ax1.set_ylabel("Value ($)")
    ax1.set_title("Stock Purchase vs Current Value (Stacked Bar)")
    ax1.legend(loc="upper right")

    # Add value labels inside the bars
    ax1.bar_label(
        bars_purchase,
        labels=[f"${v:,.2f}" for v in purchase_values],
        label_type="center",
        fontsize=10,
    )

    pos_labels = [f"${v:,.2f}" if v != 0 else "" for v in gain_loss_pos]
    neg_labels = [f"${v:,.2f}" if v != 0 else "" for v in gain_loss_neg]

    ax1.bar_label(bars_gain_loss_pos, labels=pos_labels, label_type="center", fontsize=10)
    ax1.bar_label(bars_gain_loss_neg, labels=neg_labels, label_type="center", fontsize=10)

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

    return chart_img, total_purchase, total_current

def create_portfolio_html(portfolio, watchlist):
    """Create HTML content for the portfolio using Jinja2 templates"""
    # Set up Jinja2 environment
    script_dir = Path(__file__).parent
    template_dir = script_dir / "templates"

    # Create templates directory if it doesn't exist
    template_dir.mkdir(exist_ok=True)

    env = Environment(loader=FileSystemLoader(template_dir))

    # Generate the template file if it doesn't exist
    template_path = template_dir / "portfolio_template.html"
    if not template_path.exists():
        create_template_file(template_path)

    template = env.get_template("portfolio_template.html")

    # Get current date and time
    current_datetime = datetime.now().strftime("%Y-%m-%d at %-I:%M%p").lower()

    # Portfolio summary data
    total_investment = portfolio.get_total_investment()
    total_current_value = portfolio.get_total_current_value()
    total_gain_loss = portfolio.get_total_gain_loss()
    total_gain_loss_pct = portfolio.get_total_gain_loss_percentage()
    total_dollars_per_day = portfolio.get_total_dollars_per_day()

    # Generate stock details
    stock_details = []
    for stock in portfolio.list_stocks():
        gain_loss = stock.calculate_gain_loss()
        gain_loss_pct = stock.calculate_gain_loss_percentage()
        dollars_per_day = stock.get_dollars_per_day()


        stock_details.append({
            'name': stock.name,
            'symbol': stock.symbol,
            'purchase_price': float(stock.purchase_price.amount),
            'current_price': float(stock.current_price.amount) if stock.current_price else "N/A",
            'quantity': stock.quantity,
            'gain_loss': float(gain_loss.amount) if gain_loss else "N/A",
            'gain_loss_pct': gain_loss_pct if gain_loss_pct is not None else "N/A",
            'days_held': (datetime.now().date() - stock.purchase_date).days,
            'dollars_per_day': float(dollars_per_day.amount) if dollars_per_day else "N/A",
            'ten_day_moving_average' : stock.metrics.ten_day_moving_average if stock.metrics else "N/A",
            'thirty_day_moving_average': stock.metrics.thirty_day_moving_average if stock.metrics else "N/A",
            'fifty_day_moving_average': stock.metrics.fifty_day_moving_average if stock.metrics else "N/A",
            'one_hundred_day_moving_average': stock.metrics.one_hundred_day_moving_average if stock.metrics else "N/A",
            'two_hundred_day_moving_average' : stock.metrics.two_hundred_day_moving_average if stock.metrics else "N/A",
            'percent_change_today': stock.metrics.percent_change_today if stock.metrics.percent_change_today else "N/A",
            'five_day_return': stock.metrics.five_day_return if stock.metrics else "N/A",
            'thirty_day_return': stock.metrics.thirty_day_return if stock.metrics else "N/A",
            'ninety_day_return': stock.metrics.ninety_day_return if stock.metrics else "N/A",
            'ytd_return': stock.metrics.ytd_return if stock.metrics else "N/A",
            'one_year_return': stock.metrics.one_year_return if stock.metrics else "N/A"
        })

    #generate watchlist data
    watchlist_details = []
    for stock in watchlist.list_stocks():
        watchlist_details.append({
            'name': stock.name,
            'symbol': stock.symbol,
            'current_price': float(stock.current_price.amount) if stock.current_price else "N/A",
            'ten_day_moving_average': stock.metrics.ten_day_moving_average if stock.metrics.ten_day_moving_average else "N/A",
            'thirty_day_moving_average': stock.metrics.thirty_day_moving_average if stock.metrics.thirty_day_moving_average else "N/A",
            'fifty_day_moving_average': stock.metrics.fifty_day_moving_average if stock.metrics.fifty_day_moving_average else "N/A",
            'one_hundred_day_moving_average': stock.metrics.one_hundred_day_moving_average if stock.metrics.one_hundred_day_moving_average else "N/A",
            'two_hundred_day_moving_average': stock.metrics.two_hundred_day_moving_average if stock.metrics.two_hundred_day_moving_average else "N/A",
            'percent_change_today': stock.metrics.percent_change_today if stock.metrics.percent_change_today else "N/A",
            'five_day_return': stock.metrics.five_day_return if stock.metrics.five_day_return else "N/A",
            'thirty_day_return': stock.metrics.thirty_day_return if stock.metrics.thirty_day_return else "N/A",
            'ninety_day_return': stock.metrics.ninety_day_return if stock.metrics.ninety_day_return else "N/A",
            'ytd_return': stock.metrics.ytd_return if stock.metrics.ytd_return else "N/A",
            'one_year_return': stock.metrics.one_year_return if stock.metrics.one_year_return else "N/A"
        })

    # Create charts
    chart_img, total_purchase, total_current = create_portfolio_charts(portfolio)

    # Render template with context data
    return template.render(
        current_datetime=current_datetime,
        total_investment=total_investment,
        total_current=total_current_value,
        total_gain_loss=total_gain_loss,
        total_gain_loss_pct=total_gain_loss_pct,
        total_dollars_per_day=total_dollars_per_day.amount,
        stock_details=stock_details,
        watchlist_details=watchlist_details,
        chart_img=chart_img,
        total_purchase=total_purchase
    )

def create_template_file(template_path):
    """Create the Jinja2 template file"""
    template_content = """

 <!DOCTYPE html>
 <html lang="en">
 <head>
     <meta charset="UTF-8">
     <meta name="viewport" content="width=device-width, initial-scale=1.0">
     <title>Stock Portfolio Report</title>
     <style>
         body { font-family: Arial, sans-serif; margin: 20px; }
         h1, h2 { color: #333; }
         .datetime { font-size: 0.6em; color: #666; font-weight: normal; }
         .summary { background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
         table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
         th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
         th { background-color: #f2f2f2; }
         tr:nth-child(even) { background-color: #f9f9f9; }
         .chart-container { margin: 20px 0; text-align: center; }
         .gain { color: green; }
         .loss { color: red; }
     </style>
 </head>
 <body>
     <h1>Stock Portfolio Report created on {{ current_datetime }}<span class="datetime"></span></h1>
 
     <div class="summary">
         <h2>Portfolio Summary</h2>
        <p><strong>Total Investment:</strong> {{ total_investment }}</p>
        <p><strong>Total Current Value:</strong> {{ total_current }}</p>
        <p><strong>Total Gain/Loss:</strong> 
            <span class="{% if total_gain_loss.amount >= 0 %}gain{% else %}loss{% endif %}">
                {{ total_gain_loss }}
            </span>
        </p>
        <p><strong>Total Gain/Loss %:</strong> 
            <span class="{% if total_gain_loss_pct >= 0 %}gain{% else %}loss{% endif %}">
                {{ "%.2f"|format(total_gain_loss_pct) }}%
            </span>
        </p>
        <p><strong>Dollars Per Day Gain/Loss:</strong>
            <span class="{% if total_dollars_per_day >= 0 %}gain{% else %}loss{% endif %}">
                ${{ "%.2f"|format(total_dollars_per_day) }}
            </span>
    </div>

    <h2>Individual Stock Holdings</h2>
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
                <th>Days Held</th>
                <th>Dollars per day</th>
                <th>10 day average price</th>
                <th>30 day average price</th>
                <th>50 day average price</th>
                <th>100 day average price</th>
                <th>200 day average price</th>
                <th>Today's Change</th>
                <th>5 Day Return</th>
                <th>30 day Return</th>
                <th>90 Day Return</th>
                <th>YTD Return</th>
                <th>1 Year Return</th>
            </tr>
        </thead>
        <tbody>
            {% for stock in stock_details %}
            <tr>
                <td>{{ stock.name }}</td>
                <td>{{ stock.symbol }}</td>
                <td>${{ "%.2f"|format(stock.purchase_price) }}</td>
                <td>
                    {% if stock.current_price != "N/A" %}
                        ${{ "%.2f"|format(stock.current_price) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>{{ stock.quantity }}</td>
                <td class="{% if stock.gain_loss != "N/A" %}{% if stock.gain_loss >= 0 %}gain{% else %}loss{% endif %}{% endif %}">
                    {% if stock.gain_loss != "N/A" %}
                        ${{ "%.2f"|format(stock.gain_loss) }}

                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td class="{% if stock.gain_loss != "N/A" %}{% if stock.gain_loss >= 0 %}gain{% else %}loss{% endif %}{% endif %}">
                    {% if stock.gain_loss_pct != "N/A" %}
                        {{ "%.2f"|format(stock.gain_loss_pct) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.days_held != "N/A" %}
                        {{ stock.days_held }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.dollars_per_day != "N/A" %}
                        ${{ "%.2f"|format(stock.dollars_per_day) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.ten_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.ten_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.thirty_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.thirty_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.fifty_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.fifty_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.one_hundred_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.one_hundred_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.two_hundred_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.two_hundred_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.percent_change_today != "N/A" %}
                        {{ "%.2f"|format(stock.percent_change_today) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.five_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.five_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.thirty_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.thirty_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.ninety_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.ninety_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.ytd_return != "N/A" %}
                        {{ "%.2f"|format(stock.ytd_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.one_year_return != "N/A" %}
                        {{ "%.2f"|format(stock.one_year_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>

    <div class="chart-container">
        <h2>Portfolio Visualization</h2>
        <img src="data:image/png;base64,{{ chart_img }}" alt="Portfolio Charts">
    </div>

    <h2>Watchlist</h2>
    <table>
        <thead>
            <tr>
                <th>Name</th>
                <th>Symbol</th>
                <th>Current Price</th>
                <th>10 day average price</th>
                <th>30 day average price</th>
                <th>50 day average price</th>
                <th>100 day average price</th>
                <th>200 day average price</th>
                <th>Today's Change</th>
                <th>5 Day Return</th>
                <th>30 day Return</th>
                <th>90 Day Return</th>
                <th>YTD Return</th>
                <th>1 Year Return</th>
            </tr>
        </thead>
        <tbody>
            {% for stock in watchlist_details %}
            <tr>
                <td>{{ stock.name }}</td>
                <td>{{ stock.symbol }}</td>
                <td>
                    {% if stock.current_price != "N/A" %}
                        ${{ "%.2f"|format(stock.current_price) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>

                <td>
                    {% if stock.ten_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.ten_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.thirty_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.thirty_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.fifty_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.fifty_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.one_hundred_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.one_hundred_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.two_hundred_day_moving_average != "N/A" %}
                        ${{ "%.2f"|format(stock.two_hundred_day_moving_average) }}
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.percent_change_today != "N/A" %}
                        {{ "%.2f"|format(stock.percent_change_today) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.five_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.five_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.thirty_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.thirty_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.ninety_day_return != "N/A" %}
                        {{ "%.2f"|format(stock.ninety_day_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.ytd_return != "N/A" %}
                        {{ "%.2f"|format(stock.ytd_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
                <td>
                    {% if stock.one_year_return != "N/A" %}
                        {{ "%.2f"|format(stock.one_year_return) }}%
                    {% else %}
                        N/A
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>


</body>
</html>
"""
    with open(template_path, 'w') as f:
        f.write(template_content)


def save_html_to_s3(html_content):
    """Save HTML content directly to S3 without writing to file system"""
    import boto3

    # Initialize S3 client
    s3 = boto3.client('s3')

    # Get bucket and key for the HTML file from the environment variables
    load_dotenv()
    bucket_name = os.environ.get("BUCKET_NAME")
    key = os.environ.get("BUCKET_KEY")
    if not bucket_name or not key:
        # # Missing configuration, store locally
        # script_dir = Path(__file__).parent
        # local_file = script_dir / "portfolio_report.html"
        # with open(local_file, 'w') as f:
        #     f.write(html_content)
        #
        # print(f'file saved locally at file://{local_file}')
        # return f'file://{local_file}'
        return None
    else:
        #store it in S3 Bucket

        # Upload HTML content directly to S3
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=html_content,
            ContentType='text/html',
            CacheControl='no-store,no-cache,private,max-age=60'
        )

        print(f"Portfolio report uploaded to S3: s3://{bucket_name}/{key}")
        return f"https://www.{bucket_name}/{key}"

if __name__ == "__main__":
    # Create a portfolio
    portfolio = portfolio.Portfolio()

    # Get the directory where the script is located
    script_dir = Path(__file__).parent

    # Read stocks from CSV file
    csv_file = script_dir / "portfolio.csv"
    portfolio.read_stocks_from_csv(csv_file)

    # Update current prices
    portfolio.update_all_prices()
    portfolio.update_metrics()

    # Create a watchlist of stocks to track
    watchlist = watch_list.WatchList()


    # read watchlist from yaml file
    yaml_file = script_dir / "watchlist.yaml"
    watchlist.read_stocks_from_yaml(yaml_file)
    watchlist.update_all_prices()
    watchlist.update_metrics()


    # Create HTML report
    html_content = create_portfolio_html(portfolio,watchlist)

    # Save HTML to S3
    s3_url = save_html_to_s3(html_content)

    # Write HTML to file
    html_file = script_dir / "portfolio_report.html"
    with open(html_file, 'w') as f:
        f.write(html_content)



    # Open HTML in default browser
    # webbrowser.open('file://' + os.path.abspath(html_file))
    # webbrowser.open(s3_url)


    print(f"Portfolio report generated and opened in your browser: {html_file}")

    notifier = Notifier(portfolio)
    notifier.calculate_and_send_notifications()