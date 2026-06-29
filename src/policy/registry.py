"""RelayOps policy registry — the catalog of policy handles (v1.9).

Every broker decision carries a ``policy_handle``: the stable identifier of the
policy that governed the turn. Before v1.9 those handles were bare string
literals sprinkled through ``router/policy_broker.py``; nothing said what a
handle *meant*, who owned it, or whether a handle the broker emitted actually
existed.

This module makes the handle a first-class object. One ``PolicyHandle`` entry
per handle records its human title, rationale, owning team, headline
disposition, blast radius, and the ``matched_rule`` strings that may legitimately
resolve to it. The broker imports the handle ids from here, so the strings live
in exactly one place.

The catalog is the single source of truth and is *enforced*: tests drive the
broker over representative turns and assert every emitted handle/rule is
registered here (see ``tests/test_policy_registry.py``). A new decision path that
invents an undocumented handle fails the suite rather than shipping silently.

Read-only and deterministic — policy is data the broker reads, not behaviour
buried in code. Nothing here decides or executes anything.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# Bumped when the policy semantics change in a way operators must notice. The
# broker stamps this onto every ``BrokerDecisionPacket.policy_version``.
POLICY_VERSION = "relayops_policy_v1"

# Headline dispositions a handle can represent (the broker may still escalate an
# "allow" handle on a missing-evidence path; this is the policy's intent).
DISPOSITIONS = ("allow", "block", "escalate")

# --- handle ids (import these from the broker; never retype the strings) -------

DEVICE_RESET = "device.reset.allowed_if_scoped"
FAQ_ANSWER = "faq.answer.requires_grounding"
ACCOUNT_STATUS = "account.status.requires_authenticated_scope"
BILLING_REFUND = "billing.refund.requires_human"
BILLING_PLAN_CHANGE = "billing.plan_change.requires_human"
ACCOUNT_CHANGE = "account.change.requires_verification"
SUPPORT_UNKNOWN = "support.unknown.requires_human"
GREETING = "conversation.greeting.respond_allowed"
AUTH_SCOPE = "auth.customer_scope.required"
GUARDRAIL = "response.guardrail.offer_pii_tone"
FAIL_CLOSED = "safety.fail_closed"


@dataclass(frozen=True)
class PolicyHandle:
    """One catalog entry: what a policy handle means and who owns it.

    ``disposition`` is the policy's headline intent, not a guarantee for every
    turn — an ``allow`` handle still escalates when required evidence is missing.
    ``rules`` lists the ``matched_rule`` strings the broker may attach to this
    handle across its decision paths; the registry test enforces this is a
    superset of what the broker actually emits.
    """

    handle: str
    title: str
    description: str
    owner: str
    disposition: str
    blast_radius: str
    rules: tuple[str, ...]
    version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ENTRIES: tuple[PolicyHandle, ...] = (
    PolicyHandle(
        handle=DEVICE_RESET,
        title="Device reset allowed if scoped",
        description=(
            "A device reset is low-blast and reversible, so it may auto-run once "
            "the scoped tool confirms the authenticated customer owns the device."
        ),
        owner="device_support",
        disposition="allow",
        blast_radius="low",
        rules=(
            "device_reset_allowed_after_auth_and_scope",
            "tool_requires_authenticated_scope_permission",
            "scoped_tool_broker_refused_or_failed",
        ),
    ),
    PolicyHandle(
        handle=FAQ_ANSWER,
        title="FAQ answer requires grounding",
        description=(
            "Informational answers are allowed only when retrieval returns a "
            "grounded citation; ungrounded requests escalate rather than guess."
        ),
        owner="device_support",
        disposition="allow",
        blast_radius="low",
        rules=("faq_answer_requires_grounded_citation",),
    ),
    PolicyHandle(
        handle=ACCOUNT_STATUS,
        title="Account status requires authenticated scope",
        description=(
            "Read-only account/device status is shared only within the "
            "authenticated customer's scope, enforced server-side by the tool."
        ),
        owner="device_support",
        disposition="allow",
        blast_radius="low",
        rules=(
            "account_read_requires_authenticated_scope",
            "tool_requires_authenticated_scope_permission",
            "scoped_tool_broker_refused_or_failed",
        ),
    ),
    PolicyHandle(
        handle=BILLING_REFUND,
        title="Refunds require human review",
        description=(
            "Refunds, credits, and discounts are money-touching and only "
            "partially reversible, so they always escalate to billing support."
        ),
        owner="billing_support",
        disposition="escalate",
        blast_radius="high",
        rules=("discounts_refunds_require_human_review",),
    ),
    PolicyHandle(
        handle=BILLING_PLAN_CHANGE,
        title="Plan changes require human review",
        description=(
            "Plan changes alter contract terms and pricing, so they escalate to "
            "billing support rather than auto-applying."
        ),
        owner="billing_support",
        disposition="escalate",
        blast_radius="high",
        rules=("plan_changes_require_human_review",),
    ),
    PolicyHandle(
        handle=ACCOUNT_CHANGE,
        title="Account changes require verification",
        description=(
            "Account-access or identity changes are irreversible and high-blast; "
            "they require identity verification and a security specialist."
        ),
        owner="identity_security",
        disposition="escalate",
        blast_radius="high",
        rules=(
            "account_changes_require_verification",
            "scoped_tool_broker_refused_or_failed",
        ),
    ),
    PolicyHandle(
        handle=SUPPORT_UNKNOWN,
        title="Unsupported or low-confidence requires human",
        description=(
            "Requests the slice does not support, or that classify with too "
            "little confidence to act, escalate to general support."
        ),
        owner="general_support",
        disposition="escalate",
        blast_radius="unknown",
        rules=("unsupported_or_low_confidence_requires_human",),
    ),
    PolicyHandle(
        handle=GREETING,
        title="Greeting response allowed",
        description=(
            "A greeting with no tool or retrieval is safe to answer directly without escalating."
        ),
        owner="general_support",
        disposition="allow",
        blast_radius="low",
        rules=("safe_greeting_response_allowed",),
    ),
    PolicyHandle(
        handle=AUTH_SCOPE,
        title="Authenticated customer scope required",
        description=(
            "No action proceeds without an authenticated customer scope; "
            "unauthenticated turns escalate to identity review before any tool."
        ),
        owner="identity_security",
        disposition="escalate",
        blast_radius="high",
        rules=("authenticated_customer_scope_required_before_action",),
    ),
    PolicyHandle(
        handle=GUARDRAIL,
        title="Final reply guardrail (offer / PII / tone)",
        description=(
            "The final reply is screened for invented offers/prices, PII, and "
            "unsafe tone; a failed candidate is blocked and handed off, never sent."
        ),
        owner="general_support",
        disposition="block",
        blast_radius="high",
        rules=("final_reply_guardrail_failed",),
    ),
    PolicyHandle(
        handle=FAIL_CLOSED,
        title="Safety layer unavailable — fail closed",
        description=(
            "When a safety-critical layer cannot render a verdict, the turn fails "
            "closed to a safe handoff rather than silently allowing the action."
        ),
        owner="general_support",
        disposition="escalate",
        blast_radius="unknown",
        rules=("safety_layer_unavailable_fail_closed",),
    ),
)

# The catalog, keyed by handle id. Insertion order is preserved for stable
# listing in the API/CLI/UI.
REGISTRY: dict[str, PolicyHandle] = {entry.handle: entry for entry in _ENTRIES}


def get(handle: str) -> PolicyHandle:
    """Return the catalog entry for ``handle`` (raises ``KeyError`` if unknown)."""
    return REGISTRY[handle]


def exists(handle: str) -> bool:
    return handle in REGISTRY


def all_handles() -> list[str]:
    return list(REGISTRY)


def rules_for(handle: str) -> tuple[str, ...]:
    """The matched_rule strings that may resolve to ``handle``."""
    return REGISTRY[handle].rules if handle in REGISTRY else ()


def registry_as_list() -> list[dict[str, Any]]:
    """The full catalog as plain dicts, for API/CLI/UI rendering."""
    return [entry.to_dict() for entry in _ENTRIES]


def validate() -> None:
    """Assert the registry's internal integrity.

    Catches duplicate handles, empty required fields, bad dispositions, and
    handles with no matched_rule. Rules are intentionally NOT required to be
    unique across handles — generic scoped-tool rules (e.g. a permission denial)
    legitimately pair with several handles. The broker-coverage check (every
    emitted handle/rule is registered) lives in the test suite, where real broker
    output is on hand.
    """
    seen_handles: set[str] = set()
    for entry in _ENTRIES:
        if entry.handle in seen_handles:
            raise ValueError(f"duplicate policy handle: {entry.handle}")
        seen_handles.add(entry.handle)

        if entry.disposition not in DISPOSITIONS:
            raise ValueError(
                f"{entry.handle}: disposition {entry.disposition!r} not in {DISPOSITIONS}"
            )
        for field_name in ("handle", "title", "description", "owner", "blast_radius", "version"):
            if not str(getattr(entry, field_name)).strip():
                raise ValueError(f"{entry.handle}: empty {field_name}")
        if not entry.rules:
            raise ValueError(f"{entry.handle}: at least one matched_rule required")


# Fail fast at import: a malformed catalog is a programming error, not runtime
# data, so surface it the moment the module loads.
validate()
