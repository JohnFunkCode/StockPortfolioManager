from dataclasses import dataclass
from typing import Optional, Dict, List
import os
from pathlib import Path
import requests
from dotenv import load_dotenv
import yfinance as yf
import pandas as pd


class Metrics:
    five_day_moving_average: Optional[float] = None,
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
        five_day_moving_average: Optional[float] = None,
        thirty_day_moving_average: Optional[float] = None,
        fifty_day_moving_average: Optional[float] = None,
        one_hundred_day_moving_average: Optional[float] = None,
        two_hundred_day_moving_average: Optional[float] = None,
        five_day_return: Optional[float] = None,
        thirty_day_return: Optional[float] = None,
        ninety_day_return: Optional[float] = None,
        ytd_return: Optional[float] = None,
        one_year_return: Optional[float] = None,
        one_year_average_volume: Optional[float] = None
    ):
        self.five_day_moving_average = five_day_moving_average
        self.thirty_day_moving_average = thirty_day_moving_average
        self.fifty_day_moving_average = fifty_day_moving_average
        self.one_hundred_day_moving_average = one_hundred_day_moving_average
        self.two_hundred_day_moving_average = two_hundred_day_moving_average
        self.five_day_return = five_day_return
        self.thirty_day_return = thirty_day_return
        self.ninety_day_return = ninety_day_return
        self.ytd_return = ytd_return
        self.one_year_return = one_year_return
        self.one_year_average_volume = one_year_average_volume

@staticmethod
def get_historical_metrics( symbols: list[str] ) -> Dict[str, Metrics]:
    data = yf.download(
        tickers=" ".join(symbols),
        period="1y",
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
        five_day_moving_average = data[symbol]['Close'].tail(5).mean()
        thirty_day_moving_average = data[symbol]['Close'].tail(30).mean()
        fifty_day_moving_average = data[symbol]['Close'].tail(50).mean()
        one_hundred_day_moving_average = data[symbol]['Close'].tail(100).mean()
        two_hundred_day_moving_average = data[symbol]['Close'].tail(200).mean()

        five_day_return = (data[symbol]['Open'].iloc[-1] - data[symbol]['Close'].iloc[-6]) / data[symbol]['Close'].iloc[-6] * 100
        thirty_day_return = (data[symbol]['Open'].iloc[-1] - data[symbol]['Close'].iloc[-31]) / data[symbol]['Close'].iloc[-31] * 100
        ninety_day_return = (data[symbol]['Open'].iloc[-1] - data[symbol]['Close'].iloc[-91]) / data[symbol]['Close'].iloc[-91] * 100

        days_since_start_of_year = (pd.Timestamp.now() - pd.Timestamp(year=pd.Timestamp.now().year, month=1, day=1)).days
        ytd_return = (data[symbol]['Open'].iloc[-1] - data[symbol]['Close'].iloc[-days_since_start_of_year]) / data[symbol]['Close'].iloc[-days_since_start_of_year] * 100
        one_year_return = (data[symbol]['Open'].iloc[-1] - data[symbol]['Close'].iloc[0]) / data[symbol]['Close'].iloc[0] * 100

        one_year_average_volume = data[symbol]['Volume'].mean()

        metrics[symbol] = Metrics(
            five_day_moving_average=five_day_moving_average,
            thirty_day_moving_average=thirty_day_moving_average,
            fifty_day_moving_average=fifty_day_moving_average,
            one_hundred_day_moving_average=one_hundred_day_moving_average,
            two_hundred_day_moving_average=two_hundred_day_moving_average,
            five_day_return=five_day_return,
            thirty_day_return=thirty_day_return,
            ninety_day_return=ninety_day_return,
            ytd_return=ytd_return,
            one_year_return=one_year_return,
            one_year_average_volume=one_year_average_volume
        )

    #     stock_info = f"\nMetrics for {symbol}:\n"
    #     stock_info += f"{symbol} 5d Avg: ${five_day_moving_average:.2f}\n"
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

    return metrics


if __name__ == "__main__":
    symbols = ["AAPL", "GOOG", "AMZN"]
    metrics = get_historical_metrics(symbols)
    for symbol, metric in metrics.items():
        print(f"{symbol}: {metric}")