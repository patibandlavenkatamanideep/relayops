"""Approved-offers catalog — offers/prices are DATA, never model-invented.

The guardrail treats this module as ground truth. If a candidate reply states a
price, discount, or paid offer that is NOT represented here, it is a
hallucination and gets blocked (then escalated to a human, who is the one allowed
to discuss billing). This is what makes "the agent never invents money" an
enforceable property instead of a hope.
"""

from __future__ import annotations

# The only monetary amounts the agent may state autonomously, as exact,
# normalised strings. Self-service actions (reset, links) are free; anything
# that costs money is a human/billing conversation, which we escalate.
APPROVED_AMOUNTS: frozenset[str] = frozenset({"free", "$0", "$0.00"})

# Approved offers the agent may name, mapped to their approved amount string.
APPROVED_OFFERS: dict[str, str] = {
    "device_reset": "free",
    "reset_guide": "free",
    "app_download": "free",
}
