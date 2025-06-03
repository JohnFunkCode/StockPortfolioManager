I'll update the README.md to show examples using Apple (AAPL) and Alphabet Inc Class C (GOOG) instead of the current examples:

```markdown
# Stock Portfolio Manager

A very simple Python stock portfolio tracker with real-time price updates and multi-currency support.
This is the humble beginnings of explorations into automating stock portfolio management


## Features

- Track stocks with purchase information (price, date, quantity)
- Fetch real-time stock prices via Yahoo Finance API
- Calculate gain/loss for individual stocks and total portfolio
- Support for multiple currencies with real-time conversion
- Load portfolio data from CSV files
- Calculate portfolio performance metrics

## Requirements

- Python 3.9+
- pandas
- yfinance
- requests

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/stock-portfolio-manager.git
   cd stock-portfolio-manager
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Create a file called `stocks.csv` file with your portfolio data:
   ```csv
   name,symbol,purchase_price,quantity,purchase_date,currency,sale_price,sale_date,current_price
   Apple Inc,AAPL,150.82,10,2023-06-15,USD,,,
   Alphabet Inc Class C,GOOG,125.23,5,2023-07-21,USD,,,
   ```

## Usage

Run the main application:
```
python main.py
```

The application will:
1. Load stock data from `stocks.csv`
2. Update current prices from Yahoo Finance
3. Calculate and display current values and gain/loss metrics
4. Optionally convert values to other currencies

## Project Structure

- `main.py`: Application entry point, reads CSV data and displays portfolio information
- `stock_portfolio_manager.py`: Core functionality for managing stocks and portfolios
- `money.py`: Currency handling and conversion functionality
- `stocks.csv`: CSV file containing stock data
- `test_money.py`: Unit tests for money handling
- `test_stock_portfolio_manager.py`: Unit tests for portfolio functionality

## Testing

Run the unit tests:
```
python -m unittest discover
```

## Example Output

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
```