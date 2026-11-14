"""The extracted HTML report script (#147, Part F).

The four rendering functions moved out of main.py verbatim and are covered by
the report they produce. What is *new* here is the CLI around them, and it
exists to close the specific hole issue #147 describes: the old code returned
``None`` when BUCKET_NAME/BUCKET_KEY were missing and let the caller carry on
reporting success, so the published page could stop updating without anyone
noticing. These tests pin the two halves of that fix — a precondition check
before the expensive work, and a hard failure at the upload itself.

The template-path test guards the one line that could not move verbatim.

**Why the import below is guarded.** The script under test is the one place
that legitimately imports matplotlib/jinja2/boto3 at module level — it runs on
the Pi, never in a container, which is what ``requirements-report.txt`` exists
to say. But ``prod-rollout.yml`` runs the whole suite on the *lean* install
(``requirements-base.txt``), so an unguarded import here is not a failing test,
it is a promotion that cannot leave the gate. The skip is deliberately narrow:
only the three report-only packages are tolerated, so a genuine broken import
in the script still errors the run.
"""
import io
import os
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from portfolio.metrics import Metrics
from portfolio.stock import Stock

# requirements-report.txt — installed by requirements-dev.txt (deploy.yml, where
# these tests actually run and are measured for coverage) and by nothing lean.
REPORT_ONLY_DEPENDENCIES = {"matplotlib", "jinja2", "boto3"}

try:
    import scripts.generate_portfolio_report as report
except ImportError as exc:
    if (exc.name or "").split(".")[0] not in REPORT_ONLY_DEPENDENCIES:
        raise
    raise unittest.SkipTest(
        f"{exc.name} is not installed; the report script's tests need the "
        "report dependency set (pip install -r requirements-report.txt)"
    ) from exc


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
        """Not scripts/ — the Pi and the S3 key both assume the old location.

        Asserted as a path relationship rather than a directory name: the
        checkout is not required to be called anything in particular.
        """
        self.assertIsNone(report.parse_args([]).output)

        script_dir = Path(report.__file__).resolve().parent
        self.assertEqual(report.REPO_ROOT, script_dir.parent)
        self.assertTrue((report.REPO_ROOT / "main.py").is_file())
        self.assertTrue((report.REPO_ROOT / "templates").is_dir())


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


class ReportRowTest(unittest.TestCase):
    """The row builders extracted from create_portfolio_html (#431 plan step
    1.4). The extraction changed no behaviour, so these pin what the rows
    already said, quirks included."""

    AVERAGES_AND_RETURNS = (
        "ten_day_moving_average", "thirty_day_moving_average",
        "fifty_day_moving_average", "one_hundred_day_moving_average",
        "two_hundred_day_moving_average", "five_day_return",
        "thirty_day_return", "ninety_day_return", "ytd_return",
        "one_year_return",
    )

    def _stock(self, current_price=40.0, **metrics):
        stock = Stock(
            name="ZZ Row Test", symbol="ZZROW", quantity=Decimal("2.500000"),
            purchase_price=50.0, purchase_date=date.today() - timedelta(days=10),
            current_price=current_price,
        )
        stock.metrics = Metrics(**metrics)
        return stock

    def test_a_portfolio_row_carries_the_holding_and_its_metrics(self):
        stock = self._stock(
            ten_day_moving_average=41.5, percent_change_today=1.25,
            five_day_return=-2.0, one_year_return=12.0,
        )
        stock.earnings_date = date(2026, 12, 1)

        row = report._portfolio_row(stock)

        self.assertEqual(row["name"], "ZZ Row Test")
        self.assertEqual(row["symbol"], "ZZROW")
        self.assertEqual(row["purchase_price"], 50.0)
        self.assertEqual(row["current_price"], 40.0)
        self.assertEqual(row["quantity"], "2.5")
        self.assertAlmostEqual(row["gain_loss"], -25.0)
        self.assertAlmostEqual(float(row["gain_loss_pct"]), -20.0)
        self.assertEqual(row["days_held"], 10)
        self.assertAlmostEqual(row["dollars_per_day"], -2.5)
        self.assertEqual(row["earnings_date"], "2026-12-01")
        self.assertEqual(row["ten_day_moving_average"], 41.5)
        self.assertEqual(row["percent_change_today"], 1.25)
        self.assertEqual(row["five_day_return"], -2.0)
        self.assertEqual(row["one_year_return"], 12.0)
        self.assertEqual(
            set(row),
            {"name", "symbol", "purchase_price", "current_price", "quantity",
             "gain_loss", "gain_loss_pct", "days_held", "dollars_per_day",
             "earnings_date", "percent_change_today", *self.AVERAGES_AND_RETURNS},
        )

    def test_a_portfolio_row_without_a_price_says_not_available(self):
        row = report._portfolio_row(self._stock(current_price=None))

        for key in ("current_price", "gain_loss", "gain_loss_pct",
                    "dollars_per_day", "earnings_date"):
            self.assertEqual(row[key], "N/A", key)

    def test_a_portfolio_row_keeps_a_zero_return_but_not_a_zero_change_today(self):
        """The holdings table has always printed a zero return as 0 and a zero
        change today as N/A."""
        row = report._portfolio_row(
            self._stock(five_day_return=0.0, percent_change_today=0.0)
        )

        self.assertEqual(row["five_day_return"], 0.0)
        self.assertEqual(row["percent_change_today"], "N/A")

    def test_a_portfolio_row_without_metrics_still_raises(self):
        """percent_change_today is read without checking for metrics, so a
        stock with none has always failed the report rather than print N/A."""
        stock = self._stock()
        stock.metrics = None

        with self.assertRaises(AttributeError):
            report._portfolio_row(stock)

    def test_a_watchlist_row_carries_the_price_and_metrics(self):
        stock = self._stock(
            two_hundred_day_moving_average=38.0, percent_change_today=-0.5,
            ytd_return=7.0,
        )

        row = report._watchlist_row(stock)

        self.assertEqual(row["name"], "ZZ Row Test")
        self.assertEqual(row["symbol"], "ZZROW")
        self.assertEqual(row["current_price"], 40.0)
        self.assertEqual(row["two_hundred_day_moving_average"], 38.0)
        self.assertEqual(row["percent_change_today"], -0.5)
        self.assertEqual(row["ytd_return"], 7.0)
        self.assertEqual(
            set(row),
            {"name", "symbol", "current_price", "percent_change_today",
             *self.AVERAGES_AND_RETURNS},
        )

    def test_a_watchlist_row_says_not_available_for_a_missing_or_zero_figure(self):
        row = report._watchlist_row(
            self._stock(current_price=None, five_day_return=0.0)
        )

        self.assertEqual(row["current_price"], "N/A")
        self.assertEqual(row["five_day_return"], "N/A")   # zero
        self.assertEqual(row["one_year_return"], "N/A")   # missing

    def test_the_rows_reach_the_template(self):
        held, watched = self._stock(), self._stock()
        portfolio, watchlist = MagicMock(), MagicMock()
        portfolio.list_stocks.return_value = [held]
        watchlist.list_stocks.return_value = [watched]

        with patch.object(report, "create_portfolio_charts", return_value=("img", 1, 2)), \
                patch.object(report, "Environment") as env_cls, \
                patch.object(report, "FileSystemLoader"):
            render = env_cls.return_value.get_template.return_value.render
            report.create_portfolio_html(portfolio=portfolio, watchlist=watchlist)

        context = render.call_args.kwargs
        self.assertEqual(context["stock_details"], [report._portfolio_row(held)])
        self.assertEqual(context["watchlist_details"], [report._watchlist_row(watched)])


if __name__ == "__main__":
    unittest.main()
