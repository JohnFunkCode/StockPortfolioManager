"""Analytics — pure computation: DataFrame/values in, dict/float out.

Architectural standard v2 §5.1: no I/O of any kind here (no network, no SQL,
no filesystem). Everything is deterministically testable with synthetic data.
"""
