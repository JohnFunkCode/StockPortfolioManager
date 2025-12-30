# Stock Portfolio Manager

A Python-based stock portfolio tracker with real-time price updates, multi-currency support, and HTML report generation capabilities.

## Features

- Track stocks portfolio positions including purchase information (price, date, quantity) read from posrtolio.csv
- Tracks Optional watchlist including per-stock 'tags' loaded from watchlist.yaml
- Fetch real-time stock prices via Yahoo Finance API
- Calculate gain/loss for individual stocks and total portfolio
- Support for multiple currencies with real-time conversion
- Generate HTML reports with portfolio performance metrics
- Calculate portfolio performance statistics

## Technologies Used

- Python 3.9+
- pandas - Data manipulation and analysis
- yfinance - Yahoo Finance API integration
- requests - HTTP library
- Jinja2 - HTML template rendering

## Example Stocks

This example uses Apple Inc (AAPL) and Alphabet Inc Class C (GOOG) stocks to demonstrate functionality.

### Example Stock Data

```csv
name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price
Apple Inc,AAPL,150.82,10,2023-06-15,USD,,,
Alphabet Inc Class C,GOOG,125.23,5,2023-07-21,USD,,,
```

### Example Output

```
Stock Portfolio Report created on 2025-06-27 at 8:33pm

Portfolio Summary
Total Investment: $49,236.00
Total Current Value: $60,269.00
Total Gain/Loss: $11,033.00
Total Gain/Loss %: 22.41%

Individual Stock Details
Name	             Symbol	Purchase Price	 Current Price	Quantity	Gain/Loss	Gain/Loss %
Apple Inc	         AAPL	$169.82	         $201.10	    100	        $3128.00	18.42%
Alphabet Inc Class C GOOG	$153.57	         $178.38	    100	        $2481.00	16.16%
Amazon.com Inc	     AMZN	$168.97	         $223.21	    100	        $5424.00	32.10%

```

### Watchlist Files
YAML file entries:
~~~
- name: Example Corp
  symbol: EXMPL
  currency: USD
  tags:
    - ai
    - cloud
~~~

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/JohnFunkCode/stock-portfolio-manager.git
   cd stock-portfolio-manager
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a `stocks.csv` file with your portfolio data following the format in the example above.

## Usage

### Basic Portfolio Analysis

Run the main application:
The application can generate detailed HTML reports with portfolio performance metrics and timestamps in 12-hour format:

```
python main.py

```

## Project Structure

- `main.py`: Application entry point, reads CSV data and displays portfolio information
- portfolio/ – domain modules (stock.py, money.py, metrics.py, portfolio.py, watch_list.py, yfinance_gateway.py).
- html_summary.py, simple_text_summary.py – reporting utilities.
- notifier.py – notification hook.
- templates/ – Jinja2 HTML template.
- Tests: test_money.py, test_stock_portfolio_manager.py.
- Data samples: portfolio.csv, watchlist.csv, watchlist.yaml.

## Testing

Run the unit tests:
```
python -m unittest discover
```

## Report Features

The HTML reports include:
- Portfolio summary with total values
- Individual stock performance metrics
- Gain/loss visualization
- Generated timestamp in 12-hour format (e.g., "2023-05-15 2:30:45 pm")
- Currency conversion options