"""FastAPI REST tier for the Harvester Plan Store and Securities Dashboard.

Phase 2 of architectural-standard-v2: rebuilds the Flask tier (api/app.py) on
FastAPI + Pydantic while preserving every route path and JSON shape so the
React front end runs unmodified.

Run with:  uvicorn api.main:app --host 127.0.0.1 --port 5001
       or:  python -m api.main
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root and fastMCPTest are importable (mirrors api/app.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAST_MCP_DIR = PROJECT_ROOT / "fastMCPTest"
if str(FAST_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_MCP_DIR))

from .errors import register_exception_handlers  # noqa: E402
from .json_response import QuantCoreJSONResponse  # noqa: E402


def create_app() -> FastAPI:
    """Application factory mirroring the Flask ``create_app`` contract."""
    from quantcore.db import init_schema

    init_schema()

    app = FastAPI(
        title="QuantCore REST API",
        version="2.0.0",
        default_response_class=QuantCoreJSONResponse,
    )

    # CORS — allow React dev servers (parity with the Flask CORS config).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # Route groups are registered incrementally across Phase 2 steps.
    from .routers import (
        dashboard,
        fundamentals,
        microstructure,
        options,
        plans,
        portfolio,
        prices,
        recommendations,
        rungs,
        sentiment,
        symbols,
        system,
    )

    app.include_router(system.router)
    app.include_router(plans.router)
    app.include_router(rungs.router)
    app.include_router(symbols.router)
    app.include_router(dashboard.router)
    app.include_router(portfolio.router)
    app.include_router(prices.router)
    app.include_router(options.router)
    app.include_router(fundamentals.router)
    app.include_router(sentiment.router)
    app.include_router(microstructure.router)
    app.include_router(recommendations.router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5001)
