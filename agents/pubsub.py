"""
Pub/Sub helpers for the Agentic Market Intelligence System.

Provides a single function — publish_escalation() — used by the Signal Scanner
and Portfolio Monitor to enqueue symbols for Deep Analysis.

The topic name and GCP project are read from environment variables:
  GCP_PROJECT              — defaults to "stock-portfolio-tfowler"
  PUBSUB_ESCALATION_TOPIC  — defaults to "deep-analysis-escalation"

Publishing is silently skipped when the google-cloud-pubsub package is not
installed or when credentials are unavailable (e.g. local dev without ADC).
Set PUBSUB_ENABLED=false to unconditionally disable publishing.
"""
import json
import os


_ENABLED = os.environ.get("PUBSUB_ENABLED", "true").lower() not in ("false", "0", "no")
_PROJECT = os.environ.get("GCP_PROJECT", "stock-portfolio-tfowler")
_TOPIC   = os.environ.get("PUBSUB_ESCALATION_TOPIC", "deep-analysis-escalation")

_publisher = None   # lazy-initialised on first use


def _get_publisher():
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def publish_escalation(
    tenant_id: str,
    symbol: str,
    source: str,
    priority: str = "P3",
) -> bool:
    """
    Publish an escalation message to the deep-analysis Pub/Sub topic.

    Args:
        tenant_id: UUID of the tenant requesting deep analysis.
        symbol:    Stock ticker to analyse.
        source:    "signal_scanner" | "portfolio_monitor" | "manual"
        priority:  "P1" (AT RISK) | "P2" (INST EXIT) | "P3" (signal)

    Returns True if published successfully, False otherwise.
    """
    if not _ENABLED:
        return False

    payload = json.dumps({
        "tenant_id": tenant_id,
        "symbol":    symbol,
        "source":    source,
        "priority":  priority,
    }).encode("utf-8")

    try:
        publisher  = _get_publisher()
        topic_path = publisher.topic_path(_PROJECT, _TOPIC)
        future     = publisher.publish(topic_path, payload)
        future.result(timeout=10)
        return True
    except Exception as exc:
        print(f"Pub/Sub publish skipped: {exc}")
        return False
