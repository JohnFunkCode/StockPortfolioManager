"""Tests for scripts/bake_finbert.py — the build-time FinBERT bake (issue #280).

The download is never real here: ``bake()`` and ``verify()`` run against fake
``torch``/``transformers``/``huggingface_hub`` modules, so these tests also pass on the lean
install. PR CI does not build the images, so this is the only pre-merge check
of the save -> safetensors -> offline-reload path; the image build re-runs
``verify()`` against the real model.
"""
import hashlib
import os
import stat
import tempfile
import types
import unittest
from unittest import mock

from scripts import bake_finbert


class RetryTest(unittest.TestCase):
    def test_returns_first_success(self):
        download = mock.Mock(return_value=("tok", "model"))
        sleep = mock.Mock()
        self.assertEqual(bake_finbert.download_with_retry("rev", download=download, sleep=sleep),
                         ("tok", "model"))
        download.assert_called_once_with("rev")
        sleep.assert_not_called()

    def test_retries_with_linear_backoff(self):
        download = mock.Mock(side_effect=[OSError("429"), OSError("429"), ("tok", "model")])
        sleep = mock.Mock()
        with mock.patch("sys.stderr"):
            out = bake_finbert.download_with_retry("rev", attempts=5, backoff=10,
                                                   download=download, sleep=sleep)
        self.assertEqual(out, ("tok", "model"))
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [10, 20])

    def test_last_failure_propagates(self):
        download = mock.Mock(side_effect=OSError("429"))
        sleep = mock.Mock()
        with mock.patch("sys.stderr"), self.assertRaises(OSError):
            bake_finbert.download_with_retry("rev", attempts=3, download=download, sleep=sleep)
        self.assertEqual(download.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


def _fake_ml_modules(logits_shape=(1, 3)):
    """Stand-ins for torch and transformers that record how they were called."""
    model = mock.MagicMock()
    model.return_value.logits.shape = logits_shape
    transformers = types.SimpleNamespace(
        AutoTokenizer=mock.Mock(), AutoModelForSequenceClassification=mock.Mock())
    transformers.AutoTokenizer.from_pretrained.return_value = mock.MagicMock()
    transformers.AutoModelForSequenceClassification.from_pretrained.return_value = model
    torch = types.SimpleNamespace(no_grad=mock.MagicMock())
    return {"torch": torch, "transformers": transformers}, transformers


class BakeTest(unittest.TestCase):
    """bake(): download -> hash check -> safetensors-only load -> save -> verify."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.staging = os.path.join(self.tmp.name, "staging")
        os.makedirs(self.staging)
        self.out = os.path.join(self.tmp.name, "finbert")

    def _bake(self, tokenizer=None, model=None, verify=None, verify_hashes=None):
        calls = mock.Mock()
        tokenizer = tokenizer or calls.tokenizer
        model = model or calls.model
        with mock.patch.object(bake_finbert, "download_with_retry",
                               return_value=self.staging) as download, \
             mock.patch.object(bake_finbert, "verify_hashes",
                               verify_hashes or calls.verify_hashes), \
             mock.patch.object(bake_finbert, "_load", calls._load), \
             mock.patch.object(bake_finbert, "verify", verify or calls.verify), \
             mock.patch("builtins.print"):
            calls._load.return_value = (tokenizer, model)
            bake_finbert.bake(self.out, "rev")
        download.assert_called_once_with("rev")
        return calls

    def test_checks_hashes_then_loads_saves_and_verifies(self):
        calls = self._bake()
        self.assertTrue(os.path.isdir(self.out))
        self.assertEqual(calls.mock_calls, [
            mock.call.verify_hashes(self.staging),   # before anything parses the files
            mock.call._load(self.staging),
            mock.call.tokenizer.save_pretrained(self.out),
            mock.call.model.save_pretrained(self.out, safe_serialization=True),
            mock.call.verify(self.out),              # verify runs last, against what was written
        ])

    def test_staging_directory_is_removed(self):
        self._bake()
        self.assertFalse(os.path.exists(self.staging))

    def test_hash_mismatch_stops_before_load_and_still_cleans_up(self):
        with mock.patch.object(bake_finbert, "_load") as load, \
             self.assertRaisesRegex(RuntimeError, "sha256"):
            self._bake(verify_hashes=mock.Mock(side_effect=RuntimeError("sha256 mismatch")))
        load.assert_not_called()
        self.assertFalse(os.path.exists(self.staging))
        self.assertFalse(os.path.exists(self.out))

    def test_written_files_are_made_readable_before_verify(self):
        # #375: save_pretrained wrote model.safetensors as 0600 root, which the
        # runtime user (appuser) cannot open. The chmod must land before verify.
        seen = {}

        def write(out, **_):
            path = os.path.join(out, "model.safetensors")
            open(path, "w").close()
            os.chmod(path, 0o600)

        def record_mode(out):
            seen["mode"] = stat.S_IMODE(os.stat(os.path.join(out, "model.safetensors")).st_mode)

        model = mock.Mock()
        model.save_pretrained.side_effect = write
        self._bake(model=model, verify=mock.Mock(side_effect=record_mode))
        self.assertEqual(seen["mode"], 0o644)

    def test_verify_failure_fails_the_bake(self):
        with self.assertRaises(RuntimeError):
            self._bake(verify=mock.Mock(side_effect=RuntimeError("bad")))


class DownloadTest(unittest.TestCase):
    def test_fetches_only_the_allow_list_at_the_revision(self):
        hub = types.SimpleNamespace(snapshot_download=mock.Mock())
        with mock.patch.dict("sys.modules", {"huggingface_hub": hub}):
            staging = bake_finbert._download("rev")
        self.addCleanup(os.rmdir, staging)
        hub.snapshot_download.assert_called_once_with(
            "ProsusAI/finbert", revision="rev", local_dir=staging,
            allow_patterns=sorted(bake_finbert.EXPECTED_SHA256))

    def test_failed_download_removes_its_staging_dir(self):
        # Review of #394: a retry starts fresh, so a failure must not leave a partial dir.
        hub = types.SimpleNamespace(snapshot_download=mock.Mock(side_effect=OSError("429")))
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(**kw):
            made.append(real_mkdtemp(**kw))
            return made[-1]

        with mock.patch.dict("sys.modules", {"huggingface_hub": hub}), \
             mock.patch.object(bake_finbert.tempfile, "mkdtemp", side_effect=mkdtemp):
            with self.assertRaisesRegex(OSError, "429"):
                bake_finbert._download("rev")
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))

    def test_allow_list_has_no_pickle_format_file(self):
        self.assertIn("model.safetensors", bake_finbert.EXPECTED_SHA256)
        for name in bake_finbert.EXPECTED_SHA256:
            self.assertFalse(name.endswith(bake_finbert.PICKLE_SUFFIXES), name)
            self.assertNotIn("*", name)     # exact names, never a glob


class VerifyHashesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.expected = {}
        for name, body in (("model.safetensors", b"weights"), ("config.json", b"{}")):
            with open(os.path.join(self.tmp.name, name), "wb") as fh:
                fh.write(body)
            self.expected[name] = hashlib.sha256(body).hexdigest()

    def test_matching_files_pass(self):
        bake_finbert.verify_hashes(self.tmp.name, self.expected)

    def test_tampered_file_is_rejected(self):
        with open(os.path.join(self.tmp.name, "model.safetensors"), "ab") as fh:
            fh.write(b"!")
        with self.assertRaisesRegex(RuntimeError, r"model\.safetensors sha256"):
            bake_finbert.verify_hashes(self.tmp.name, self.expected)

    def test_missing_file_is_rejected(self):
        os.remove(os.path.join(self.tmp.name, "config.json"))
        with self.assertRaisesRegex(RuntimeError, "missing config.json"):
            bake_finbert.verify_hashes(self.tmp.name, self.expected)

    def test_any_pickle_file_is_rejected(self):
        os.makedirs(os.path.join(self.tmp.name, "sub"))
        open(os.path.join(self.tmp.name, "sub", "pytorch_model.BIN"), "w").close()
        with self.assertRaisesRegex(RuntimeError, "pickle"):
            bake_finbert.verify_hashes(self.tmp.name, self.expected)


class VerifyTest(unittest.TestCase):
    def _verify(self, files=("model.safetensors",), logits_shape=(1, 3)):
        modules, transformers = _fake_ml_modules(logits_shape)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict("sys.modules", modules):
            os.chmod(tmp, 0o755)            # mkdtemp is 0700; a real bake chmods first
            for name in files:
                open(os.path.join(tmp, name), "w").close()
            bake_finbert.verify(tmp)
        return tmp, transformers

    def test_reloads_offline_safetensors_only_from_the_baked_directory(self):
        tmp, transformers = self._verify()
        transformers.AutoTokenizer.from_pretrained.assert_called_once_with(
            tmp, local_files_only=True)
        transformers.AutoModelForSequenceClassification.from_pretrained.assert_called_once_with(
            tmp, local_files_only=True, use_safetensors=True)

    def test_wrong_logits_shape_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "shape"):
            self._verify(logits_shape=(1, 2))

    def test_missing_safetensors_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "safetensors"):
            self._verify(files=("config.json",))

    def test_pickle_file_next_to_safetensors_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "pytorch_model.bin"):
            self._verify(files=("model.safetensors", "pytorch_model.bin"))


class PermissionsTest(unittest.TestCase):
    """#375: the baked weights must be readable by the non-root runtime user."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "finbert")
        self.sub = os.path.join(self.root, "sub")
        os.makedirs(self.sub)
        self.file = os.path.join(self.root, "model.safetensors")
        self.nested = os.path.join(self.sub, "config.json")
        for path in (self.file, self.nested):
            open(path, "w").close()
            os.chmod(path, 0o600)
        os.chmod(self.sub, 0o700)
        os.chmod(self.root, 0o700)

    @staticmethod
    def mode(path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_make_world_readable_opens_files_and_directories(self):
        bake_finbert._make_world_readable(self.root)
        self.assertEqual(self.mode(self.root), 0o755)
        self.assertEqual(self.mode(self.sub), 0o755)
        self.assertEqual(self.mode(self.file), 0o644)
        self.assertEqual(self.mode(self.nested), 0o644)
        bake_finbert.check_world_readable(self.root)   # and the check agrees

    def test_unreadable_file_is_rejected_with_its_mode(self):
        os.chmod(self.root, 0o755)
        os.chmod(self.sub, 0o755)
        os.chmod(self.nested, 0o644)
        with self.assertRaisesRegex(RuntimeError, r"model\.safetensors is mode 600"):
            bake_finbert.check_world_readable(self.root)

    def test_untraversable_directory_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "directory .* is mode 700"):
            bake_finbert.check_world_readable(self.root)

    def test_verify_checks_permissions_before_loading(self):
        modules, transformers = _fake_ml_modules()
        with mock.patch.dict("sys.modules", modules), \
             self.assertRaisesRegex(RuntimeError, "cannot read"):
            bake_finbert.verify(self.root)
        transformers.AutoTokenizer.from_pretrained.assert_not_called()


class MainTest(unittest.TestCase):
    def test_revision_defaults_to_the_pin(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(bake_finbert, "bake") as bake:
            self.assertEqual(bake_finbert.main(["--out", "/x"]), 0)
        bake.assert_called_once_with("/x", bake_finbert.DEFAULT_REVISION)

    def test_revision_env_overrides_the_pin(self):
        with mock.patch.dict("os.environ", {"FINBERT_REVISION": "abc123"}), \
             mock.patch.object(bake_finbert, "bake") as bake:
            bake_finbert.main(["--out", "/x"])
        bake.assert_called_once_with("/x", "abc123")

    def test_pin_matches_the_runtime_fallback(self):
        # The bake can't import quantcore (the Dockerfile copies it alone), so
        # sentiment.py mirrors the pin; keep the two in step.
        from quantcore.services import sentiment
        self.assertEqual(bake_finbert.DEFAULT_REVISION, sentiment.FINBERT_REVISION)
        self.assertEqual(bake_finbert.PICKLE_SUFFIXES, sentiment.PICKLE_SUFFIXES)


if __name__ == "__main__":
    unittest.main()
