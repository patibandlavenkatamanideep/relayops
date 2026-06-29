"""RelayOps policy registry (v1.9).

A policy *handle* is the stable identifier the broker stamps onto every decision
(``BrokerDecisionPacket.policy_handle``). This package turns those handles from
bare strings scattered through the broker into a single, enforced catalog.
"""

from __future__ import annotations

from .registry import (
    POLICY_VERSION,
    REGISTRY,
    PolicyHandle,
    all_handles,
    exists,
    get,
    registry_as_list,
    rules_for,
    validate,
)

__all__ = [
    "POLICY_VERSION",
    "REGISTRY",
    "PolicyHandle",
    "all_handles",
    "exists",
    "get",
    "registry_as_list",
    "rules_for",
    "validate",
]
