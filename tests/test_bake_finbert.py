"""Tests for scripts/bake_finbert.py — the build-time FinBERT bake (issue #280).

The download is never real here: ``bake()`` and ``verify()`` run against fake
``torch``/``transformers`` modules, so these tests also pass on the lean
install. PR CI does not build the images, so this is the only pre-merge check
of the save -> safetensors -> offline-reload path; the image build re-runs
``verify()`` against the real model.
"""
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
    def test_saves_tokenizer_and_safetensors_model_then_verifies(self):
        calls = mock.Mock()
        tokenizer, model = calls.tokenizer, calls.model
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bake_finbert, "download_with_retry",
                               return_value=(tokenizer, model)) as download, \
             mock.patch.object(bake_finbert, "verify", calls.verify), \
             mock.patch("builtins.print"):
            out = os.path.join(tmp, "finbert")
            bake_finbert.bake(out, "rev")
            self.assertTrue(os.path.isdir(out))
        download.assert_called_once_with("rev")
        self.assertEqual(calls.mock_calls, [
            mock.call.tokenizer.save_pretrained(out),
            mock.call.model.save_pretrained(out, safe_serialization=True),
            mock.call.verify(out),          # verify runs last, against what was written
        ])

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
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bake_finbert, "download_with_retry",
                               return_value=(mock.Mock(), model)), \
             mock.patch.object(bake_finbert, "verify", side_effect=record_mode), \
             mock.patch("builtins.print"):
            bake_finbert.bake(os.path.join(tmp, "finbert"), "rev")
        self.assertEqual(seen["mode"], 0o644)

    def test_verify_failure_fails_the_bake(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bake_finbert, "download_with_retry",
                               return_value=(mock.Mock(), mock.Mock())), \
             mock.patch.object(bake_finbert, "verify", side_effect=RuntimeError("bad")), \
             self.assertRaises(RuntimeError):
            bake_finbert.bake(os.path.join(tmp, "finbert"), "rev")


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

    def test_reloads_offline_from_the_baked_directory(self):
        tmp, transformers = self._verify()
        transformers.AutoTokenizer.from_pretrained.assert_called_once_with(
            tmp, local_files_only=True)
        transformers.AutoModelForSequenceClassification.from_pretrained.assert_called_once_with(
            tmp, local_files_only=True)

    def test_wrong_logits_shape_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "shape"):
            self._verify(logits_shape=(1, 2))

    def test_missing_safetensors_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "safetensors"):
            self._verify(files=("pytorch_model.bin",))


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


if __name__ == "__main__":
    unittest.main()
