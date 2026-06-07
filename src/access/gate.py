"""Deterministic access gate — the first stage, and NOT an LLM.

Security policy before mechanism: this runs before any model. It authenticates
the caller and resolves *exactly* what that customer may do. The resulting
``AccessContext`` is the only authority later stages and the MCP server trust.
A prompt-injected model downstream cannot widen it, because nothing downstream
re-derives permissions — they only read this context.
"""

from __future__ import annotations

from ..core import data
from ..core.models import AccessContext, Action

# In v1 every authenticated customer gets the same low-risk, reversible action
# set. (Per-customer / per-plan policy is a deferred extension.)
_DEFAULT_ALLOWED: frozenset[Action] = frozenset(
    {Action.ACCOUNT_LOOKUP, Action.DEVICE_RESET, Action.SEND_LINK}
)


def authenticate(auth_token: str | None) -> AccessContext:
    """Turn an auth token into a scoped permission context."""
    customer_id = data.resolve_token(auth_token)
    if customer_id is None:
        return AccessContext(authenticated=False)
    return AccessContext(
        authenticated=True,
        customer_id=customer_id,
        allowed_actions=_DEFAULT_ALLOWED,
    )
