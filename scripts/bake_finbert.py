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
  change to ``DEFAULT_REVISION`` *and* ``EXPECTED_SHA256`` (below), never a
  silent follow of ``main``. ``FINBERT_REVISION`` overrides the default, but a
  revision whose files don't match the hashes fails the bake.
- **Pickle-format weights are never fetched or loaded** (issue #381). A
  ``.bin``/``.pt``/``.ckpt`` file is a pickle, and unpickling runs code. The
  download is an explicit allow-list of the five files below -- the official
  ``model.safetensors`` that ProsusAI added on 2026-10-12, plus tokenizer and
  config -- so ``pytorch_model.bin`` is never even requested. Each file's sha256
  is checked against the committed value *before* anything parses it, and the
  load passes ``use_safetensors=True``. (Before #381 the bake downloaded the
  ``.bin`` with ``use_safetensors=False`` and converted it; the upstream
  safetensors is byte-identical to that conversion's source tensors.)
- **Saved as safetensors**, so the runtime load is a fast mmap rather than an
  unpickle. ``verify()`` refuses any pickle-format file in the output.
- **Retries with back-off** because Cloud Build shares egress IPs and can be
  rate limited too; a bake that still fails fails the *build*, visibly, instead
  of a 2 a.m. Job. ``HF_TOKEN`` in the environment is honoured if supplied.
- **Verifies the result** by reloading it with ``local_files_only=True`` and
  scoring one sentence, so a broken bake cannot produce a shippable image.
- **World-readable** (issue #375). ``save_pretrained`` writes the safetensors
  file 0600, the bake runs as root, and the runtime is ``appuser`` -- so every
  runtime load failed, and safetensors reported the EACCES as "No such file or
  directory". The root-run reload in ``verify()`` can't see that (root reads
  0600), so ``verify()`` checks the mode bits explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import time

MODEL_ID = "ProsusAI/finbert"
# Hub `main` of ProsusAI/finbert as of 2026-10-15: the first revisions with an
# official model.safetensors. Mirrored by FINBERT_REVISION in
# quantcore/services/sentiment.py (a test keeps them equal). Bump deliberately,
# together with EXPECTED_SHA256 (see docstring).
DEFAULT_REVISION = "0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031"
# The only files the bake downloads, and their sha256 at DEFAULT_REVISION.
# The four small files are byte-identical to the pre-#381 pin (4556d13).
EXPECTED_SHA256 = {
    "model.safetensors": "e5897858ff819aad7629b96ce521ae5477952d03634ef5dd30ed2d76357a9f00",
    "config.json": "f6449ddda85eb726207a40be59c0cd3bd4b142ccb27298d5e45f9ae3396b1abe",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    "tokenizer_config.json": "ffc7913f6084b138dad68de5a0c2f0ee25ac983709260901e01f53be5796b9d9",
    "special_tokens_map.json": "303df45a03609e4ead04bc3dc1536d0ab19b5358db685b6f3da123d05ec200e3",
}
# Pickle-based serializations: loading one can execute code (#381).
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")
DEFAULT_ATTEMPTS = 5
DEFAULT_BACKOFF_SECONDS = 15.0


def _download(revision: str) -> str:
    """Fetch exactly the allow-listed files into a fresh directory; return its path."""
    from huggingface_hub import snapshot_download

    staging = tempfile.mkdtemp(prefix="finbert-download-")
    try:
        snapshot_download(MODEL_ID, revision=revision, local_dir=staging,
                          allow_patterns=sorted(EXPECTED_SHA256))
    except BaseException:
        # A retry starts a fresh directory, so don't leave a partial download behind.
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_pickle_files(directory: str) -> list:
    """Every pickle-format file under ``directory``, as paths relative to it."""
    found = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if name.lower().endswith(PICKLE_SUFFIXES):
                found.append(os.path.relpath(os.path.join(root, name), directory))
    return sorted(found)


def verify_hashes(directory: str, expected: dict = EXPECTED_SHA256) -> None:
    """Raise unless every expected file is present with its committed sha256.

    Runs before anything parses the files, so a tampered download is never loaded.
    """
    pickles = find_pickle_files(directory)
    if pickles:
        raise RuntimeError(f"pickle-format files were downloaded: {pickles}")
    for name, want in sorted(expected.items()):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            raise RuntimeError(f"FinBERT download is missing {name}")
        got = _sha256(path)
        if got != want:
            raise RuntimeError(f"FinBERT {name} sha256 is {got}, expected {want}; "
                               "refusing to load it")


def download_with_retry(revision: str, attempts: int = DEFAULT_ATTEMPTS,
                        backoff: float = DEFAULT_BACKOFF_SECONDS,
                        download=_download, sleep=time.sleep):
    """Run ``download(revision)``, retrying with linear back-off on any error."""
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


def _make_world_readable(out_dir: str) -> None:
    """Add read (files) and read+traverse (directories) for group and other."""
    read = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    traverse = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    for root, dirs, files in os.walk(out_dir):
        os.chmod(root, stat.S_IMODE(os.stat(root).st_mode) | read | traverse)
        for name in files:
            path = os.path.join(root, name)
            os.chmod(path, stat.S_IMODE(os.stat(path).st_mode) | read)


def check_world_readable(out_dir: str) -> None:
    """Raise unless every file is other-readable and every directory other-traversable."""
    for root, dirs, files in os.walk(out_dir):
        mode = stat.S_IMODE(os.stat(root).st_mode)
        if not mode & stat.S_IROTH or not mode & stat.S_IXOTH:
            raise RuntimeError(f"baked FinBERT directory {root} is mode {mode:o}; "
                               "the runtime user cannot read it")
        for name in files:
            path = os.path.join(root, name)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if not mode & stat.S_IROTH:
                raise RuntimeError(f"baked FinBERT file {path} is mode {mode:o}; "
                                   "the runtime user cannot read it")


def _load(directory: str):
    """Load tokenizer + model from a local directory, safetensors only."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        directory, local_files_only=True, use_safetensors=True)
    return tokenizer, model


def verify(out_dir: str) -> None:
    """Check permissions and formats, then reload offline and score one sentence."""
    check_world_readable(out_dir)
    if not any(name.endswith(".safetensors") for name in os.listdir(out_dir)):
        raise RuntimeError(f"no .safetensors file was written to {out_dir}")
    pickles = find_pickle_files(out_dir)
    if pickles:
        raise RuntimeError(f"pickle-format files in baked FinBERT: {pickles}")
    import torch

    tokenizer, model = _load(out_dir)
    model.eval()
    with torch.no_grad():
        logits = model(**tokenizer("Profits rose sharply.", return_tensors="pt")).logits
    if tuple(logits.shape) != (1, 3):
        raise RuntimeError(f"baked FinBERT returned logits of shape {tuple(logits.shape)}")


def bake(out_dir: str, revision: str) -> None:
    staging = download_with_retry(revision)
    try:
        verify_hashes(staging)
        tokenizer, model = _load(staging)
        os.makedirs(out_dir, exist_ok=True)
        tokenizer.save_pretrained(out_dir)
        model.save_pretrained(out_dir, safe_serialization=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _make_world_readable(out_dir)
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
