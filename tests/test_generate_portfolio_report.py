"""The extracted HTML report script (#147, Part F).

The four rendering functions moved out of main.py verbatim and are covered by
the report they produce. What is *new* here is the CLI around them, and it
exists to close the specific hole issue #147 describes: the old code returned
``None`` when BUCKET_NAME/BUCKET_KEY were missing and let the caller carry on
reporting success, so the published page could stop updating without anyone
noticing. These tests pin the two halves of that fix — a precondition check
before the expensive work, and a hard failure at the upload itself.

The template-path test guards the one line that could not move verbatim.
"""
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import scripts.generate_portfolio_report as report


def _no_dotenv():
    """Keep a developer's real .env out of the environment assertions."""
    return patch.object(report, "load_dotenv", lambda *a, **k: None)


class PublishPreconditionTest(unittest.TestCase):
    """--publish was asked for explicitly, so missing credentials are a
    failure, not a fallback."""

    def test_missing_bucket_config_fails_before_any_work(self):
        with _no_dotenv(), patch.dict(os.environ, {}, clear=True), \
                patch.object(report, "build_report") as build, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = report.main(["--publish"])

        self.assertEqual(code, 1)
        build.assert_not_called()  # the check lands before minutes of price fetching
        self.assertIn("BUCKET_NAME", err.getvalue())

    def test_half_configured_is_still_a_failure(self):
        with _no_dotenv(), patch.dict(os.environ, {"BUCKET_NAME": "b"}, clear=True), \
                patch.object(report, "build_report"), \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            code = report.main(["--publish"])

        self.assertEqual(code, 1)
        self.assertIn("BUCKET_KEY", err.getvalue())

    def test_a_configured_publish_uploads_and_succeeds(self):
        env = {"BUCKET_NAME": "example.com", "BUCKET_KEY": "report.html"}
        with _no_dotenv(), patch.dict(os.environ, env, clear=True), \
                patch.object(report, "build_report", return_value="<html>hi</html>"), \
                patch.object(report, "save_html_to_s3", return_value="https://x") as put:
            code = report.main(["--publish"])

        self.assertEqual(code, 0)
        put.assert_called_once_with("<html>hi</html>")

    def test_an_upload_that_returns_no_url_is_not_a_success(self):
        env = {"BUCKET_NAME": "example.com", "BUCKET_KEY": "report.html"}
        with _no_dotenv(), patch.dict(os.environ, env, clear=True), \
                patch.object(report, "build_report", return_value="<html></html>"), \
                patch.object(report, "save_html_to_s3", return_value=None), \
                patch("sys.stderr", new_callable=io.StringIO):
            code = report.main(["--publish"])

        self.assertEqual(code, 1)


class SaveHtmlToS3Test(unittest.TestCase):
    def test_missing_bucket_config_raises_instead_of_returning_none(self):
        """The original silent-success path. Nothing calls this without the
        precondition check above, but a future caller might."""
        with _no_dotenv(), patch.dict(os.environ, {}, clear=True), \
                patch("boto3.client"):
            with self.assertRaises(RuntimeError):
                report.save_html_to_s3("<html></html>")

    def test_the_upload_is_marked_uncacheable(self):
        """The page is regenerated daily; a cached copy is indistinguishable
        from the report having stopped."""
        env = {"BUCKET_NAME": "example.com", "BUCKET_KEY": "report.html"}
        with _no_dotenv(), patch.dict(os.environ, env, clear=True), \
                patch("boto3.client") as client:
            url = report.save_html_to_s3("<html>body</html>")

        kwargs = client.return_value.put_object.call_args.kwargs
        self.assertEqual(kwargs["Bucket"], "example.com")
        self.assertEqual(kwargs["ContentType"], "text/html")
        self.assertIn("no-store", kwargs["CacheControl"])
        self.assertEqual(url, "https://www.example.com/report.html")


class LocalOutputTest(unittest.TestCase):
    def test_output_path_is_created_and_written(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "report.html"
            with patch.object(report, "build_report", return_value="<html>ok</html>"), \
                    patch("sys.stdout", new_callable=io.StringIO):
                code = report.main(["--output", str(target)])

            self.assertEqual(code, 0)
            self.assertEqual(target.read_text(), "<html>ok</html>")

    def test_the_default_output_lands_in_the_repo_root(self):
        """Not scripts/ — the Pi and the S3 key both assume the old location."""
        self.assertEqual(
            report.parse_args([]).output, None
        )
        self.assertEqual(report.REPO_ROOT.name, "StockPortfolioManager")


class TemplateLocationTest(unittest.TestCase):
    """The one line that could not move verbatim: it was
    Path(__file__).parent, which meant the repo root while the function lived
    in main.py and would mean scripts/ here — silently generating a second,
    divergent copy of the template."""

    def test_the_template_loader_points_at_the_repo_root_templates_dir(self):
        empty = MagicMock()
        empty.list_stocks.return_value = []

        with patch.object(report, "create_portfolio_charts", return_value=("img", 1, 2)), \
                patch.object(report, "Environment") as env_cls, \
                patch.object(report, "FileSystemLoader") as loader, \
                patch.object(report, "create_template_file") as write_template:
            env_cls.return_value.get_template.return_value.render.return_value = "<html/>"
            report.create_portfolio_html(portfolio=empty, watchlist=empty)

        self.assertEqual(loader.call_args.args[0], report.REPO_ROOT / "templates")
        self.assertNotEqual(
            loader.call_args.args[0], Path(report.__file__).parent / "templates"
        )
        # The committed template is already there, so nothing regenerates it.
        write_template.assert_not_called()


if __name__ == "__main__":
    unittest.main()
