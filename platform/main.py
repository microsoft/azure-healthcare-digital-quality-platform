"""Platform stack -- Phase 0 minimal FastAPI skeleton.

Exposes a /health endpoint so the stack can be smoke-tested locally before
Phase 4 lifts the real platform routes in.
"""
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="dq-platform", version="0.0.0-phase0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "stack": "platform",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
def root() -> dict:
    return {"stack": "platform", "phase": 0, "message": "placeholder"}
