"""scripts/monitoring/ and scripts/auth_alerting.py — the G6 auth-failure alerting (#464).

The config tests read the real files, so a policy pointing at a metric that doesn't
exist, an identity label, or a committed address fails here rather than at `apply`. The
extractor tests run each metric's regex over the exact lines ``api/auth.py`` emits,
formatted as Cloud Logging receives them, so a change to the log wording that would
silently zero a metric fails here too. The script runs against a fake ``gcloud``;
nothing reaches Google Cloud.
"""
import importlib.util
import json
import logging
import os
import re
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from api import rate_limit
from api.auth import Principal, _log_rejection, require_owner, resolve_owner_or_none
from quantcore.services.identity import UnknownIdentityError

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "auth_alerting", ROOT / "scripts" / "auth_alerting.py"
)
aa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aa)

METRICS = aa.metrics()
POLICIES = aa.policies()
FILES = sorted(aa.MONITORING.glob("*.yaml"))
# api/main.py's basicConfig format: what Cloud Logging stores as textPayload.
CLOUD_FORMAT = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
IDENTITY_LIKE = ("identity", "sub", "email", "user", "owner", "principal", "token")


def _metric_of(policy: dict) -> str:
    f = policy["conditions"][0]["conditionThreshold"]["filter"]
    return re.search(r'metric\.type="logging\.googleapis\.com/user/(\w+)"', f).group(1)


def _extract(expr: str, text: str) -> str:
    """Evaluate ``REGEXP_EXTRACT(textPayload, "<re>")`` the way Cloud Logging does."""
    pattern = re.fullmatch(r'REGEXP_EXTRACT\(textPayload, "(.*)"\)', expr).group(1)
    m = re.search(pattern, text)
    return m.group(1) if m else ""


class MonitoringFilesTests(unittest.TestCase):
    def test_every_policy_counts_a_metric_defined_here(self):
        for p in POLICIES:
            with self.subTest(p["displayName"]):
                self.assertIn(_metric_of(p), METRICS)

    def test_policy_label_filters_name_labels_the_metric_has(self):
        for p in POLICIES:
            metric = METRICS[_metric_of(p)]
            labels = {lab["key"] for lab in metric["metricDescriptor"].get("labels", [])}
            f = p["conditions"][0]["conditionThreshold"]["filter"]
            for used in re.findall(r"metric\.label\.(\w+)", f):
                with self.subTest(p["displayName"], label=used):
                    self.assertIn(used, labels)

    def test_extractors_match_declared_labels(self):
        for name, m in METRICS.items():
            with self.subTest(name):
                declared = {lab["key"] for lab in m["metricDescriptor"].get("labels", [])}
                self.assertEqual(declared, set(m.get("labelExtractors", {})))

    def test_no_identity_label_anywhere(self):
        # Unbounded cardinality, and it would copy *who* into a metric.
        for name, m in METRICS.items():
            for lab in m["metricDescriptor"].get("labels", []):
                with self.subTest(name, label=lab["key"]):
                    self.assertFalse(any(w in lab["key"] for w in IDENTITY_LIKE))

    def test_auth_rejections_covers_both_statuses_and_both_deciders(self):
        f = " ".join(METRICS["auth_rejections"]["filter"].split())
        self.assertIn('log_id("run.googleapis.com/requests")', f)
        self.assertIn("httpRequest.status=(401 OR 403)", f)
        self.assertIn('"quantcore-api"', f)
        self.assertIn('"quantcore-keyproxy"', f)

    def test_nothing_environment_specific_is_committed(self):
        # The channel is merged in by the script; projects come from the inventory.
        for path in FILES:
            text = path.read_text()
            with self.subTest(path.name):
                self.assertNotIn("quantcore-test-", text)
                self.assertNotIn("quantcore-prod-", text)
                self.assertNotIn("@", text)
        for p in POLICIES:
            with self.subTest(p["displayName"]):
                self.assertNotIn("notificationChannels", p)

    def test_policies_are_documented_and_use_a_real_severity(self):
        for p in POLICIES:
            with self.subTest(p["displayName"]):
                self.assertIn(p["severity"], ("CRITICAL", "ERROR", "WARNING"))
                self.assertIs(p.get("enabled"), True)
                self.assertIn("First look", p["documentation"]["content"])
                self.assertTrue(p["displayName"].startswith("G6: "))


class ExtractorTests(unittest.TestCase):
    """The security_events regexes against the lines api/auth.py really writes."""

    def _lines(self, fn) -> list[str]:
        with self.assertLogs("quantcore.security", level="WARNING") as logs:
            fn()
        return [CLOUD_FORMAT.format(r) for r in logs.records]

    def _labels(self, line: str) -> dict:
        m = METRICS["security_events"]
        self.assertIn(m["filter"].split('textPayload:"')[1].rstrip('"'), line)
        return {k: _extract(v, line) for k, v in m["labelExtractors"].items()}

    def test_auth_rejected_reasons(self):
        for reason, identity in (("missing", None), ("invalid", None), ("expired", "sager")):
            with self.subTest(reason):
                (line,) = self._lines(lambda: _log_rejection(reason, identity))
                self.assertEqual(
                    self._labels(line), {"event": "auth_rejected", "reason": reason}
                )

    def test_unmapped_identity_events(self):
        principal = Principal.from_claims({"sub": "stranger@example.com"}, None)
        services = mock.MagicMock()
        services.identity.resolve_owner.side_effect = UnknownIdentityError("unmapped")

        def hard():
            try:
                require_owner(principal)
            except Exception:  # the 403 — tested in test_auth
                pass

        with mock.patch("api.auth.get_services", return_value=services):
            (hard_line,) = self._lines(hard)
            (soft_line,) = self._lines(lambda: resolve_owner_or_none(principal))
        self.assertEqual(self._labels(hard_line), {"event": "unmapped_principal", "reason": ""})
        self.assertEqual(
            self._labels(soft_line), {"event": "unmapped_principal_soft", "reason": ""}
        )

    def test_rate_limited_event(self):
        # G2 (#461): metric only until step 3, so no policy reads it yet -- but the
        # metric must already count the line the soak is measured by.
        principal = Principal.from_claims({"sub": "runaway"}, None)
        request = mock.MagicMock()
        request.scope = {"route": mock.MagicMock(path="/api/securities/{ticker}")}
        env = {rate_limit.MODE_ENV: "shadow", rate_limit.BURST_ENV: "1"}
        rate_limit.reset_limiter()
        self.addCleanup(rate_limit.reset_limiter)
        with mock.patch.dict(os.environ, env):
            rate_limit.enforce_rate_limit(request, principal)  # takes the one token

            (line,) = self._lines(lambda: rate_limit.enforce_rate_limit(request, principal))
        self.assertEqual(self._labels(line), {"event": "rate_limited", "reason": ""})

    def test_policy_label_values_are_values_the_metric_produces(self):
        produced = {"auth_rejected", "unmapped_principal", "unmapped_principal_soft",
                    "missing", "invalid", "expired"}
        for p in POLICIES:
            f = p["conditions"][0]["conditionThreshold"]["filter"]
            for clause in re.findall(r'metric\.label\.\w+=(?:one_of\(([^)]*)\)|"(\w+)")', f):
                values = re.findall(r'"(\w+)"', clause[0]) or [clause[1]]
                for v in values:
                    with self.subTest(p["displayName"], value=v):
                        self.assertIn(v, produced)


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.want = POLICIES[0]

    def _live(self, **changes):
        live = json.loads(json.dumps(self.want))
        # What the API adds and how it renders values: not drift.
        live["name"] = "projects/p/alertPolicies/1"
        live["conditions"][0]["name"] = "projects/p/alertPolicies/1/conditions/2"
        live["conditions"][0]["conditionThreshold"]["thresholdValue"] = float(
            live["conditions"][0]["conditionThreshold"]["thresholdValue"]
        )
        live["mutationRecord"] = {"mutateTime": "2026-10-28T00:00:00Z"}
        live["conditions"][0]["conditionThreshold"].update(changes)
        return live

    def test_api_additions_are_not_drift(self):
        self.assertEqual(aa.diff(self.want, self._live(), aa.POLICY_FIELDS), [])

    def test_whitespace_in_filters_is_not_drift(self):
        f = self.want["conditions"][0]["conditionThreshold"]["filter"]
        live = self._live(filter="  ".join(f.split()))
        self.assertEqual(aa.diff(self.want, live, aa.POLICY_FIELDS), [])

    def test_an_omitted_zero_threshold_is_not_drift(self):
        # proto3 JSON drops a number at its default; the real API returns no
        # thresholdValue for a policy whose file says 0.
        want = next(p for p in POLICIES
                    if p["conditions"][0]["conditionThreshold"]["thresholdValue"] == 0)
        live = json.loads(json.dumps(want))
        del live["conditions"][0]["conditionThreshold"]["thresholdValue"]
        self.assertEqual(aa.diff(want, live, aa.POLICY_FIELDS), [])

    def test_an_omitted_nonzero_threshold_is_drift(self):
        live = self._live()
        del live["conditions"][0]["conditionThreshold"]["thresholdValue"]
        self.assertEqual(len(aa.diff(self.want, live, aa.POLICY_FIELDS)), 1)

    def test_a_changed_threshold_is_drift(self):
        lines = aa.diff(self.want, self._live(thresholdValue=99), aa.POLICY_FIELDS)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("conditions:"))

    def test_a_missing_field_is_drift(self):
        live = self._live()
        del live["documentation"]
        self.assertEqual(len(aa.diff(self.want, live, aa.POLICY_FIELDS)), 1)


class FakeGcloud:
    """Answers the script's gcloud calls from dicts; records what was sent.

    ``fail`` names a command prefix (e.g. ``("monitoring", "policies", "create")``) that
    exits non-zero, for the failure paths.
    """

    def __init__(self, *, metrics=(), policies=(), channels=None, fail=None):
        self.metrics = set(metrics)
        self.policies = {name: f"projects/p/alertPolicies/{i}"
                         for i, name in enumerate(policies)}
        self.channels = dict(channels or {})  # {email: channel name}
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        self.sent: list[tuple[str, dict]] = []
        self.handlers = {
            ("logging", "metrics", "describe"): self._describe_metric,
            ("monitoring", "policies", "list"): self._list_policies,
            ("beta", "monitoring", "channels", "list"): self._list_channels,
            ("beta", "monitoring", "channels", "create"): self._create_channel,
        }

    def __call__(self, *args, capture=True):
        self.calls.append(args)
        self._record_sent(args)
        if self.fail and args[:len(self.fail)] == self.fail:
            return mock.Mock(returncode=1, stdout="", stderr="ERROR: PERMISSION_DENIED")
        handler = next((h for k, h in self.handlers.items() if args[:len(k)] == k), None)
        out, rc, err = handler(args) if handler else ("", 0, "")
        return mock.Mock(returncode=rc, stdout=out, stderr=err)

    def _record_sent(self, args):
        for flag in args:
            if flag.startswith(("--config-from-file=", "--policy-from-file=")):
                body = json.loads(Path(flag.split("=", 1)[1]).read_text())
                self.sent.append((" ".join(args[:3]), body))

    def _describe_metric(self, args):
        if args[3] in self.metrics:
            return json.dumps({"name": args[3], **METRICS[args[3]]}), 0, ""
        return "", 1, "ERROR: NOT_FOUND: metric does not exist"

    def _list_policies(self, args):
        by_name = {p["displayName"]: p for p in POLICIES}
        return json.dumps([
            {**by_name.get(d, {}), "name": n, "displayName": d,
             "notificationChannels": sorted(self.channels.values())}
            for d, n in self.policies.items()
        ]), 0, ""

    def _list_channels(self, args):
        return json.dumps([{"name": n, "labels": {"email_address": e}}
                           for e, n in self.channels.items()]), 0, ""

    def _create_channel(self, args):
        email = next(a for a in args if a.startswith("--channel-labels=")).split("=", 2)[2]
        name = f"projects/p/notificationChannels/{90 + len(self.channels)}"
        self.channels[email] = name
        return json.dumps({"name": name}), 0, ""


def _rewrite_policies(fake, change):
    """``fake`` with each listed policy passed through ``change``."""
    def wrapped(*args, capture=True):
        r = fake(*args, capture=capture)
        if args[:3] == ("monitoring", "policies", "list"):
            r.stdout = json.dumps([change(p) for p in json.loads(r.stdout)])
        return r
    return wrapped


def _complete_fake(**kw):
    kw.setdefault("channels", {"ops@example.com": "projects/p/notificationChannels/1"})
    return FakeGcloud(metrics=METRICS, policies=[p["displayName"] for p in POLICIES], **kw)


class CheckTests(unittest.TestCase):
    def _check(self, fake):
        out = StringIO()
        with mock.patch.object(aa, "gcloud", fake), redirect_stdout(out):
            rc = aa.main(["check", "--env", "test"])
        return rc, out.getvalue()

    def test_matching_project_is_clean(self):
        rc, out = self._check(_complete_fake())
        self.assertEqual(rc, 0, out)
        self.assertIn("0 object(s) differ", out)

    def test_empty_project_reports_everything_missing(self):
        rc, out = self._check(FakeGcloud())
        self.assertEqual(rc, 1)
        self.assertEqual(out.count(": MISSING"), len(METRICS) + 1 + len(POLICIES))

    def test_a_detached_channel_is_drift(self):
        fake = _rewrite_policies(_complete_fake(),
                                 lambda p: {**p, "notificationChannels": []})
        rc, out = self._check(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("not attached"), len(POLICIES))

    def test_one_of_two_channels_detached_is_drift(self):
        both = {"a@example.com": "projects/p/notificationChannels/1",
                "b@example.com": "projects/p/notificationChannels/2"}
        fake = _rewrite_policies(_complete_fake(channels=both), lambda p: {
            **p, "notificationChannels": ["projects/p/notificationChannels/1"]})
        rc, out = self._check(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("1 'QuantCore security alerts (email)' not attached"),
                         len(POLICIES))

    def test_a_disabled_policy_is_drift(self):
        # A disabled policy can't alert: the worst drift there is, so check must see it.
        fake = _rewrite_policies(_complete_fake(), lambda p: {**p, "enabled": False})
        rc, out = self._check(fake)
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("enabled: live false"), len(POLICIES))

    def test_a_describe_error_that_isnt_not_found_fails(self):
        # Only NOT_FOUND means MISSING; anything else (permissions) must not read as absent.
        fake = FakeGcloud(fail=("logging", "metrics", "describe"))
        with mock.patch("sys.stderr", StringIO()) as err:
            rc, _ = self._check(fake)
        self.assertEqual(rc, 1)
        self.assertIn("::error title=gcloud failed::", err.getvalue())

    def test_check_never_writes(self):
        fake = FakeGcloud()
        self._check(fake)
        writes = [c for c in fake.calls if {"create", "update"} & set(c)]
        self.assertEqual(writes, [])


class ApplyTests(unittest.TestCase):
    def _apply(self, fake, *args):
        with mock.patch.object(aa, "gcloud", fake), \
                mock.patch.object(aa, "cmd_check", return_value=0), \
                redirect_stdout(StringIO()):
            return aa.main(["apply", "--env", "test", *args])

    def test_first_apply_creates_everything_with_every_channel(self):
        fake = FakeGcloud()
        rc = self._apply(fake, "--email", "ops@example.com", "--email", "oncall@example.com")
        self.assertEqual(rc, 0)
        self.assertEqual(set(fake.channels), {"ops@example.com", "oncall@example.com"})
        verbs = [v for v, _ in fake.sent]
        self.assertEqual(verbs.count("logging metrics create"), len(METRICS))
        self.assertEqual(verbs.count("monitoring policies create"), len(POLICIES))
        for verb, body in fake.sent:
            if verb.startswith("monitoring policies"):
                self.assertEqual(body["notificationChannels"], sorted(fake.channels.values()))
        self.assertTrue(all("--project" in c and "quantcore-test-20260606" in c
                            for c in fake.calls))

    def test_update_keeps_the_channel(self):
        # --policy-from-file replaces the whole policy: without the merge, every
        # re-apply would detach the email channel.
        fake = _complete_fake()
        self.assertEqual(self._apply(fake), 0)
        updates = [b for v, b in fake.sent if v == "monitoring policies update"]
        self.assertEqual(len(updates), len(POLICIES))
        for body in updates:
            self.assertEqual(body["notificationChannels"], ["projects/p/notificationChannels/1"])
        self.assertFalse(any(c[:4] == ("beta", "monitoring", "channels", "create")
                             for c in fake.calls))

    def test_no_channel_and_no_email_refuses_before_any_policy(self):
        fake = FakeGcloud(metrics=METRICS)
        with redirect_stdout(StringIO()), mock.patch("sys.stderr", StringIO()):
            self.assertEqual(self._apply(fake), 2)
        self.assertFalse(any(v.startswith("monitoring policies") for v, _ in fake.sent))

    def test_every_policy_is_sent_enabled(self):
        # So a re-apply turns back on a policy someone disabled in the console.
        fake = _complete_fake()
        self._apply(fake)
        for verb, body in fake.sent:
            if verb.startswith("monitoring policies"):
                self.assertIs(body["enabled"], True)

    def test_a_failed_policy_write_exits_nonzero(self):
        fake = _complete_fake(fail=("monitoring", "policies", "update"))
        with mock.patch("sys.stderr", StringIO()) as err:
            self.assertEqual(self._apply(fake), 1)
        self.assertIn("::error title=gcloud failed::", err.getvalue())
        self.assertNotIn("::warning::", err.getvalue())

    def test_a_failure_after_creating_a_metric_says_to_rerun(self):
        fake = FakeGcloud(channels={"ops@example.com": "projects/p/notificationChannels/1"},
                          fail=("monitoring", "policies", "create"))
        with mock.patch("sys.stderr", StringIO()) as err:
            self.assertEqual(self._apply(fake), 1)
        self.assertIn("wait a minute and re-run apply", err.getvalue())

    def test_an_existing_address_is_reused_and_a_new_one_added(self):
        fake = _complete_fake()
        rc = self._apply(fake, "--email", "ops@example.com", "--email", "new@example.com")
        self.assertEqual(rc, 0)
        creates = [c for c in fake.calls if c[:4] == ("beta", "monitoring", "channels", "create")]
        self.assertEqual(len(creates), 1)
        self.assertIn("--channel-labels=email_address=new@example.com", creates[0])
        for verb, body in fake.sent:
            if verb.startswith("monitoring policies"):
                self.assertEqual(len(body["notificationChannels"]), 2)

    def test_reapply_without_email_keeps_every_channel(self):
        both = {"a@example.com": "projects/p/notificationChannels/1",
                "b@example.com": "projects/p/notificationChannels/2"}
        fake = _complete_fake(channels=both)
        self.assertEqual(self._apply(fake), 0)
        for verb, body in fake.sent:
            if verb.startswith("monitoring policies"):
                self.assertEqual(body["notificationChannels"], sorted(both.values()))

    def test_prod_asks_first(self):
        fake = FakeGcloud()
        with mock.patch.object(aa, "gcloud", fake), \
                mock.patch("builtins.input", return_value="n"), \
                redirect_stdout(StringIO()):
            self.assertEqual(aa.main(["apply", "--env", "prod"]), 1)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
