"""
Structured logging for the Agentic Market Intelligence System.

In Cloud Run (K_SERVICE env var is set), emits JSON-formatted log records
that Cloud Logging parses natively — including severity, message, and any
extra fields passed as keyword arguments.

Locally (K_SERVICE not set), falls back to a human-readable format.

Usage:
    from agents.structured_logging import get_logger

    log = get_logger(__name__)
    log.info("Signal fired", symbol="NVDA", score=5, tenant_id="7d3cc53d")
    log.error("Tool failed", tool="get_rsi", error=str(exc))

Severity mapping:
    log.debug    → DEBUG
    log.info     → INFO
    log.warning  → WARNING
    log.error    → ERROR
    log.critical → CRITICAL
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone


_IN_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))


class _CloudJsonFormatter(logging.Formatter):
    """
    Formats log records as Cloud Logging–compatible JSON.

    Each record becomes a single JSON line on stderr.  Cloud Logging
    recognises the `severity`, `message`, and `time` fields automatically.
    Any extras passed via log.info("msg", extra={...}) are merged in.
    """

    _SEVERITY = {
        logging.DEBUG:    "DEBUG",
        logging.INFO:     "INFO",
        logging.WARNING:  "WARNING",
        logging.ERROR:    "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "severity": self._SEVERITY.get(record.levelno, "DEFAULT"),
            "message":  record.getMessage(),
            "time":     datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "logger":   record.name,
        }

        # Merge any extras passed as keyword arguments
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
        }
        for key, val in record.__dict__.items():
            if key not in skip:
                payload[key] = val

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class _LocalFormatter(logging.Formatter):
    """Human-readable format for local development."""

    def format(self, record: logging.LogRecord) -> str:
        ts    = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname[0]   # D / I / W / E / C
        msg   = record.getMessage()

        # Append any extras (excluding standard record fields)
        skip = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process", "message",
            "taskName",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in skip}
        if extras:
            msg += "  " + "  ".join(f"{k}={v}" for k, v in extras.items())

        line = f"{ts} [{level}] {record.name}: {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def get_logger(name: str) -> logging.Logger:
    """
    Return a configured logger for `name`.

    Multiple calls with the same name return the same logger instance
    (standard Python logging behaviour).
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger   # already configured — avoid duplicate handlers

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        _CloudJsonFormatter() if _IN_CLOUD_RUN else _LocalFormatter()
    )

    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    return logger
