"""Tests for scripts/bake_finbert.py — the build-time FinBERT bake (issue #280).

Only the retry logic and argument handling run here; the real download and
save are exercised by the image build, which verifies its own output.
"""
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
