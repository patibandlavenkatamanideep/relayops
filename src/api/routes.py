"""HTTP routes — a thin wrapper over the v1 pipeline.

Endpoints:
    GET  /healthz              liveness
    POST /v1/turn             run one support turn (returns a turn_id)
    GET  /v1/audit/{turn_id}  fetch the durable audit record for a turn
    GET  /v1/handoffs         recent human-escalation handoffs

Each turn is run through ``handle_turn`` (no reimplementation) with a
request-scoped ``turn_id`` so the response, the audit row, and the logs share
one id.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..graph.pipeline import handle_turn
from ..observability.audit_ledger import AuditLedger
from ..observability.audit_store import DEFAULT_DB_PATH, AuditStore
from .schemas import (
    HandoffsResponse,
    HealthResponse,
    ToolResultModel,
    TurnRequest,
    TurnResponse,
)

logger = logging.getLogger("relayops.api")

router = APIRouter()

# One durable audit store per process, created lazily so a test can point
# RELAYOPS_AUDIT_DB at a temp file before the first request.
_store: AuditStore | None = None


def get_store() -> AuditStore:
    global _store
    if _store is None:
        path = os.environ.get("RELAYOPS_AUDIT_DB", str(DEFAULT_DB_PATH))
        _store = AuditStore(path)
    return _store


def _make_turn_id() -> str:
    """Human-scannable, request-scoped id: turn_<UTC date>_<short random>."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"turn_{stamp}_{uuid.uuid4().hex[:8]}"


# NOTE: handlers are ``async def`` so the *synchronous* pipeline (handle_turn,
# including the RAG/embedder step) runs inline on the event-loop thread rather
# than FastAPI's sync-endpoint threadpool. The retriever and its embedder are
# built for single-threaded use, so running them off the main thread can hang;
# inlining keeps the exact thread context the rest of the code is tested in. The
# pipeline is fast and CPU-light, so blocking the loop per turn is fine for this
# prototype; a production build would offload to a process/worker pool instead.


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(ok=True)


@router.post("/v1/turn", response_model=TurnResponse)
async def post_turn(req: TurnRequest) -> TurnResponse:
    turn_id = _make_turn_id()

    # Capture the per-turn decision trail and persist it durably, exactly as the
    # Streamlit UI does — the API just supplies the shared turn_id.
    ledger = AuditLedger()
    response = handle_turn(
        req.message,
        auth_token=req.token,
        device_id=req.device_id,
        audit=ledger,
        turn_id=turn_id,
    )
    record = ledger.records[-1]
    get_store().write(record, response)

    logger.info(
        "turn=%s intent=%s tier=%s disposition=%s escalated=%s guardrail=%s",
        turn_id,
        response.intent.value,
        response.tier.value,
        response.disposition.value,
        response.escalated,
        response.guardrail_action,
    )

    return TurnResponse(
        turn_id=turn_id,
        reply=response.text,
        intent=response.intent.value,
        tier=response.tier.value,
        disposition=response.disposition.value,
        escalated=response.escalated,
        pre_action_intent_packet=record.pre_action_intent_packet,
        broker_decision_packet=record.broker_decision_packet,
        guardrail_result=record.guardrail,
        audit_record=record.to_dict(),
        trace=record.to_dict(),
        tool_results=[
            ToolResultModel(ok=r.ok, data=r.data, error=r.error)
            for r in response.tool_results
        ],
        handoff=response.handoff_context,
    )


@router.get("/v1/audit/{turn_id}")
async def get_audit(turn_id: str) -> JSONResponse:
    row = get_store().get(turn_id)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(status_code=200, content=row)


@router.get("/v1/handoffs", response_model=HandoffsResponse)
async def get_handoffs(limit: int = 20) -> HandoffsResponse:
    return HandoffsResponse(handoffs=get_store().handoffs(limit=limit))


@router.get("/v1/operator/review")
async def operator_review(limit: int = 100) -> JSONResponse:
    """Read-only Hermes operator review over recent audit traces.

    Operator-side, not customer-facing: it returns advisory findings and drafts
    (failure summary, suggested tests, policy gaps, issues, release notes). It
    never executes anything — see ``src/hermes``.
    """
    from ..hermes import build_report

    report = build_report(get_store().list(limit=limit))
    return JSONResponse(status_code=200, content=report.to_dict())
