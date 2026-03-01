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

## REST API (`api/`)

A Flask REST API that exposes the Harvester Plan Store over HTTP for use by the React frontend or other clients.

**Entry point:** `api/app.py`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check — confirms the API and SQLite database are reachable |
| GET | `/api/plans` | List harvest plans; filter by `?status=ACTIVE\|SUPERSEDED\|ALL` |
| POST | `/api/plans` | Create a new harvest plan for a symbol |
| GET | `/api/plans/<id>` | Get a single plan with its rungs |
| PATCH | `/api/plans/<id>` | Update plan notes or metadata |
| DELETE | `/api/plans/<id>` | Delete (supersede) a plan |
| GET | `/api/plans/<id>/rungs` | List all rungs for a plan |
| GET | `/api/rungs/<id>` | Get a single rung |
| POST | `/api/rungs/<id>/achieve` | Mark a rung as achieved at a given trigger price |
| POST | `/api/rungs/<id>/execute` | Record that shares were sold at a rung (price, quantity, tax) |
| GET | `/api/symbols` | List all ticker symbols that have plans |
| GET | `/api/symbols/<ticker>/price` | Fetch the latest close price for a ticker |
| GET | `/api/dashboard/stats` | Aggregate stats for the dashboard |

### Starting the API server

```bash
# From the project root, with the virtualenv active:
source .venv/bin/activate
python -m api.app
```

The server starts on `http://127.0.0.1:5000`. CORS is enabled for all origins on `/api/*` routes so the React dev server can connect without a proxy.

---

## React Frontend (`frontend/`)

A **Harvest Ladder** dashboard built with React 19, TypeScript, Vite, and Material UI. It communicates exclusively with the Flask API above.

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Summary stats: total active plans, rungs hit, shares harvested, and estimated proceeds |
| Plans | `/plans` | Table of all harvest plans with status badges; create or delete plans |
| Plan Detail | `/plans/:id` | Full rung ladder for a plan; mark rungs as achieved or record executions |
| Symbols | `/symbols` | Look up the latest live price for any ticker symbol |

### Key dependencies

- **React Router v7** — client-side navigation
- **TanStack Query v5** — data fetching, caching, and background refresh
- **MUI v6** (Material UI + MUI X Data Grid) — UI components and data tables

### Starting the frontend dev server

```bash
# From the frontend/ directory:
cd frontend
npm install        # first time only
npm run dev
```

The dev server starts on `http://localhost:5173` and hot-reloads on file changes. It expects the Flask API to be running on `http://127.0.0.1:5000`.

To build a production bundle:

```bash
cd frontend
npm run build      # output goes to frontend/dist/
```

---

## Starting Both Servers Together (Mac)

`runUI-MAC.sh` is a convenience script that launches both the API and frontend servers in the background from a single command.

```bash
./runUI-MAC.sh
```

What it does:
- Activates the Python virtualenv (`.venv/`)
- Starts the Flask API server in the background — output logged to `api.log`
- Starts the Vite frontend dev server in the background — output logged to `frontent.log`
- Prints the PID of each process and the URLs for both servers

Both processes run independently; closing the terminal does not stop them. The script prints a `kill` command with both PIDs so you can shut them down when done.

---

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