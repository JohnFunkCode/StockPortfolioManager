#!/usr/bin/env python3
"""Bake the FinBERT weights into a directory at image-build time (issue #280).

Run by the builder stage of ``Dockerfile.api`` and ``Dockerfile.news``::

    python scripts/bake_finbert.py --out /opt/models/finbert

Without this, every cold start downloaded the model from the Hugging Face Hub at
runtime -- unauthenticated, so the Hub's 429 rate limit stalled the news Job for
~2.5 minutes a night and turned the first ``/api/securities/{ticker}/news`` call
after a cold start into a 504. Baked, the runtime loads from local disk with
``HF_HUB_OFFLINE=1`` and makes no Hub calls at all.

Choices worth keeping:

- **The revision is pinned.** A new checkpoint shifts the score distribution and
  puts a step change into stored sentiment history, so an upgrade is a reviewed
  one-line change to ``FINBERT_REVISION`` (default below), never a silent
  follow of ``main``.
- **``use_safetensors=False`` on the download.** The pinned revision ships only
  ``pytorch_model.bin``; left to itself ``transformers`` probes the Hub bot's
  safetensors-conversion PR ref and downloads *both* formats (~836 MB and extra
  Hub calls). We fetch the ``.bin`` once and write safetensors ourselves.
- **Saved as safetensors**, so the runtime load is a fast mmap rather than an
  unpickle -- and nothing at runtime unpickles a downloaded file.
- **Retries with back-off** because Cloud Build shares egress IPs and can be
  rate limited too; a bake that still fails fails the *build*, visibly, instead
  of a 2 a.m. Job. ``HF_TOKEN`` in the environment is honoured if supplied.
- **Verifies the result** by reloading it with ``local_files_only=True`` and
  scoring one sentence, so a broken bake cannot produce a shippable image.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

MODEL_ID = "ProsusAI/finbert"
# Hub `main` of ProsusAI/finbert as of 2026-10-02. Bump deliberately (see docstring).
DEFAULT_REVISION = "4556d13015211d73dccd3fdd39d39232506f3e43"
DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = 15.0


def _download(revision: str):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=revision)
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_ID, revision=revision, use_safetensors=False)
    return tokenizer, model


def download_with_retry(revision: str, attempts: int = DEFAULT_ATTEMPTS,
                        backoff: float = DEFAULT_BACKOFF_SECONDS,
                        download=_download, sleep=time.sleep):
    """Download tokenizer + model, retrying with linear back-off on any error."""
    for attempt in range(1, attempts + 1):
        try:
            return download(revision)
        except Exception as exc:  # noqa: BLE001 — 429s surface as several exception types
            if attempt == attempts:
                raise
            wait = backoff * attempt
            print(f"FinBERT download attempt {attempt}/{attempts} failed "
                  f"({type(exc).__name__}); retrying in {wait:.0f}s.", file=sys.stderr)
            sleep(wait)
    raise AssertionError("unreachable")


def verify(out_dir: str) -> None:
    """Reload from disk with no network and score one sentence."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(out_dir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(out_dir, local_files_only=True)
    model.eval()
    with torch.no_grad():
        logits = model(**tokenizer("Profits rose sharply.", return_tensors="pt")).logits
    if tuple(logits.shape) != (1, 3):
        raise RuntimeError(f"baked FinBERT returned logits of shape {tuple(logits.shape)}")
    if not any(name.endswith(".safetensors") for name in os.listdir(out_dir)):
        raise RuntimeError(f"no .safetensors file was written to {out_dir}")


def bake(out_dir: str, revision: str) -> None:
    tokenizer, model = download_with_retry(revision)
    os.makedirs(out_dir, exist_ok=True)
    tokenizer.save_pretrained(out_dir)
    model.save_pretrained(out_dir, safe_serialization=True)
    verify(out_dir)
    print(f"Baked {MODEL_ID}@{revision[:7]} into {out_dir}.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="directory to write the model into")
    parser.add_argument("--revision",
                        default=os.environ.get("FINBERT_REVISION") or DEFAULT_REVISION,
                        help="Hub revision (default: $FINBERT_REVISION or the pinned sha)")
    args = parser.parse_args(argv)
    bake(args.out, args.revision)
    return 0


if __name__ == "__main__":
    sys.exit(main())
