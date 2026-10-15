#!/usr/bin/env python3
"""Check that two baked FinBERT directories score identically (issue #381).

A one-time piece of evidence, not a CI step: a model swap that changes the logits
puts a step change into stored sentiment history, so a re-bake from a new
revision (or a new download path) should be shown to score the same as the bake
it replaces::

    python scripts/finbert_parity.py OLD_DIR NEW_DIR [--tolerance 1e-6]

Both directories must hold safetensors weights; this script loads nothing else.
Exits 0 when every logit agrees within the tolerance, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sys

HEADLINES = [
    "Profits rose sharply.",
    "The company missed earnings estimates and cut its full-year guidance.",
    "Shares plunged after the CEO resigned unexpectedly.",
    "Revenue grew 12% year over year, beating analyst expectations.",
    "The board approved a $5 billion share buyback program.",
    "Regulators opened an investigation into the firm's accounting practices.",
    "The stock was little changed in light trading.",
    "Analysts downgraded the shares to sell, citing weak demand.",
    "The merger is expected to close in the third quarter.",
    "Gross margin contracted on higher input costs.",
    "The company raised its dividend for the tenth consecutive year.",
    "Quarterly results were in line with consensus.",
    "Supply chain disruptions continue to weigh on production.",
    "The firm announced layoffs affecting 8% of its workforce.",
    "Cloud revenue accelerated for a third straight quarter.",
    "The bank set aside more money for loan losses.",
    "The chipmaker unveiled a new product line at its annual conference.",
    "Oil prices fell as inventories built more than expected.",
    "The retailer reported record holiday sales.",
    "Credit rating agencies placed the company on negative watch.",
]


def _logits(directory: str):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        directory, local_files_only=True, use_safetensors=True)
    model.eval()
    with torch.no_grad():
        batch = tokenizer(HEADLINES, return_tensors="pt", padding=True, truncation=True)
        return model(**batch).logits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old_dir")
    parser.add_argument("new_dir")
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args(argv)

    old, new = _logits(args.old_dir), _logits(args.new_dir)
    worst = float((old - new).abs().max())
    same_labels = bool((old.argmax(dim=-1) == new.argmax(dim=-1)).all())
    print(f"{len(HEADLINES)} headlines: max |Δlogit| = {worst:.3g}, "
          f"labels {'identical' if same_labels else 'DIFFER'}")
    return 0 if worst <= args.tolerance and same_labels else 1


if __name__ == "__main__":
    sys.exit(main())
