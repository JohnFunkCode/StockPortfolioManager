"""scripts/check_cloudbuild.py — the PR-time guard on cloudbuild.yaml."""
import unittest
from pathlib import Path

import yaml

from scripts.check_cloudbuild import PATH, check

R = "${_REGION}-docker.pkg.dev/${_PROJECT}/${_REPO}"
T = R + "/quantcore-%s:${_TAG}"
CACHE = "type=registry,ref=" + R + "/quantcore-%s:buildcache-${_CACHE_TAG}"


def step(img):
    return {
        "id": f"build-{img}",
        "waitFor": ["builder"],
        "args": ["buildx", "build", "--builder", "quantcore",
                 "--cache-from", CACHE % img,
                 "--cache-to", CACHE % img + ",mode=max,image-manifest=true",
                 "--provenance=false", "-t", T % img, "--push", "."],
    }


def tag_latest(*imgs, wait=None):
    script = "\n".join(f"gcloud artifacts docker tags add {T % i} "
                       f"{R}/quantcore-{i}:${{_LATEST_TAG}}" for i in imgs)
    return {"id": "tag-latest",
            "waitFor": wait if wait is not None else [f"build-{i}" for i in imgs],
            "entrypoint": "bash", "args": ["-ceu", script]}


def doc(*imgs, steps=None, tag=None):
    return {"steps": (steps or [step(i) for i in imgs]) + [tag or tag_latest(*imgs)]}


class CheckCloudbuildTest(unittest.TestCase):
    def test_committed_file_is_valid(self):
        self.assertEqual(check(yaml.safe_load(Path(PATH).read_text())), [])

    def test_consistent_config_passes(self):
        self.assertEqual(check(doc("api", "ui")), [])

    def test_image_line_inside_a_step_is_caught(self):
        # The original defect: a stray list item broke the step's args.
        d = doc("api"); d["steps"].insert(1, "oops")
        self.assertTrue(any("not a mapping" in p for p in check(d)))

    def test_built_but_untagged_and_tagged_but_unbuilt(self):
        problems = check(doc(steps=[step("news")], tag=tag_latest("ui", wait=["build-news"])))
        self.assertTrue(any("quantcore-news" in p and "never tags" in p for p in problems))
        self.assertTrue(any("quantcore-ui" in p and "no step" in p for p in problems))

    def test_rolling_tag_waits_for_every_build(self):
        problems = check(doc("api", "mcp", tag=tag_latest("api", "mcp", wait=["build-api"])))
        self.assertTrue(any("waitFor build-mcp" in p for p in problems), problems)

    def test_missing_tag_step_and_images_block_are_caught(self):
        no_tag = {"steps": [step("api")]}
        self.assertTrue(any("tag-latest" in p for p in check(no_tag)))
        with_images = doc("api"); with_images["images"] = [T % "api"]
        self.assertTrue(any("`images:`" in p for p in check(with_images)))

    def test_non_string_args_are_caught(self):
        d = doc("api"); d["steps"].insert(0, {"id": "x", "args": [{"a": 1}]})
        self.assertTrue(any("list of strings" in p for p in check(d)))

    def test_cache_wiring_is_required_on_every_build_step(self):
        # Each of these still builds a correct image, just a cold (or wrapped) one.
        def without(flag):
            s = step("api"); i = s["args"].index(flag)
            del s["args"][i:i + (1 if flag.startswith("--p") else 2)]
            return s
        min_mode = step("api")
        min_mode["args"][7] = CACHE % "api" + ",mode=min"
        wrong_source = step("api"); wrong_source["args"][5] = CACHE % "mcp"
        for s, needle in ((without("--builder"), "--builder quantcore"),
                          (without("--cache-from"), "--cache-from"),
                          (without("--cache-to"), "--cache-to"),
                          (min_mode, "mode=max"),
                          (wrong_source, "--cache-from"),
                          (without("--provenance=false"), "--provenance=false"),
                          (without("--push"), "--push")):
            problems = check(doc("api", steps=[s]))
            self.assertTrue(any(needle in p for p in problems), (needle, problems))

    def test_plain_docker_build_is_refused(self):
        # Inline cache on the daemon's BuildKit alternated warm/cold on main.
        legacy = {"id": "build-api", "args": ["build", "-t", T % "api", "."]}
        problems = check(doc("api", steps=[step("api"), legacy]))
        self.assertTrue(any("buildx build" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
