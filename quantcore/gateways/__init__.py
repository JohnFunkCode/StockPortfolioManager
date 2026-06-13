"""Gateways — wrappers around external I/O (yfinance, Polygon, webhooks).

Architectural standard v2 §5.1: all network access to third-party data
providers lives here, behind classes that services receive via constructor
injection. No SQL, no business logic.
"""
