# Stock Portfolio Manager

A Python-based stock portfolio tracker with real-time price updates, multi-currency support, and HTML report generation capabilities.

## Features

- Track stocks with purchase information (price, date, quantity)
- Fetch real-time stock prices via Yahoo Finance API
- Calculate gain/loss for individual stocks and total portfolio
- Support for multiple currencies with real-time conversion
- Load portfolio data from CSV files
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
Stock: Apple Inc (AAPL)
Purchase Price: USD 150.82
Current Price: USD 182.74
Quantity: 10
Gain/Loss: USD 319.20
Gain/Loss %: 21.16%

Stock: Alphabet Inc Class C (GOOG)
Purchase Price: USD 125.23
Current Price: USD 178.05
Quantity: 5
Gain/Loss: USD 264.10
Gain/Loss %: 42.18%

Total Invested: USD 2134.35
Total Portfolio Current Value: USD 2717.65
Total Portfolio Gain/Loss: USD 583.30
Total Portfolio Gain/Loss %: 27.33%
```

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
```
python main.py
```

### Generating HTML Reports

The application can generate detailed HTML reports with portfolio performance metrics and timestamps in 12-hour format:

```
python generate_report.py
```

## Project Structure

- `main.py`: Application entry point, reads CSV data and displays portfolio information
- `stock_portfolio_manager.py`: Core functionality for managing stocks and portfolios
- `money.py`: Currency handling and conversion functionality
- `generate_report.py`: HTML report generation using Jinja2 templates
- `templates/`: Directory containing Jinja2 HTML templates
- `stocks.csv`: CSV file containing stock data
- `test_money.py`: Unit tests for money handling
- `test_stock_portfolio_manager.py`: Unit tests for portfolio functionality

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