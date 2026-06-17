"""CI wrapper smoke — boot each MCP gateway server in-memory and assert its tool
surface is intact (Phase 3 Step 10).

This is a *static* check: it imports each wrapper module's ``mcp`` object and lists
its tools over fastmcp's in-memory transport, asserting every tool exposes a name and
a well-formed JSON input schema. It does **not** call any tool, so it needs no REST
tier, no DB, and no network — it guards against a wrapper failing to import or a tool
losing its schema (e.g. a botched signature/docstring edit) before we ever deploy.

Run: ``PYTHONPATH=. python scripts/ci_wrapper_smoke.py``
Exit 0 = all wrappers booted with valid tool schemas; non-zero = failure (CI red).
"""

from __future__ import annotations

import asyncio
import importlib
import sys

from fastmcp import Client

# (import path, friendly name). Minimum expected tool count is a floor, not an exact
# match — adding tools must not turn CI red, but a wrapper dropping to zero (failed
# import / lost registration) must.
WRAPPERS = [
    ("fastMCPTest.stock_price_server", "stock-price", 1),
    ("fastMCPTest.options_analysis", "options-analysis", 1),
    ("fastMCPTest.company_fundamentals_server", "company-fundamentals", 1),
    ("fastMCPTest.news_sentiment_server", "news-sentiment", 1),
    ("fastMCPTest.market_analysis_server", "market-analysis", 1),
]


async def smoke_one(module_path: str, name: str, floor: int) -> tuple[str, int, list[str]]:
    module = importlib.import_module(module_path)
    mcp = getattr(module, "mcp")
    problems: list[str] = []
    async with Client(mcp) as client:
        tools = await client.list_tools()
        if len(tools) < floor:
            problems.append(f"only {len(tools)} tool(s), expected >= {floor}")
        for t in tools:
            if not t.name:
                problems.append("a tool has an empty name")
            schema = t.inputSchema
            if not isinstance(schema, dict) or schema.get("type") != "object":
                problems.append(f"{t.name}: input schema is not a JSON object")
    return name, len(tools), problems


async def main() -> int:
    failures = 0
    for module_path, name, floor in WRAPPERS:
        try:
            n, problems = (await smoke_one(module_path, name, floor))[1:]
        except Exception as e:  # import or boot failure
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failures += 1
            continue
        if problems:
            print(f"[FAIL] {name}: listTools={n}; " + "; ".join(problems))
            failures += 1
        else:
            print(f"[ok]   {name}: listTools={n}, all schemas valid")
    print(f"\n{len(WRAPPERS) - failures}/{len(WRAPPERS)} wrappers healthy")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
