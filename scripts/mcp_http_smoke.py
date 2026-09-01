#!/usr/bin/env python3
"""Bounded smoke test for deployed streamable HTTP MCP wrappers.

Each endpoint is initialized, optionally held open, and then asked to execute a
named lightweight tool. The script prints only session/tool metadata, never the
tool result, token, or exception payload. It is intended for post-deploy use;
ordinary CI continues to use ``ci_wrapper_smoke.py`` for in-memory checks.

Example::

    PYTHONPATH=. python scripts/mcp_http_smoke.py \
      --endpoint stock-price=https://host/mcp \
      --tool stock-price=get_stock_price \
      --arguments 'stock-price={"symbol":"AAPL"}' \
      --hold-seconds 301 --auth-token "$QUANTCORE_MCP_TOKEN"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from fastmcp import Client

MAX_HOLD_SECONDS = 840.0


def _pairs(values: list[str], flag: str) -> dict[str, str]:
    result = {}
    for value in values:
        name, separator, item = value.partition("=")
        if not separator or not name or not item:
            raise ValueError(f"{flag} must use NAME=VALUE")
        result[name] = item
    return result


async def _probe(name: str, url: str, tool: str, *, hold_seconds: float,
                 arguments: dict[str, Any], auth_token: str | None,
                 timeout: float) -> None:
    kwargs: dict[str, Any] = {"timeout": timeout}
    if auth_token:
        kwargs["auth"] = auth_token
    async with Client(url, **kwargs) as client:
        tools = await client.list_tools()
        available = {item.name for item in tools}
        if tool not in available:
            raise RuntimeError(f"tool {tool!r} is not advertised by {name}")
        if hold_seconds:
            await asyncio.sleep(hold_seconds)
        await client.call_tool(tool, arguments=arguments or None)
    print(f"[ok] {name}: initialized, held={hold_seconds:.1f}s, tool={tool}")


async def _run(args: argparse.Namespace) -> int:
    endpoints = _pairs(args.endpoint, "--endpoint")
    tools = _pairs(args.tool, "--tool")
    raw_arguments = _pairs(args.arguments, "--arguments")
    missing = sorted(set(endpoints) - set(tools))
    if missing:
        raise ValueError("missing --tool for endpoint(s): " + ", ".join(missing))
    failures = 0
    for name, url in endpoints.items():
        try:
            await _probe(
                name,
                url,
                tools[name],
                hold_seconds=args.hold_seconds,
                arguments=json.loads(raw_arguments.get(name, "{}")),
                auth_token=args.auth_token,
                timeout=args.timeout,
            )
        except Exception as exc:  # noqa: BLE001 — smoke test reports one endpoint failure
            print(f"[FAIL] {name}: {type(exc).__name__}", file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", required=True,
                        help="NAME=streamable HTTP /mcp URL; repeat per wrapper")
    parser.add_argument("--tool", action="append", required=True,
                        help="NAME=tool to call after initialization; repeat per endpoint")
    parser.add_argument("--arguments", action="append", default=[],
                        help="NAME=JSON object passed to the tool; repeat per endpoint")
    parser.add_argument("--hold-seconds", type=float, default=0.0,
                        help=f"hold each session before the tool call (0-{MAX_HOLD_SECONDS:g})")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="per-client operation timeout in seconds")
    parser.add_argument("--auth-token", default=os.environ.get("QUANTCORE_MCP_TOKEN"),
                        help="bearer token; defaults to QUANTCORE_MCP_TOKEN")
    args = parser.parse_args()
    if not 0 <= args.hold_seconds <= MAX_HOLD_SECONDS:
        parser.error(f"--hold-seconds must be between 0 and {MAX_HOLD_SECONDS:g}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        return asyncio.run(_run(args))
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
