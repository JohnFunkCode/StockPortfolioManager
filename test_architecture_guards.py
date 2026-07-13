"""Architecture fence (architectural-standard-v2, issues #74–#77).

Turns the July 2026 audit findings into permanent CI guarantees:

  * yfinance may be imported ONLY by the provider gateway package, the legacy
    ``portfolio/`` domain layer (main.py report path carve-out), and the
    standalone ``experiments/`` monitors.
  * ``yf.download`` — the thread-unsafe call that caused the OHLCV
    cross-ticker corruption — may appear only inside quantcore/gateways/,
    where every call is serialized on _YF_DOWNLOAD_LOCK.

If one of these tests fails, a provider import leaked outside the gateway
seam — move the call behind YFinanceGateway instead of editing the allowlist.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent

# Directories whose *.py files are subject to the fence.
SCANNED_DIRS = ["quantcore", "api", "fastMCPTest", "scripts", "mcp_gateway"]
# Root-level modules subject to the fence.
SCANNED_ROOT_FILES = ["main.py", "notifier.py"]

# Sanctioned yfinance importers (standard §5.3 + documented carve-outs).
YFINANCE_ALLOWED_PREFIXES = (
    "quantcore/gateways/",
    "portfolio/",
    "experiments/",
)

# Actual import statements only — docstring prose like "from yfinance and
# caches the result" must not trip the fence.
_IMPORT_RE = re.compile(
    r"^\s*(import yfinance\b|from yfinance(\.\w+)* import\b)", re.MULTILINE
)
_DOWNLOAD_RE = re.compile(r"\byf\.download\s*\(")
# Real call sites in the gateway are multiline calls: "yf.download(\n".
_DOWNLOAD_CALL_RE = re.compile(r"yf\.download\(\s*\n")


def _scanned_files():
    for d in SCANNED_DIRS:
        root = REPO / d
        if root.exists():
            yield from root.rglob("*.py")
    for name in SCANNED_ROOT_FILES:
        f = REPO / name
        if f.exists():
            yield f


class TestYfinanceFence(unittest.TestCase):
    def test_yfinance_imports_only_in_sanctioned_locations(self):
        violations = []
        for f in _scanned_files():
            rel = f.relative_to(REPO).as_posix()
            if rel.startswith(YFINANCE_ALLOWED_PREFIXES):
                continue
            if _IMPORT_RE.search(f.read_text(errors="replace")):
                violations.append(rel)
        self.assertEqual(
            violations, [],
            "yfinance imported outside the gateway/carve-outs — route these "
            f"through YFinanceGateway: {violations}",
        )

    def test_yf_download_only_in_gateways(self):
        violations = []
        for f in _scanned_files():
            rel = f.relative_to(REPO).as_posix()
            if rel.startswith("quantcore/gateways/"):
                continue
            src = f.read_text(errors="replace")
            if _IMPORT_RE.search(src) and _DOWNLOAD_RE.search(src):
                violations.append(rel)
        self.assertEqual(violations, [], f"yf.download outside gateways: {violations}")

    def test_gateway_download_sites_hold_the_lock(self):
        """Every yf.download call in the gateway must sit inside a
        `with _YF_DOWNLOAD_LOCK:` block (crude but effective source check)."""
        src = (REPO / "quantcore/gateways/yfinance_gateway.py").read_text()
        calls = len(_DOWNLOAD_CALL_RE.findall(src))
        self.assertGreater(calls, 0, "expected at least one yf.download call site")
        locked_blocks = src.count("with _YF_DOWNLOAD_LOCK:")
        self.assertGreaterEqual(locked_blocks, calls,
                                "a yf.download call site is missing the lock")


if __name__ == "__main__":
    unittest.main()
