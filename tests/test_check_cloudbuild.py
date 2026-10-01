"""scripts/check_cloudbuild.py — the PR-time guard on cloudbuild.yaml."""
import unittest
from pathlib import Path

import yaml

from scripts.check_cloudbuild import PATH, check

T = "r-docker.pkg.dev/p/q/quantcore-%s:${_TAG}"


def step(img):
    return {"id": f"build-{img}", "args": ["build", "-t", T % img, "."]}


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


if __name__ == "__main__":
    unittest.main()
