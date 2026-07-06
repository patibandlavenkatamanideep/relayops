"""Input/output channels for the RelayOps control plane (v3.2).

A *channel* is a way a customer request arrives (and a reply leaves) — chat, the
HTTP API, a support-ticket batch, and now a synthetic **voice** transcript. A
channel is deliberately thin: it *normalizes* an inbound request into the same
internal shape the pipeline already consumes and carries channel metadata for
audit. It adds **no authority** and makes **no decision** — the access gate,
policy broker, action envelope, scoped tool boundary, approval queue, audit
ledger, replay, and Hermes are unchanged and un-bypassed regardless of channel.
"""
