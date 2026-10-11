"""prod-rollout.yml's single-approval wiring (issue #346).

GitHub asks for an environment approval only once a job's `needs` have finished, and asks
again for every job on that environment. So: one fast `preflight` job with no environment
and no prod credentials, then exactly one `environment: prod` job holding every prod
action, with the gate run inside it before the prod auth step. Workflow text only.
"""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROD_YML = ROOT / ".github" / "workflows" / "prod-rollout.yml"


def _text(step):
    return "\n".join(str(v) for v in (step.get("name"), step.get("uses"), step.get("run"),
                                      step.get("with"), step.get("env")) if v)


class ProdRolloutWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = yaml.safe_load(PROD_YML.read_text())
        cls.jobs = cls.wf["jobs"]
        cls.prod_jobs = [n for n, j in cls.jobs.items() if j.get("environment") == "prod"]
        cls.prod = cls.jobs[cls.prod_jobs[0]] if cls.prod_jobs else {}
        cls.steps = [_text(s) for s in cls.prod.get("steps", [])]

    def index(self, needle):
        hits = [i for i, t in enumerate(self.steps) if needle in t]
        self.assertTrue(hits, f"no step in the prod job mentions {needle!r}")
        return hits[0]

    def test_dispatch_only_and_never_cancelled(self):
        self.assertEqual(list(self.wf[True]), ["workflow_dispatch"])  # yaml reads `on` as True
        self.assertEqual(self.wf["concurrency"],
                         {"group": "prod-rollout", "cancel-in-progress": False})

    def test_preflight_has_no_environment_and_no_prod_credentials(self):
        pre = self.jobs["preflight"]
        self.assertNotIn("environment", pre)
        body = yaml.safe_dump(pre)
        self.assertIn("secrets.GCP_WIF_PROVIDER", body)
        self.assertNotIn("GCP_PROD_", body)

    def test_exactly_one_prod_job_and_it_needs_only_preflight(self):
        self.assertEqual(len(self.prod_jobs), 1, self.prod_jobs)
        self.assertIn(self.prod.get("needs"), ("preflight", ["preflight"]))
        others = set(self.jobs) - {"preflight", self.prod_jobs[0]}
        self.assertFalse(others, f"a third job would need its own approval or run unapproved: {others}")

    def test_gate_runs_before_prod_auth_and_auth_before_every_prod_action(self):
        auth = self.index("GCP_PROD_WIF_PROVIDER")
        for gate in ("unittest discover", "ci_wrapper_smoke.py", "check_openapi_snapshot.py"):
            self.assertLess(self.index(gate), auth, gate)
        order = [auth, self.index("--missing-manual"), self.index("imagetools create"),
                 self.index("ci_migrate.sh"), self.index("cloudrun_services.py deploy")]
        self.assertEqual(order, sorted(order), "auth -> manual check -> promote -> migrate -> roll out")

    def test_promote_copies_the_preflight_digests_not_the_tag(self):
        promote = self.prod["steps"][self.index("imagetools create")]
        env = yaml.safe_dump(promote.get("env", {}))
        self.assertIn("needs.preflight.outputs.api_digest", env)
        self.assertIn("needs.preflight.outputs.migrate_digest", env)
        self.assertNotIn("inputs.image_tag", env)
        self.assertIn("@$DIGEST", promote["run"].replace("${DIGEST}", "$DIGEST"))

    def test_promoted_sha_is_recorded_last_and_only_on_success(self):
        last = self.prod["steps"][-1]
        self.assertTrue(last.get("uses", "").startswith("actions/upload-artifact@"))
        self.assertEqual(last["with"]["name"], "promoted-sha")
        self.assertNotIn("if", last)


if __name__ == "__main__":
    unittest.main()
