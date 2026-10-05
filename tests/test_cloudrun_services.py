"""scripts/cloudrun_services.py and deploy/cloudrun-services.toml — the service inventory (#161).

The script runs in-process against a stub `gcloud` on PATH that records every call and
answers `describe` / `get-iam-policy` from JSON files; nothing reaches Google Cloud. The
inventory tests read the real manifest, so a wrapper added there without its image, smoke
entry or module fails here rather than on the roll-out.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "cloudrun_services.py"
_spec = importlib.util.spec_from_file_location("cloudrun_services", SCRIPT)
crs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crs)

REG = "us-central1-docker.pkg.dev/proj-t/quantcore"

MANIFEST = """
[environments.test]
project = "proj-t"
project_number = "111"
region = "us-central1"
runtime_sa = "run@{project}.iam.gserviceaccount.com"
api_url = "https://api-{project_number}.{region}.run.app"

[environments.prod]
project = "proj-p"
project_number = "222"
region = "us-central1"
runtime_sa = "run@{project}.iam.gserviceaccount.com"
api_url = "https://api-{project_number}.{region}.run.app"

[[services]]
name = "svc-api"
image = "quantcore-api"
phase = 1
first_create = "manual"
runbook = "RUNBOOK.md step 3"
auth = "public"
port = 8080
cpu = "2"
memory = "4Gi"
cpu_boost = true
timeout = 300
concurrency = 160
max_instances = 3
ingress = "all"
service_account = "{runtime_sa}"
cloudsql = ["{project}:{region}:db"]
[services.env]
MODE = "x"
[services.secrets]
DSN = { test = "dsn-t:latest", prod = "dsn-p:latest" }

[[services]]
name = "svc-proxy"
image = "quantcore-keyproxy"
phase = 1
first_create = "manual"
runbook = "PROXY.md"
auth = "private"
skip_without_digest = true
port = 8080
cpu = "1"
memory = "512Mi"
cpu_boost = true
timeout = 300
concurrency = 80
max_instances = 1
ingress = "all"
service_account = "proxy@{project}.iam.gserviceaccount.com"

[[services]]
name = "svc-wrap"
image = "quantcore-mcp"
phase = 2
first_create = "auto"
auth = "public"
port = 8080
cpu = "1"
memory = "512Mi"
cpu_boost = true
timeout = 900
concurrency = 80
max_instances = 2
ingress = "all"
service_account = "{runtime_sa}"
[services.env]
SERVER_MODULE = "fastMCPTest.wrap_server"
QUANTCORE_REST_URL = "{api_url}"
"""

# Logs every call to $STUB_LOG. `$4` is the service name for describe/get-iam-policy.
STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$*" in
  "run services describe "*)
    [[ -n "${STUB_DESCRIBE_ERR:-}" ]] && { echo "ERROR: PERMISSION_DENIED" >&2; exit 1; }
    f="$STUB_DIR/describe-$4.json"
    [[ -f "$f" ]] && { cat "$f"; exit 0; }
    echo "ERROR: (gcloud.run.services.describe) Cannot find service [$4]" >&2; exit 1 ;;
  "run services get-iam-policy "*)
    f="$STUB_DIR/iam-$4.json"
    if [[ -f "$f" ]]; then cat "$f"; else echo '{}'; fi ;;
  "run services add-iam-policy-binding "*) [[ -z "${STUB_BIND_FAILS:-}" ]] ;;
  "run deploy "*) [[ -z "${STUB_DEPLOY_FAILS:-}" ]] ;;
esac
"""

PUBLIC = {"bindings": [{"role": "roles/run.invoker", "members": ["allUsers"]}]}


def live_json(s, env=None, secrets=None, **scalars):
    """A `gcloud run services describe --format=json` body matching resolved service `s`."""
    s = {**s, **scalars}
    env = s["env"] if env is None else env
    secrets = s["secrets"] if secrets is None else secrets
    tmpl_ann = {"autoscaling.knative.dev/maxScale": str(s["max_instances"])}
    if s["cpu_boost"]:
        tmpl_ann["run.googleapis.com/startup-cpu-boost"] = "true"
    if s["cloudsql"]:
        tmpl_ann["run.googleapis.com/cloudsql-instances"] = ",".join(s["cloudsql"])
    env_list = [{"name": k, "value": v} for k, v in env.items()]
    for k, ref in secrets.items():
        name, key = ref.split(":")
        env_list.append({"name": k, "valueFrom": {"secretKeyRef": {"name": name, "key": key}}})
    return {
        "metadata": {"annotations": {"run.googleapis.com/ingress": s["ingress"]}},
        "spec": {"template": {
            "metadata": {"annotations": tmpl_ann},
            "spec": {
                "containerConcurrency": s["concurrency"],
                "timeoutSeconds": s["timeout"],
                "serviceAccountName": s["service_account"],
                "containers": [{
                    "ports": [{"containerPort": s["port"]}],
                    "resources": {"limits": {"cpu": s["cpu"], "memory": s["memory"]}},
                    "env": env_list,
                }],
            },
        }},
    }


class StubbedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        gcloud = self.dir / "gcloud"
        gcloud.write_text(STUB)
        gcloud.chmod(gcloud.stat().st_mode | stat.S_IEXEC)
        self.log = self.dir / "calls.log"
        self.manifest = self.dir / "inventory.toml"
        self.manifest.write_text(MANIFEST)
        patcher = mock.patch.object(crs, "MANIFEST", self.manifest)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.svc = {s["name"]: s for s in crs.load("test")}

    def live(self, name, iam=None, **kw):
        """Make `name` exist in the stub, with its manifest config unless overridden."""
        (self.dir / f"describe-{name}.json").write_text(json.dumps(live_json(self.svc[name], **kw)))
        if iam is not None:
            (self.dir / f"iam-{name}.json").write_text(json.dumps(iam))

    def run_cli(self, *argv, **flags):
        env = {"PATH": f"{self.dir}:{os.environ['PATH']}", "STUB_LOG": str(self.log),
               "STUB_DIR": str(self.dir), **flags}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            rc = crs.main(list(argv))
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return rc, out.getvalue() + err.getvalue(), calls

    def deploy(self, name, *extra, **flags):
        how = extra or ("--tag", "abc1234")
        return self.run_cli("deploy", name, "--env", "test", "--registry", REG, *how, **flags)

    @staticmethod
    def find(calls, prefix):
        return [c for c in calls if c.startswith(prefix)]


class DeployExistingTest(StubbedTest):
    def test_no_drift_rolls_the_image_and_reasserts_every_scalar(self):
        self.live("svc-wrap", iam=PUBLIC)
        rc, out, calls = self.deploy("svc-wrap")
        self.assertEqual(rc, 0, out)
        (dep,) = self.find(calls, "run deploy svc-wrap")
        self.assertIn(f"--image {REG}/quantcore-mcp:abc1234", dep)
        self.assertIn("--project proj-t --region us-central1", dep)
        self.assertIn("--port 8080 --cpu 1 --memory 512Mi --timeout 900 --concurrency 80 "
                      "--max-instances 2 --ingress all "
                      "--service-account run@proj-t.iam.gserviceaccount.com --cpu-boost", dep)
        self.assertNotIn("--update-", dep)
        self.assertNotIn("--set-", dep)
        self.assertNotIn("allow-unauthenticated", dep)  # either form makes gcloud set IAM
        self.assertTrue(dep.endswith("--quiet"))
        self.assertEqual(self.find(calls, "run services add-iam-policy-binding"), [])
        self.assertNotIn("differs", out)

    def test_only_differing_env_is_updated_and_extra_live_keys_are_kept(self):
        env = {**self.svc["svc-wrap"]["env"], "SERVER_MODULE": "old.module", "EXTRA": "kept"}
        self.live("svc-wrap", iam=PUBLIC, env=env)
        rc, out, calls = self.deploy("svc-wrap")
        self.assertEqual(rc, 0, out)
        (dep,) = self.find(calls, "run deploy svc-wrap")
        self.assertIn("--update-env-vars SERVER_MODULE=fastMCPTest.wrap_server --", dep)
        self.assertNotIn("QUANTCORE_REST_URL", dep)
        self.assertNotIn("EXTRA", dep)
        self.assertIn("svc-wrap differs from the inventory, applying: env.SERVER_MODULE", out)

    def test_missing_secret_and_cloudsql_are_added(self):
        self.live("svc-api", iam=PUBLIC, secrets={}, cloudsql=[])
        rc, out, calls = self.deploy("svc-api")
        self.assertEqual(rc, 0, out)
        (dep,) = self.find(calls, "run deploy svc-api")
        self.assertIn("--update-secrets DSN=dsn-t:latest", dep)
        self.assertIn("--add-cloudsql-instances proj-t:us-central1:db", dep)
        self.assertNotIn("--update-env-vars", dep)

    def test_a_scalar_change_is_applied_and_reported(self):
        self.live("svc-wrap", iam=PUBLIC, memory="1Gi", cpu_boost=False)
        rc, out, calls = self.deploy("svc-wrap")
        self.assertEqual(rc, 0, out)
        self.assertIn("memory: live '1Gi', manifest '512Mi'", out)
        self.assertIn("cpu_boost: live False, manifest True", out)

    def test_a_public_service_without_its_allusers_binding_fails(self):
        self.live("svc-wrap", iam={"bindings": []})
        rc, out, calls = self.deploy("svc-wrap")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=svc-wrap not public::", out)
        self.assertIn("gcloud run services add-iam-policy-binding svc-wrap --project proj-t "
                      "--region us-central1 --member=allUsers --role=roles/run.invoker", out)
        self.assertEqual(self.find(calls, "run services add-iam-policy-binding"), [])

    def test_a_private_service_never_has_its_iam_read(self):
        self.live("svc-proxy")
        rc, out, calls = self.deploy("svc-proxy")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.find(calls, "run services get-iam-policy"), [])

    def test_a_failed_deploy_fails(self):
        self.live("svc-wrap", iam=PUBLIC)
        rc, _, _ = self.deploy("svc-wrap", STUB_DEPLOY_FAILS="1")
        self.assertEqual(rc, 1)

    def test_a_describe_failure_other_than_not_found_deploys_nothing(self):
        # A permission error must not be read as "missing" and trigger a create.
        rc, out, calls = self.deploy("svc-wrap", STUB_DESCRIBE_ERR="1")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=gcloud failed::describing svc-wrap failed", out)
        self.assertEqual(self.find(calls, "run deploy"), [])


class DeployMissingTest(StubbedTest):
    def test_an_auto_service_is_created_with_its_full_config_then_made_public(self):
        rc, out, calls = self.deploy("svc-wrap")
        self.assertEqual(rc, 0, out)
        self.assertEqual([c.split(" svc-wrap")[0] for c in calls],
                         ["run services describe", "run deploy",
                          "run services add-iam-policy-binding"])
        (dep,) = self.find(calls, "run deploy svc-wrap")
        self.assertIn("--update-env-vars SERVER_MODULE=fastMCPTest.wrap_server,"
                      "QUANTCORE_REST_URL=https://api-111.us-central1.run.app", dep)
        self.assertIn("--port 8080", dep)
        self.assertNotIn("PORT=", dep)
        (bind,) = self.find(calls, "run services add-iam-policy-binding svc-wrap")
        self.assertIn("--member=allUsers --role=roles/run.invoker", bind)
        self.assertIn("creating it from the inventory", out)

    def test_a_failed_iam_bind_fails_with_the_grant_command(self):
        rc, out, _ = self.deploy("svc-wrap", STUB_BIND_FAILS="1")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=svc-wrap not public::svc-wrap was created", out)
        self.assertIn("--member=allUsers --role=roles/run.invoker", out)

    def test_a_failed_create_does_not_bind(self):
        rc, _, calls = self.deploy("svc-wrap", STUB_DEPLOY_FAILS="1")
        self.assertEqual(rc, 1)
        self.assertEqual(self.find(calls, "run services add-iam-policy-binding"), [])

    def test_a_manual_service_fails_naming_its_runbook_and_changes_nothing(self):
        rc, out, calls = self.deploy("svc-api")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=svc-api missing::", out)
        self.assertIn("RUNBOOK.md step 3", out)
        self.assertEqual([c for c in calls if not c.startswith("run services describe")], [])


class ByDigestTest(StubbedTest):
    def test_the_promoted_digest_is_deployed(self):
        self.live("svc-wrap", iam=PUBLIC)
        rc, out, calls = self.deploy("svc-wrap", "--by-digest",
                                     QUANTCORE_MCP_DIGEST="sha256:feed")
        self.assertEqual(rc, 0, out)
        (dep,) = self.find(calls, "run deploy svc-wrap")
        self.assertIn(f"--image {REG}/quantcore-mcp@sha256:feed", dep)

    def test_a_missing_digest_fails_before_any_call(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("QUANTCORE_MCP_DIGEST", None)
            rc, out, calls = self.deploy("svc-wrap", "--by-digest")
        self.assertEqual(rc, 1)
        self.assertIn("::error title=svc-wrap has no image::QUANTCORE_MCP_DIGEST is unset", out)
        self.assertEqual(calls, [])

    def test_an_optional_image_skips_without_its_digest(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("QUANTCORE_KEYPROXY_DIGEST", None)
            rc, out, calls = self.deploy("svc-proxy", "--by-digest")
        self.assertEqual(rc, 0)
        self.assertIn("skipping svc-proxy", out)
        self.assertEqual(calls, [])

    def test_tag_and_digest_are_exclusive_and_one_is_required(self):
        for how in ((), ("--tag", "x", "--by-digest")):
            with self.subTest(how=how), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                crs.main(["deploy", "svc-wrap", "--env", "test", "--registry", REG, *how])


class NamesAndCheckTest(StubbedTest):
    def test_names_by_phase(self):
        self.assertEqual(self.run_cli("names", "--env", "test", "--phase", "1")[1].split(),
                         ["svc-api", "svc-proxy"])
        self.assertEqual(self.run_cli("names", "--env", "test", "--phase", "2")[1].split(),
                         ["svc-wrap"])
        self.assertEqual(len(self.run_cli("names", "--env", "prod")[1].split()), 3)

    def test_per_environment_values_resolve(self):
        prod = {s["name"]: s for s in crs.load("prod")}
        self.assertEqual(prod["svc-api"]["secrets"]["DSN"], "dsn-p:latest")
        self.assertEqual(prod["svc-api"]["cloudsql"], ["proj-p:us-central1:db"])
        self.assertEqual(prod["svc-wrap"]["env"]["QUANTCORE_REST_URL"],
                         "https://api-222.us-central1.run.app")

    def test_check_all_ok(self):
        self.live("svc-api", iam=PUBLIC)
        self.live("svc-proxy")
        self.live("svc-wrap", iam=PUBLIC)
        rc, out, calls = self.run_cli("check", "--env", "test")
        self.assertEqual(rc, 0, out)
        self.assertIn("test: 0 service(s) differ", out)
        self.assertEqual(self.find(calls, "run deploy"), [])  # read-only

    def test_check_reports_drift_auth_and_missing(self):
        self.live("svc-api", iam=PUBLIC, memory="2Gi")
        self.live("svc-proxy", iam=PUBLIC)  # a private service open to allUsers
        rc, out, calls = self.run_cli("check", "--env", "test")
        self.assertEqual(rc, 1)
        self.assertIn("svc-api: DRIFT\n  memory: live '2Gi', manifest '4Gi'", out)
        self.assertIn("svc-proxy: DRIFT\n  auth: allUsers invoker present, manifest auth=private",
                      out)
        # A missing auto service isn't drift: the next roll-out creates it.
        self.assertIn("svc-wrap: MISSING (first_create=auto", out)
        self.assertIn("test: 2 service(s) differ", out)
        self.assertEqual(self.find(calls, "run deploy"), [])
        self.assertEqual(self.find(calls, "run services add-iam-policy-binding"), [])

    def test_check_counts_a_missing_manual_service(self):
        self.live("svc-wrap", iam=PUBLIC)
        self.live("svc-proxy")
        rc, out, _ = self.run_cli("check", "--env", "test")
        self.assertEqual(rc, 1)
        self.assertIn("svc-api: MISSING (manual first deploy: RUNBOOK.md step 3)", out)


class ValidationTest(StubbedTest):
    def assert_refused(self, old, new, message):
        self.manifest.write_text(MANIFEST.replace(old, new, 1))
        rc, out, calls = self.run_cli("names", "--env", "test")
        self.assertEqual(rc, 2)
        self.assertIn("::error title=service inventory::", out)
        self.assertIn(message, out)
        self.assertEqual(calls, [])

    def test_port_is_reserved(self):
        self.assert_refused('MODE = "x"', 'PORT = "8080"', "PORT is reserved")

    def test_commas_are_refused(self):
        self.assert_refused('MODE = "x"', 'MODE = "a,b"', "MODE contains a comma")

    def test_only_a_public_service_may_be_auto_created(self):
        self.assert_refused('first_create = "auto"\nauth = "public"',
                            'first_create = "auto"\nauth = "iap"', "only a public service")

    def test_a_manual_service_needs_a_runbook(self):
        self.assert_refused('runbook = "PROXY.md"\n', "", "needs a runbook")

    def test_phase(self):
        self.assert_refused("phase = 2", "phase = 3", "phase must be 1 or 2")

    def test_duplicate_names(self):
        self.assert_refused('name = "svc-proxy"', 'name = "svc-api"', "svc-api: listed twice")

    def test_a_key_cannot_be_both_env_and_secret(self):
        self.assert_refused('MODE = "x"', 'DSN = "x"', "both an env var and a secret")

    def test_a_missing_required_field(self):
        self.assert_refused('memory = "512Mi"\ncpu_boost = true\ntimeout = 900',
                            'cpu_boost = true\ntimeout = 900', "svc-wrap: missing memory")

    def test_a_per_environment_value_must_cover_the_environment(self):
        self.assert_refused('{ test = "dsn-t:latest", prod = "dsn-p:latest" }',
                            '{ prod = "dsn-p:latest" }', "no value for environment 'test'")

    def test_an_unknown_template(self):
        self.assert_refused('MODE = "x"', 'MODE = "{nope}"', "unknown template {nope}")

    def test_an_unknown_environment(self):
        rc, out, _ = self.run_cli("names", "--env", "staging")
        self.assertEqual(rc, 2)
        self.assertIn("unknown environment 'staging'", out)


class RealInventoryTest(unittest.TestCase):
    """The checked-in manifest: everything it names exists, and the sizing rules hold."""

    @classmethod
    def setUpClass(cls):
        cls.envs = {e: crs.load(e) for e in ("test", "prod")}

    def test_both_environments_hold_the_same_services(self):
        names = {e: [s["name"] for s in ss] for e, ss in self.envs.items()}
        self.assertEqual(names["test"], names["prod"])
        self.assertIn("quantcore-api", names["test"])

    def test_api_and_keyproxy_roll_before_their_consumers(self):
        for s in self.envs["test"]:
            with self.subTest(service=s["name"]):
                expected = 1 if s["name"] in ("quantcore-api", "quantcore-keyproxy") else 2
                self.assertEqual(s["phase"], expected)

    def test_every_image_is_built_and_promoted(self):
        build = (ROOT / "cloudbuild.yaml").read_text()
        prod = (ROOT / ".github/workflows/prod-rollout.yml").read_text()
        loop = next(l for l in prod.splitlines() if l.strip().startswith("for img in"))
        promoted = loop.replace(";", " ").split()
        for image in {s["image"] for s in self.envs["prod"]}:
            with self.subTest(image=image):
                self.assertIn(f"/{image}:${{_TAG}}", build)
                self.assertIn(image, promoted)

    def test_wrappers_match_the_smoke_list_and_exist(self):
        smoke = set(re.findall(r'\("(fastMCPTest\.\w+)"',
                               (ROOT / "scripts/ci_wrapper_smoke.py").read_text()))
        deployed = {s["env"]["SERVER_MODULE"] for s in self.envs["test"]
                    if "SERVER_MODULE" in s["env"]}
        self.assertEqual(deployed, smoke, "a wrapper must be both smoke-tested and deployed")
        for module in deployed:
            self.assertTrue((ROOT / (module.replace(".", "/") + ".py")).is_file(), module)

    def test_runbooks_exist(self):
        for s in self.envs["test"]:
            if s["first_create"] == "manual":
                with self.subTest(service=s["name"]):
                    self.assertTrue((ROOT / s["runbook"].split()[0]).is_file(), s["runbook"])

    def test_sizing_rules(self):
        # CPU < 1 is rejected at concurrency > 1; api keeps 2 CPU / 4Gi and its boost (#280).
        for env, services in self.envs.items():
            for s in services:
                with self.subTest(env=env, service=s["name"]):
                    self.assertGreaterEqual(float(s["cpu"]), 1)
                    self.assertGreater(s["concurrency"], 1)
            api = next(s for s in services if s["name"] == "quantcore-api")
            self.assertEqual((api["cpu"], api["memory"], api["cpu_boost"]), ("2", "4Gi", True))

    def test_no_secret_values_in_the_manifest(self):
        for s in self.envs["prod"]:
            for key, ref in s["secrets"].items():
                with self.subTest(service=s["name"], secret=key):
                    self.assertRegex(ref, r"^[a-z0-9-]+:(latest|\d+)$")


class ScriptHygieneTest(unittest.TestCase):
    def test_no_set_flags_in_code(self):
        src = SCRIPT.read_text().split('"""', 2)[2]  # the docstring says "never --set-*"
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        self.assertNotIn("--set-", code)
        self.assertTrue(os.access(SCRIPT, os.X_OK))


if __name__ == "__main__":
    unittest.main()
