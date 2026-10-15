# FinBERT: load only hash-verified safetensors (issue #381)

**Status:** implemented on branch `issue-381-finbert-safetensors`, pending review. Prod roll-out
is the usual manual `prod-rollout.yml` dispatch.

## Why

A PyTorch `.bin`/`.pt`/`.ckpt` file is a **pickle**, and loading a pickle runs code. Before this
change `scripts/bake_finbert.py` passed `use_safetensors=False`, so every api and news image build
downloaded `ProsusAI/finbert`'s `pytorch_model.bin` and deserialized it in the builder. Anyone able
to change that file on the Hub, or between the Hub and Cloud Build, could run code in the build.
The local-dev fallback in `_ensure_finbert` had the same exposure with **no revision pin at all**,
and the tokenizer and config files were fetched unverified.

## What changed

1. **The bake** (`scripts/bake_finbert.py`)
   - The pin moves to `0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031` (Hub `main` on 2026-10-15). That
     is the first upstream revision with an official `model.safetensors`.
   - `snapshot_download` is called with an `allow_patterns` list of five files.
   - Each file's sha256 is checked against `EXPECTED_SHA256` before anything parses it.
   - The weights load with `use_safetensors=True` and are re-saved as safetensors.
   - `verify()` now also refuses any pickle-suffix file in the output.
2. **The runtime** (`quantcore/services/sentiment.py`)
   - `_ensure_finbert` refuses a local `FINBERT_MODEL_PATH` that contains a pickle-suffix file. It
     logs the file names, nothing else.
   - It loads with `use_safetensors=True`.
   - The Hub fallback is pinned to `FINBERT_REVISION`. A test keeps that value equal to the bake's
     `DEFAULT_REVISION`, because the script is copied into the builder alone and can't import
     `quantcore`.
3. **The Dockerfiles.** The `appuser` read check in `Dockerfile.api` and `Dockerfile.news` also
   asserts that no pickle-suffix file is present.
4. **The guard** (`tests/test_no_pickle_model_loads.py`)
   - It is a pure AST scan of tracked `*.py` outside `tests/` and `experiments/`, so it runs in the
     lean CI jobs without torch.
   - It fails on any of these:
     - `torch`/`pickle`/`dill`/`cloudpickle`/`joblib` `.load`/`.loads`/`.Unpickler`
     - `from pickle import load`
     - `trust_remote_code` set to anything but `False`
     - a non-tokenizer `from_pretrained` without `use_safetensors=True`
   - It self-tests each rule.
5. **`scripts/finbert_parity.py OLD_DIR NEW_DIR`** scores 20 headlines with two baked directories
   and fails if any logit differs by more than 1e-6. It is evidence for a model bump, not a CI step.

## Decision for John: the model card's license

At the old pin `4556d13` the model card declared **no license**. From `2f82991` (2026-10-12, the
commit that added `model.safetensors`) it declares **`cc-by-nc-sa-4.0`**: non-commercial and
share-alike. Pinning `0bc2ac1` means building from a revision whose card states that license.

- **The weights are byte-identical,** so the old revision is arguably covered by the same terms
  now anyway.
- **This is a policy call, not a code one.** If you'd rather not adopt the new card, the
  alternative changes nothing else in this design:
  - Stay on `4556d13`'s files.
  - Fetch the identical `model.safetensors` from SFconvertbot's `refs/pr/29` by its commit sha.
  - Keep the same allow-list and hash check.

## Gotchas

- **`use_safetensors=False` was never a security decision.** #280 added it because, left alone,
  `transformers` also looked up the Hub bot's conversion PR and pulled both formats (836 MB, more
  Hub calls, more 429 exposure). The allow-list keeps that protection: it makes one listing call,
  fetches one format, and downloads about 438 MB. So removing the flag doesn't reopen the 429
  problem.
- **The upstream safetensors appeared three days before this work.** Without it, the options were
  to self-host a converted file or to pull `refs/pr/29`. ProsusAI's file has sha256
  `e5897858…9f00` (437,965,908 bytes) and is **byte-identical to `refs/pr/29`**. The four small
  files are byte-identical to the old pin.
- **The new file is stored on Xet, not LFS.** The download redirects to `cas-bridge`/`xet-bridge`
  and carries an `x-xet-hash` header, and `hf-xet` (in the ML lock) takes over the transfer.
  - In the local Claude Code sandbox the `hf_xet` client stalled at 0 bytes for over ten minutes
    and left an `.incomplete` file. No error was raised; it simply never finished.
  - `HF_HUB_DISABLE_XET=1` forces the plain HTTP bridge, which completed in about a minute.
  - Cloud Build has no such sandbox, so the build doesn't set the variable. If an image build ever
    hangs in the bake, try that first.
- **The baked file is not the upstream file.** `save_pretrained` re-serializes the tensors, so the
  baked `model.safetensors` is 437,961,700 bytes with a different hash. That is expected.
  `EXPECTED_SHA256` checks the **download**, and `verify()` checks the output by loading and
  scoring it.
- **transformers prints an `UNEXPECTED` keys note** when loading the checkpoint, because it carries
  keys the sequence-classification head doesn't use. The old `.bin` path printed the same thing.
  The weights that matter load: all 201 tensors, and the labels come out right.

- **gitleaks flags the `EXPECTED_SHA256` entries.** Its `generic-api-key` rule reads
  `"special_tokens_map.json": "<64 hex>"` as a key (entropy ~3.7), which failed the PR's
  `lean-import` job.
  - The fix is a second `[[allowlists]]` table in `.gitleaks.toml`. It is path **AND** regex:
    only a bare 64-hex digest, and only in `scripts/bake_finbert.py`. The same hex anywhere else
    still trips the scan.
  - It is not a `.gitleaksignore` fingerprint, because a fingerprint is tied to one commit and
    line, so every model bump would need a new one.
  - **What misled:** `[[allowlists]]` needs gitleaks >= 8.25, and `gitleaks-action@v3` silently
    defaults to **8.24.3**. 8.24.3 would refuse the config outright, and a local 8.30 run passes
    either way. `deploy.yml` now pins `GITLEAKS_VERSION`, and `tests/test_check_deploy_ref.py`
    asserts the pin is >= 8.25. The legacy `[allowlist]` table can't be mixed with
    `[[allowlists]]`, so the existing one was renamed too.

## Out of scope

`experiments/INTC_bear_call_spread_monitor.py` and `experiments/WMT_bull_call_spread_monitor.py`
use `pickle` for their own local state file. That file is not model weights, and those scripts are
not deployed, which is why the guard excludes `experiments/`. They are candidates for a switch to
JSON.

## Checkpoint log

- **2026-10-15:** The upstream state is verified through the Hub API: the pin, the five hashes,
  `refs/pr/29` byte-identity, and the license change.
- **2026-10-15:** Bake, runtime, Dockerfiles, guard test and unit tests are done. The targeted
  suites pass: `test_bake_finbert`, `test_sentiment_service`, `test_no_pickle_model_loads`.
- **2026-10-15:** The local bake ran (`HF_HUB_DISABLE_XET=1 python scripts/bake_finbert.py --out
  $TMPDIR/finbert-new`).
  - Hashes verified, the staging dir was removed, `verify()` passed, and the output holds
    `config.json`, `model.safetensors`, `tokenizer.json` and `tokenizer_config.json`, with no
    pickle files.
  - Sanity scores:
    - "Profits rose sharply." → positive 0.84
    - "Shares plunged after the CEO resigned unexpectedly." → negative 0.96
    - "The stock was little changed in light trading." → neutral 0.87
- **Parity against the deployed bake: not run.** The old bake exists only inside the test api
  image, a multi-GB pull from Artifact Registry. What stands in for it:
  - The new upstream tensors are byte-identical to `refs/pr/29`, which is SFconvertbot's tensor
    conversion of the same `.bin` that the old bake converted.
  - No logit change is expected.
  - To produce the evidence, extract `/opt/models/finbert` from a pre-#381 test image and run
    `python scripts/finbert_parity.py OLD NEW`.
- **2026-10-15:** The PR's gitleaks step flagged the two hash lines. A scoped allowlist and a
  pinned `GITLEAKS_VERSION` fixed it. Local gitleaks 8.30.1 finds no leaks on the PR range or in
  the full history, and a negative check on the same hex in another file is still flagged.
