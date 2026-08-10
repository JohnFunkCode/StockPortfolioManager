#!/bin/bash
# This script is used to publish the HTML portfolio report from a Raspberry Pi.
#
# Deliberately the report script only, not main.py: the quantcore-report Cloud
# Run Job already sends the Discord notifications, checks the Harvester rungs,
# and captures the daily options snapshots, so running main.py here too would
# double every alert and write a second snapshot per symbol per day (#147).
#
# Prerequisites on the Pi are in the script's module docstring — QUANTCORE_DB_DSN,
# a Cloud SQL Auth Proxy, AWS credentials, and `pip install -r requirements.txt`.
cd ~/Documents/code/StockPortfolioManager
source ~/Documents/code/StockPortfolioManager/.venv/bin/activate
python ~/Documents/code/StockPortfolioManager/scripts/generate_portfolio_report.py --publish
