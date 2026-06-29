"""ASGI entrypoint for the RelayOps API.

Run locally:
    python -m uvicorn src.api.main:app --reload --port 8000

The app only mounts routes; all behaviour lives in the wrapped pipeline. The
public posture is unchanged — no LLM key is required or used by default.
"""

from __future__ import annotations

from fastapi import FastAPI

from .routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="RelayOps API",
        version="2.1",
        description=(
            "Service boundary for RelayOps support turns. Wraps the deterministic "
            "pipeline: scoped access gate, route safety, guardrail, durable audit, "
            "human handoff. Signed bearer-token auth and per-caller rate limiting "
            "guard the API; the LLM composer stays opt-in and disabled by default."
        ),
    )
    app.include_router(router)
    return app


app = create_app()
