"""scripts/check_cloudbuild.py — the PR-time guard on cloudbuild.yaml."""
import unittest
from pathlib import Path

import yaml

from scripts.check_cloudbuild import PATH, check

T = "r-docker.pkg.dev/p/q/quantcore-%s:${_TAG}"


C = "r-docker.pkg.dev/p/q/quantcore-%s:${_CACHE_TAG}"


def step(img):
    return {
        "id": f"build-{img}",
        "env": ["DOCKER_BUILDKIT=1"],
        "args": ["build", "--cache-from", C % img,
                 "--build-arg", "BUILDKIT_INLINE_CACHE=1", "-t", T % img, "."],
    }


class CheckCloudbuildTest(unittest.TestCase):
    def test_committed_file_is_valid(self):
        self.assertEqual(check(yaml.safe_load(Path(PATH).read_text())), [])

    def test_consistent_config_passes(self):
        doc = {"steps": [step("api")], "images": [T % "api"]}
        self.assertEqual(check(doc), [])

    def test_image_line_inside_a_step_is_caught(self):
        # The original defect: a stray list item broke the step's args.
        doc = {"steps": [step("api"), "oops"], "images": [T % "api"]}
        self.assertTrue(any("not a mapping" in p for p in check(doc)))

    def test_built_but_unlisted_and_listed_but_unbuilt(self):
        doc = {"steps": [step("news")], "images": [T % "ui"]}
        problems = check(doc)
        self.assertTrue(any("quantcore-news" in p and "missing" in p for p in problems))
        self.assertTrue(any("quantcore-ui" in p and "no step" in p for p in problems))

    def test_non_string_args_are_caught(self):
        doc = {"steps": [{"id": "x", "args": [{"a": 1}]}], "images": []}
        self.assertTrue(any("list of strings" in p for p in check(doc)))

    def test_cache_wiring_is_required_on_every_build_step(self):
        # #278: each of these still builds a correct image, just a cold one.
        no_buildkit = step("api"); no_buildkit["env"] = []
        no_inline = step("api"); no_inline["args"].remove("BUILDKIT_INLINE_CACHE=1")
        wrong_source = step("api"); wrong_source["args"][2] = C % "mcp"
        for doc_step, needle in ((no_buildkit, "DOCKER_BUILDKIT"),
                                 (no_inline, "BUILDKIT_INLINE_CACHE"),
                                 (wrong_source, "--cache-from quantcore-api")):
            problems = check({"steps": [doc_step], "images": [T % "api"]})
            self.assertTrue(any(needle in p for p in problems), problems)

    def test_non_build_steps_need_no_cache(self):
        doc = {"steps": [step("api"), {"id": "x", "args": ["push", T % "api"]}],
               "images": [T % "api"]}
        self.assertEqual(check(doc), [])


if __name__ == "__main__":
    unittest.main()
