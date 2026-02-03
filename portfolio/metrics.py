from dataclasses import dataclass
from typing import Optional, Dict, List
import os
from pathlib import Path
import requests
from dotenv import load_dotenv
import yfinance as yf
import pandas as pd
import numpy as np
import pandas as pd


class Metrics:
    ten_day_moving_average: Optional[float] = None,
    thirty_day_moving_average: Optional[float] = None,
    fifty_day_moving_average: Optional[float] = None,
    one_hundred_day_moving_average: Optional[float] = None,
    two_hundred_day_moving_average: Optional[float] = None,
    five_day_return: Optional[float] = None
    thirty_day_return: Optional[float] = None,
    ninety_day_return: Optional[float] = None,
    ytd_return: Optional[float] = None,
    one_year_return: Optional[float] = None

    def __init__(
        self,
        ten_day_moving_average: Optional[float] = None,
        thirty_day_moving_average: Optional[float] = None,
        fifty_day_moving_average: Optional[float] = None,
        one_hundred_day_moving_average: Optional[float] = None,
        two_hundred_day_moving_average: Optional[float] = None,
        percent_change_today: Optional[float] = None,
        five_day_return: Optional[float] = None,
        thirty_day_return: Optional[float] = None,
        ninety_day_return: Optional[float] = None,
        ytd_return: Optional[float] = None,
        one_year_return: Optional[float] = None,
        one_year_average_volume: Optional[float] = None
    ):
        self.ten_day_moving_average = ten_day_moving_average
        self.thirty_day_moving_average = thirty_day_moving_average
        self.fifty_day_moving_average = fifty_day_moving_average
        self.one_hundred_day_moving_average = one_hundred_day_moving_average
        self.two_hundred_day_moving_average = two_hundred_day_moving_average
        self.percent_change_today = percent_change_today
        self.five_day_return = five_day_return
        self.thirty_day_return = thirty_day_return
        self.ninety_day_return = ninety_day_return
        self.ytd_return = ytd_return
        self.one_year_return = one_year_return
        self.one_year_average_volume = one_year_average_volume


def ma_regression_slope(
    close: pd.Series,
    ma_window: int = 100,
    slope_window: int | None = None,
    normalize_result: bool = True
) -> float:
    """
    Compute the regression slope of a moving average using an
    industry-practice slope window:

        slope_window = clamp( int(0.5 * ma_window), 10, ma_window )

    unless explicitly provided, in which case the window is clamped
    to [10, ma_window].

    Parameters
    ----------
    close : pd.Series
        Series of closing prices, oldest -> newest.
    ma_window : int
        Window size for the moving average.
    slope_window : int or None
        If None: choose industry-practice default.
        If provided: clamp to [10, ma_window].

    Returns
    -------
    float
        Regression slope of the MA over the slope window.
    """
    if not isinstance(close, pd.Series):
        close = pd.Series(close)

    # ---- Determine slope window (industry practice) ----
    if slope_window is None:
        # Default: 50% of MA length, clamped to [10, ma_window]
        slope_window = int(0.5 * ma_window)
    # Clamp regardless of where slope_window came from
    slope_window = max(10, min(slope_window, ma_window))

    # ---- Data length sanity check ----
    min_len = ma_window + slope_window - 1
    if len(close) < min_len:
        raise ValueError(
            f"Not enough data: got {len(close)} points, need at least "
            f"{min_len} for ma_window={ma_window} and slope_window={slope_window}."
        )

    # ---- Moving average ----
    ma = close.rolling(window=ma_window, min_periods=ma_window).mean()

    # ---- Last `slope_window` MA points ----
    ma_tail = ma.iloc[-slope_window:].astype(float).values

    # ---- Linear regression slope ----
    W = slope_window
    t = np.arange(1, W + 1, dtype=float)

    sum_t = t.sum()
    sum_t2 = (t * t).sum()
    sum_y = ma_tail.sum()
    sum_ty = (t * ma_tail).sum()

    numerator = W * sum_ty - sum_t * sum_y
    denominator = W * sum_t2 - (sum_t ** 2)
    if denominator == 0:
        raise ZeroDivisionError("Degenerate regression denominator.")

    slope = float(numerator / denominator)
    normal_slope = slope / ma_tail[-1] * 100 # normalized to percentage-per-day terms

    if normalize_result:
        return_value = normal_slope
    else:
        return_value = slope

    return return_value

def get_historical_metrics( symbols: list[str] ) -> Dict[str, Metrics]:
    data = yf.download(
        tickers=" ".join(symbols),
        period="2y",
        interval="1d",
        progress=False,
        threads=False,
        group_by="ticker",
        auto_adjust=False,
    )
    # print(data)

    # Calculate the average price for each stock in the data
    metrics = {}
    for symbol in data.columns.levels[0]:
        change_today = data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-1]
        percent_change_today = change_today / data[symbol]['Open'].iloc[-1] * 100

        ten_day_moving_average = data[symbol]['Close'].tail(10).mean()
        # ten_day_velocity = ma_regression_slope(data[symbol]['Close'], ma_window=10)

        thirty_day_moving_average = data[symbol]['Close'].tail(30).mean()
        # thirty_day_velocity = ma_regression_slope(data[symbol]['Close'], ma_window=30)

        fifty_day_moving_average = data[symbol]['Close'].tail(50).mean()
        # fifty_day_velocity = ma_regression_slope(data[symbol]['Close'], ma_window=50)

        one_hundred_day_moving_average = data[symbol]['Close'].tail(100).mean()
        # one_hundred_day_velocity = ma_regression_slope(data[symbol]['Close'], ma_window=100)

        two_hundred_day_moving_average = data[symbol]['Close'].tail(200).mean()
        # two_hundred_day_velocity = ma_regression_slope(data[symbol]['Close'], ma_window=200)

        five_day_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-6]) / data[symbol]['Open'].iloc[-6] * 100
        thirty_day_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-31]) / data[symbol]['Open'].iloc[-31] * 100
        ninety_day_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-91]) / data[symbol]['Open'].iloc[-91] * 100

        # days_since_start_of_year = (pd.Timestamp.now() - pd.Timestamp(year=pd.Timestamp.now().year, month=1, day=1)).days
        # ytd_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-days_since_start_of_year]) / data[symbol]['Open'].iloc[-days_since_start_of_year] * 100

        start_of_year = pd.Timestamp(year=pd.Timestamp.now().year, month=1, day=1)
        today = pd.Timestamp.now().normalize()
        working_days = len(pd.bdate_range(start=start_of_year, end=today)) - 1  # exclude today if needed
        ytd_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[-working_days]) / data[symbol]['Open'].iloc[-working_days] * 100
        one_year_return = (data[symbol]['Close'].iloc[-1] - data[symbol]['Open'].iloc[0]) / data[symbol]['Open'].iloc[0] * 100

        one_year_average_volume = data[symbol]['Volume'].mean()


        metrics[symbol] = Metrics(
            ten_day_moving_average=ten_day_moving_average,
            # ten_day_velocity = ten_day_velocity,
            thirty_day_moving_average=thirty_day_moving_average,
            # thirty_day_velocity = thirty_day_velocity,
            fifty_day_moving_average=fifty_day_moving_average,
            # fifty_day_velocity = fifty_day_velocity,
            one_hundred_day_moving_average=one_hundred_day_moving_average,
            # one_hundred_day_velocity = one_hundred_day_velocity,
            two_hundred_day_moving_average=two_hundred_day_moving_average,
            # two_hundred_day_velocity = two_hundred_day_velocity,

            percent_change_today = percent_change_today,
            five_day_return=five_day_return,
            thirty_day_return=thirty_day_return,
            ninety_day_return=ninety_day_return,
            ytd_return=ytd_return,
            one_year_return=one_year_return,
            one_year_average_volume=one_year_average_volume
        )
    return metrics

    #     stock_info = f"\nMetrics for {symbol}:\n"
    #     stock_info += f"{symbol} 5d Avg: ${ten_day_moving_average:.2f}\n"
    #     stock_info += f"{symbol} 30d Avg: ${thirty_day_moving_average:.2f}\n"
    #     stock_info += f"{symbol} 50d Avg: ${fifty_day_moving_average:.2f}\n"
    #     stock_info += f"{symbol} 100d Avg: ${one_hundred_day_moving_average:.2f}\n"
    #     stock_info += f"{symbol} 200d Avg: ${two_hundred_day_moving_average:.2f}\n"
    #     stock_info += f"{symbol} 5 Day Return: {five_day_return:.2f}%\n"
    #     stock_info += f"{symbol} 30 Day Return: {thirty_day_return:.2f}%\n"
    #     stock_info += f"{symbol} 90 Day Return: {ninety_day_return:.2f}%\n"
    #     stock_info += f"{symbol} YTD Return: {ytd_return:.2f}%\n"
    #     stock_info += f"{symbol} 1 Year Return: {one_year_return:.2f}%\n"
    #     stock_info += f"{symbol} 1 Year Average Volume: {one_year_average_volume:.0f}\n"
    #     print(stock_info)
    #
    # print("Metrics calculated successfully.")




if __name__ == "__main__":
    symbols = ["AAPL", "GOOG", "AMZN"]
    metrics = get_historical_metrics(symbols)
    for symbol, metric in metrics.items():
        print(f"{symbol}: {metric}")