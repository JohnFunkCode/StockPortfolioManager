import yfinance as yf
import pandas as pd

class MaxDrawDownAnalyzer:
    def __init__(self, symbol, start='2020-01-01', end=None):
        self.symbol = symbol
        self.start = start
        self.end = end
        self.close_prices = None
        self.price_data = None
        self.max_drawdown = None
        self.worst_day_change = None
        self.worst_day_date = None
        self.worst_day_percent_change = None

    def fetch_data(self):
        try:
            ticker = yf.Ticker(self.symbol)
            df = ticker.history(start=self.start, end=self.end)
            if df.empty:
                raise ValueError(f"No data found for {self.symbol}")
            self.close_prices = df['Close']
            self.price_data = df
        except Exception as e:
            print(f"Error fetching data for {self.symbol}: {e}")
            self.close_prices = None

    def calculate_max_drawdown(self):
        if self.close_prices is None:
            print(f"Data not available for {self.symbol}")
            return None

        peak_price = self.close_prices.iloc[0]
        peak_date = self.close_prices.index[0]
        max_drawdown = 0
        trough_price = peak_price
        trough_date = peak_date

        current_peak_price = peak_price
        current_peak_date = peak_date

        for date, price in self.close_prices.items():
            if price > current_peak_price:
                current_peak_price = price
                current_peak_date = date
            drawdown = (price - current_peak_price) / current_peak_price
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                peak_price = current_peak_price
                peak_date = current_peak_date
                trough_price = price
                trough_date = date

        # Save results to instance variables
        self.max_drawdown = max_drawdown
        self.peak_date = peak_date
        self.trough_date = trough_date
        self.peak_price = peak_price
        self.trough_price = trough_price

        return max_drawdown


    def calculate_worst_day1(self):
        df = self.price_data.copy()

        df['prev_close'] = df['Close'].shift(1)
        df['pct_change'] = (df['Close'] - df['prev_close']) / df['prev_close']

        df = df.dropna()

        worst_idx = df['pct_change'].idxmin()
        worst_row = df.loc[worst_idx]

        # print(f"  Worst one-day close-to-close drop: Date: {worst_idx.date()} Return: {worst_row['pct_change']:.4%} Previous Close: {worst_row['prev_close']:.2f} Close: {worst_row['Close']:.2f}")
        print(f"  Worst one-day close-to-close drop: {worst_row['pct_change']:.4%}")
        print(f"    Date: {worst_idx.date()} Previous Close: {worst_row['prev_close']:.2f} Close: {worst_row['Close']:.2f}")

        worst_day_percent_change = float(worst_row['pct_change'])
        return worst_day_percent_change

    def calculate_rolling_drawdown(self, window_days=5):
        if self.close_prices is None:
            print(f"Data not available for {self.symbol}")
            return None

        max_dd = 0
        worst_start = None
        worst_end = None
        worst_peak = None
        worst_trough = None

        for i in range(len(self.close_prices) - window_days + 1):
            window = self.close_prices.iloc[i:i + window_days]
            dates = window.index
            prices = window.values

            peak_price = prices[0]
            peak_date = dates[0]

            for j in range(1, len(prices)):
                if prices[j] > peak_price:
                    peak_price = prices[j]
                    peak_date = dates[j]
                drawdown = (prices[j] - peak_price) / peak_price
                if drawdown < max_dd:
                    max_dd = drawdown
                    worst_start = peak_date
                    worst_end = dates[j]
                    worst_peak = peak_price
                    worst_trough = prices[j]

        self.rolling_drawdown = max_dd
        self.rolling_start_date = worst_start
        self.rolling_end_date = worst_end
        self.rolling_peak_price = worst_peak
        self.rolling_trough_price = worst_trough

        return max_dd


    def report(self):
        dd = self.calculate_max_drawdown()
        if dd is not None:
            print(f"{self.symbol}:")
            print(f"  Max Drawdown   = {dd:.2%}")
            print(f"  Peak Date      = {self.peak_date.date()} at ${self.peak_price:.2f}")
            print(f"  Trough Date    = {self.trough_date.date()} at ${self.trough_price:.2f}")
            days_in_drawdown = (self.trough_date - self.peak_date).days
            print(f"  Drawdown Period= {self.peak_date.date()} to {self.trough_date.date()} - {days_in_drawdown} day drop")
        else:
            print(f"{self.symbol}: Could not compute Max Drawdown")

        # Also show rolling 10 day drawdown
        window_days = 10
        rolling_dd = self.calculate_rolling_drawdown(window_days=window_days)
        if rolling_dd is not None:
            print(f"  Worst {window_days}-Day Drop = {rolling_dd:.2%}")
            print(f"    From {self.rolling_start_date.date()} at ${self.rolling_peak_price:.2f}")
            print(f"    To   {self.rolling_end_date.date()} at ${self.rolling_trough_price:.2f}")

        # Also show rolling 5 day drawdown
        window_days = 5
        rolling_dd = self.calculate_rolling_drawdown(window_days=window_days)
        if rolling_dd is not None:
            print(f"  Worst {window_days}-Day Drop = {rolling_dd:.2%}")
            print(f"    From {self.rolling_start_date.date()} at ${self.rolling_peak_price:.2f}")
            print(f"    To   {self.rolling_end_date.date()} at ${self.rolling_trough_price:.2f}")

        # # Also show rolling 2 day drawdown
        # window_days = 2
        # rolling_dd = self.calculate_rolling_drawdown(window_days=window_days)
        # if rolling_dd is not None:
        #     print(f"  Worst {window_days}-Day Drop = {rolling_dd:.2%}")
        #     print(f"    From {self.rolling_start_date.date()} at ${self.rolling_peak_price:.2f}")
        #     print(f"    To   {self.rolling_end_date.date()} at ${self.rolling_trough_price:.2f}")

        # and print the worst day change
        worst_day_change = self.calculate_worst_day1()

def analyze_stocks(symbols, start='2020-01-01', end=None):
    for symbol in symbols:
        analyzer = MaxDrawDownAnalyzer(symbol, start=start, end=end)
        analyzer.fetch_data()
        analyzer.report()


# === Example usage ===
if __name__ == "__main__":
    stock_list = ['AVGO', 'NVDA','RCL','PANW','ZS','CAT','GLW','WDC','GOOG','MSFT','MU','AAPL','QCOM','ZS','IREN','TER']
    analyze_stocks(stock_list, start='2025-01-01')