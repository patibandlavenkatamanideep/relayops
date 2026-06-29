"""Controlled tool execution boundary for v2.3."""

from __future__ import annotations

from ..actions import PENDING, REPLAYED, IdempotencyLedger, default_ledger
from . import registry
from .schema import ToolAuditRecord, ToolError, ToolRequest, ToolResponse, ToolStatus


class ToolServer:
    """Validate and execute tool requests through a registered scoped handler."""

    def __init__(self, ledger: IdempotencyLedger | None = None) -> None:
        self.ledger = ledger if ledger is not None else default_ledger
        self.audit_events: list[ToolAuditRecord] = []

    def execute(self, req: ToolRequest) -> ToolResponse:
        spec = registry.get(req.tool_name)
        if spec is None:
            response = self._refuse(req, "unknown_tool", "tool is not registered")
            self._audit(req, response)
            return response

        validation_error = self._validate(req, spec)
        if validation_error is not None:
            response = self._refuse(req, validation_error)
            self._audit(req, response)
            return response

        prior = self.ledger.get(req.envelope.idempotency_key)
        if prior is not None:
            req.envelope.status = REPLAYED
            req.envelope.result = dict(prior.result)
            req.envelope.completed_at = req.envelope.created_at
            response = ToolResponse(status=ToolStatus.REPLAYED, data=dict(prior.result))
            self._audit(req, response)
            return response

        response = spec.handler(req)
        if response.ok:
            req.envelope.succeed(response.data)
            self.ledger.remember(req.envelope)
        else:
            code = response.error.code if response.error else "tool_failed"
            req.envelope.fail(code)
        self._audit(req, response)
        return response

    def reset_audit(self) -> None:
        self.audit_events.clear()

    def _validate(self, req: ToolRequest, spec: registry.ToolSpec) -> str | None:
        ctx = req.context
        if not ctx.authenticated or ctx.customer_id is None:
            return "unauthenticated"
        if req.customer_id != ctx.customer_id:
            return "customer_scope_mismatch"
        if req.broker_decision.decision != "allow":
            return "broker_denied"
        if req.broker_decision.turn_id != req.turn_id:
            return "invalid_envelope"
        if spec.required_action is not None and not ctx.may(spec.required_action):
            return "not_authorized"

        env = req.envelope
        if env.status != PENDING:
            return "invalid_envelope"
        if env.turn_id != req.turn_id or env.customer_id != req.customer_id:
            return "invalid_envelope"
        if env.action != spec.envelope_action:
            return "invalid_envelope"
        if env.target_resource != req.target_resource:
            return "invalid_envelope"
        if not env.policy_handle:
            return "invalid_envelope"
        return None

    def _refuse(self, req: ToolRequest, code: str, message: str = "") -> ToolResponse:
        if req.envelope.status == PENDING:
            req.envelope.fail(code)
        return ToolResponse(status=ToolStatus.REFUSED, error=ToolError(code, message))

    def _audit(self, req: ToolRequest, response: ToolResponse) -> None:
        self.audit_events.append(
            ToolAuditRecord(
                turn_id=req.turn_id,
                tool_name=req.tool_name,
                caller=req.caller,
                customer_id=req.customer_id,
                target_resource=req.target_resource,
                envelope_action_id=req.envelope.action_id,
                envelope_status=req.envelope.status,
                broker_decision=req.broker_decision.decision,
                status=response.status.value,
                error_code=response.error.code if response.error else "",
            )
        )


default_tool_server = ToolServer()
