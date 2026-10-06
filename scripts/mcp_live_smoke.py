#!/usr/bin/env python3
"""Opt-in live smoke: call every read-only MCP tool on the deployed TEST wrappers.

Issue #44, part 3. ``tests/test_mcp_tool_contracts.py`` proves offline what each
tool *sends*; this proves the deployed chain answers it -- wrapper, REST tier,
auth, database and the upstream data sources -- for every tool at once.

- **Test only, never prod.** The wrapper URLs are built from the ``test``
  environment in ``deploy/cloudrun-services.toml``. ``--env prod`` is refused,
  and so is any target whose project number is prod's.
- **Read-only tools only.** A tool runs when its first case in
  ``scripts/mcp_tool_cases.py`` is a ``GET``, a wrapper-local health check, or
  a POST listed in ``READ_ONLY_POSTS`` (the two calculators). Everything else
  -- ``add_to_watchlist``, ``collect_news`` -- is listed as skipped, so a new
  write tool is skipped by default rather than run by accident.
- **The token is a TEST JWT from ``QUANTCORE_TEST_MCP_TOKEN``**, deliberately
  not ``QUANTCORE_MCP_TOKEN``, which holds a prod JWT for AI clients. Mint one
  with ``scripts/mint_prod_jwt.py --project quantcore-test-20260606``.
- **Prints metadata only:** tool name, ok/FAIL, the HTTP status of a REST
  error, and timing. Never a result, the token, or an error payload.

Exit 0 when every selected tool answers, 1 on any failure, 2 on bad usage.
It is not part of the unit suite or CI; run it by hand after a test deploy::

    QUANTCORE_TEST_MCP_TOKEN=... PYTHONPATH=. python scripts/mcp_live_smoke.py
    ... --wrapper options-analysis --tool get_gex_profile   # narrow it down
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any, Callable

from fastmcp import Client

from scripts.ci_wrapper_smoke import WRAPPERS
from scripts.mcp_tool_cases import CASES, READ_ONLY_POSTS

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "deploy" / "cloudrun-services.toml"
TOKEN_ENV = "QUANTCORE_TEST_MCP_TOKEN"
_STATUS = re.compile(r"REST tier returned (\d{3})")


class RefusedTarget(ValueError):
    """The requested target is not the test environment."""


def targets(env_name: str, manifest: Path = MANIFEST) -> dict[str, str]:
    """Map each wrapper's short name to its test ``/mcp`` URL; refuse prod."""
    if env_name != "test":
        raise RefusedTarget(f"refusing --env {env_name}: the live smoke runs against test only")
    doc = tomllib.loads(manifest.read_text())
    envs = doc["environments"]
    env = envs["test"]
    prod_number = envs.get("prod", {}).get("project_number")
    if not env.get("project_number") or env["project_number"] == prod_number:
        raise RefusedTarget("the test environment's project number is prod's; refusing")
    by_module = {svc.get("env", {}).get("SERVER_MODULE"): svc["name"]
                 for svc in doc["services"]}
    urls = {}
    for module_path, name, _floor in WRAPPERS:
        service = by_module.get(module_path)
        if service is None:
            raise ValueError(f"no service in {manifest.name} runs {module_path}")
        url = f"https://{service}-{env['project_number']}.{env['region']}.run.app/mcp"
        if prod_number and prod_number in url:
            raise RefusedTarget(f"{name}: target names the prod project; refusing")
        urls[name] = url
    return urls


def is_read_only(module_path: str, tool: str, expected: dict | None) -> bool:
    """A health check, a GET, or a POST listed in ``READ_ONLY_POSTS``."""
    return (expected is None or expected["method"] == "GET"
            or (module_path, tool) in READ_ONLY_POSTS)


def plan() -> tuple[dict[str, list[tuple[str, dict]]], list[tuple[str, str]]]:
    """Return ({wrapper: [(tool, args)]}, [(wrapper, tool) skipped as not read-only])."""
    names = {module_path: name for module_path, name, _floor in WRAPPERS}
    selected: dict[str, list[tuple[str, dict]]] = {name: [] for name in names.values()}
    skipped = []
    for (module_path, tool), variants in CASES.items():
        args, expected = variants[0]
        name = names[module_path]
        if is_read_only(module_path, tool, expected):
            selected[name].append((tool, args))
        else:
            skipped.append((name, tool))
    return selected, sorted(skipped)


def error_status(text: str) -> str:
    """The HTTP status of a REST error, never its payload."""
    match = _STATUS.search(text or "")
    return f"status={match.group(1)}" if match else "tool error"


async def _probe(name: str, target: Any, calls: list[tuple[str, dict]], *,
                 client_factory: Callable[[Any], Client], out) -> int:
    failures = 0
    try:
        async with client_factory(target) as client:
            advertised = {tool.name for tool in await client.list_tools()}
            for tool, args in calls:
                if tool not in advertised:
                    print(f"[FAIL] {name} {tool}: not advertised", file=out)
                    failures += 1
                    continue
                started = time.monotonic()
                try:
                    result = await client.call_tool(tool, args, raise_on_error=False)
                except Exception as exc:  # noqa: BLE001 -- report the type, never the payload
                    outcome = f"FAIL] {name} {tool}: {type(exc).__name__}"
                    failures += 1
                else:
                    if result.is_error:
                        text = " ".join(getattr(c, "text", "") for c in result.content)
                        outcome = f"FAIL] {name} {tool}: {error_status(text)}"
                        failures += 1
                    else:
                        outcome = f"ok] {name} {tool}"
                print(f"[{outcome} ({time.monotonic() - started:.1f}s)", file=out)
    except Exception as exc:  # noqa: BLE001 -- a wrapper that won't connect fails all its calls
        print(f"[FAIL] {name}: session {type(exc).__name__}", file=out)
        failures += max(len(calls), 1)
    return failures


async def run(endpoints: dict[str, Any], selected: dict[str, list[tuple[str, dict]]], *,
              client_factory: Callable[[Any], Client], out=sys.stdout) -> int:
    """Probe every wrapper concurrently; return the number of failed calls."""
    counts = await asyncio.gather(*(
        _probe(name, endpoints[name], calls, client_factory=client_factory, out=out)
        for name, calls in selected.items() if calls))
    return sum(counts)


def _filter(selected, wrappers, tools):
    out = {}
    for name, calls in selected.items():
        if wrappers and name not in wrappers:
            continue
        out[name] = [(t, a) for t, a in calls if not tools or t in tools]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default="test", help="only 'test' is accepted")
    parser.add_argument("--wrapper", action="append", default=[],
                        help="limit to a wrapper (e.g. options-analysis); repeatable")
    parser.add_argument("--tool", action="append", default=[],
                        help="limit to a tool name; repeatable")
    parser.add_argument("--timeout", type=float, default=180.0,
                        help="per-request timeout in seconds")
    args = parser.parse_args(argv)
    try:
        endpoints = targets(args.env)
    except RefusedTarget as exc:
        parser.error(str(exc))
    token = os.environ.get(TOKEN_ENV)
    if not token:
        parser.error(f"{TOKEN_ENV} is not set (a TEST JWT; see the docstring)")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    selected, skipped = plan()
    unknown = set(args.wrapper) - set(selected)
    if unknown:
        parser.error("unknown --wrapper: " + ", ".join(sorted(unknown)))
    selected = _filter(selected, set(args.wrapper), set(args.tool))
    total = sum(len(calls) for calls in selected.values())
    if not total:
        parser.error("the filters select no read-only tool")
    print(f"live smoke: env=test, {total} tool call(s) across "
          f"{sum(1 for c in selected.values() if c)} wrapper(s)")
    for name, tool in skipped:
        print(f"[skip] {name} {tool}: not read-only")

    def factory(url):
        return Client(url, auth=token, timeout=args.timeout)

    failures = asyncio.run(run(endpoints, selected, client_factory=factory))
    print(f"live smoke: {total - failures}/{total} ok" if failures
          else f"live smoke: all {total} ok")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
