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

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root and fastMCPTest are importable (mirrors api/app.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FAST_MCP_DIR = PROJECT_ROOT / "fastMCPTest"
if str(FAST_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(FAST_MCP_DIR))

from .auth import require_principal  # noqa: E402
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
        chat,
        dashboard,
        fundamentals,
        keyproxy,
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

    # /api/health stays open (unauthenticated) for Cloud Run / compose liveness probes.
    app.include_router(system.router)

    # Every business route is gated by the JWT dependency. Locally and in the
    # docker-compose stack AUTH_DISABLED=1 makes the dependency a no-op (it injects a
    # local principal), so this is inert until JWT is turned on for Cloud Run.
    protected = Depends(require_principal)
    for module in (
        plans,
        rungs,
        symbols,
        dashboard,
        portfolio,
        prices,
        options,
        fundamentals,
        sentiment,
        microstructure,
        recommendations,
        chat,
        keyproxy,
    ):
        app.include_router(module.router, dependencies=[protected])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=5001)
