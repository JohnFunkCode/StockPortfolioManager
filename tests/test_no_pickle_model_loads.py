"""No code path may deserialize a pickle-format model (issue #381).

Unpickling runs code, so a ``.bin``/``.pt``/``.ckpt`` model file from the Hub is a
program, not data. This test scans every git-tracked ``*.py`` outside ``tests/``
and ``experiments/`` (the standalone monitors, never deployed) and fails on:

- ``torch.load``, ``pickle.load``/``loads``, ``joblib.load`` (and the ``dill`` /
  ``cloudpickle`` equivalents), including ``from pickle import load``;
- ``trust_remote_code=`` set to anything but ``False``;
- a ``*.from_pretrained(...)`` call on anything but a tokenizer that does not pass
  ``use_safetensors=True``.

It is a pure AST scan -- no torch, no transformers -- so it runs in the lean CI
jobs too.
"""
from __future__ import annotations

import ast
import os
import subprocess
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED_DIRS = ("tests/", "experiments/")
PICKLE_MODULES = {"pickle", "dill", "cloudpickle", "joblib", "torch"}
BANNED_LOADERS = {"load", "loads", "Unpickler"}


def _receiver_name(node: ast.expr) -> str:
    """``AutoTokenizer`` for ``AutoTokenizer.from_pretrained``; ``a.b`` → ``b``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_true(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def find_violations(source: str, filename: str = "<string>") -> list:
    """Every banned model-loading construct in ``source``, as ``file:line: why``."""
    found = []
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.ImportFrom) and node.module in PICKLE_MODULES:
            for alias in node.names:
                if alias.name in BANNED_LOADERS:
                    found.append(f"{filename}:{node.lineno}: imports {node.module}.{alias.name}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        for kw in node.keywords:
            if kw.arg == "trust_remote_code" and not (
                    isinstance(kw.value, ast.Constant) and kw.value.value is False):
                found.append(f"{filename}:{node.lineno}: trust_remote_code is not False")
        if not isinstance(func, ast.Attribute):
            continue
        if (func.attr in BANNED_LOADERS and isinstance(func.value, ast.Name)
                and func.value.id in PICKLE_MODULES):
            found.append(f"{filename}:{node.lineno}: calls {func.value.id}.{func.attr}")
        if func.attr == "from_pretrained" and "Tokenizer" not in _receiver_name(func.value):
            if not any(kw.arg == "use_safetensors" and _is_true(kw.value)
                       for kw in node.keywords):
                found.append(f"{filename}:{node.lineno}: from_pretrained without "
                             "use_safetensors=True")
    return found


def _tracked_python_files() -> list:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT, check=True,
                         capture_output=True, text=True).stdout
    return [p for p in out.splitlines() if p and not p.startswith(EXCLUDED_DIRS)]


class NoPickleModelLoadsTest(unittest.TestCase):
    def test_repository_has_no_pickle_model_loads(self):
        files = _tracked_python_files()
        self.assertTrue(any(p == "scripts/bake_finbert.py" for p in files),
                        "the scan found none of the files it exists to check")
        violations = []
        for rel in files:
            path = os.path.join(REPO_ROOT, rel)
            if not os.path.exists(path):   # deleted in the working tree, not yet committed
                continue
            with open(path, encoding="utf-8") as fh:
                violations += find_violations(fh.read(), rel)
        self.assertEqual(violations, [], "pickle-format model loads (#381):\n"
                         + "\n".join(violations))


class ScannerTest(unittest.TestCase):
    """The scanner itself must catch what it claims to, or the guard is a no-op."""

    def assertFlags(self, source, fragment):
        violations = find_violations(source)
        self.assertTrue(any(fragment in v for v in violations), violations)

    def test_flags_torch_load(self):
        self.assertFlags("import torch\ntorch.load('w.pt')\n", "torch.load")

    def test_flags_pickle_loads_and_from_import(self):
        self.assertFlags("import pickle\npickle.loads(b'')\n", "pickle.loads")
        self.assertFlags("from pickle import load\n", "imports pickle.load")

    def test_flags_joblib_load(self):
        self.assertFlags("import joblib\njoblib.load('m.pkl')\n", "joblib.load")

    def test_flags_trust_remote_code(self):
        self.assertFlags("AutoModel.from_pretrained('x', use_safetensors=True, "
                         "trust_remote_code=True)\n", "trust_remote_code")

    def test_flags_model_load_without_safetensors(self):
        self.assertFlags("AutoModel.from_pretrained('x')\n", "use_safetensors")
        self.assertFlags("AutoModel.from_pretrained('x', use_safetensors=False)\n",
                         "use_safetensors")

    def test_allows_the_safe_forms(self):
        self.assertEqual(find_violations(
            "AutoTokenizer.from_pretrained('x')\n"
            "AutoModel.from_pretrained('x', use_safetensors=True)\n"
            "AutoModel.from_pretrained('x', use_safetensors=True, trust_remote_code=False)\n"
            "json.load(fh)\n"), [])


if __name__ == "__main__":
    unittest.main()
