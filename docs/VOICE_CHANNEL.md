# Voice Channel Adapter — RelayOps v3.2

Voice is a **channel adapter only**. It converts a *synthetic* voice-call
transcript into the exact same RelayOps request the chat and API paths already
produce, and carries the call's metadata for audit. It is **not** a new autonomous
phone agent and it adds **no new authority**.

> **What it does NOT do:** it connects to no Twilio, no phone number, no real
> audio, no speech-to-text vendor, no text-to-speech vendor, and no outbound call.
> It stores no audio and uses no real customer voice data. It executes nothing,
> decides nothing, and mints no token — the broker, policy, scope, action
> envelope, approval queue, audit, replay, and Hermes are unchanged and
> un-bypassed.

---

## 1. The flow

Voice slots in as an inbound channel; everything after normalization is the
existing control plane:

```
synthetic voice transcript
  → voice adapter (normalize to a RelayOps request + carry channel metadata)
  → deterministic access gate (scope from token — NOT from voice metadata)
  → policy broker (allow / block / escalate)
  → action envelope
  → scoped tool boundary
  → approval queue (if high-risk)
  → audit ledger  → replay verification
  → Hermes / operator review
  → safe response text
  → (optional) voice response text — the same reply text, spoken by a provider
                                     that is NOT part of this repo
```

The adapter's job is only the first arrow. It never runs the rest — a caller (a
test, a scenario, a future service) decides whether to run the normalized request
through `handle_turn`.

## 2. Input: a synthetic call object

```json
{
  "channel": "voice",
  "call_id": "synthetic_call_001",
  "caller_id": "caller_alice",
  "customer_id": "cust_alice",
  "transcript": "My router is not working. Can you reset it?",
  "auth_token": "tok_alice"
}
```

| Field | Required | Meaning |
|---|---|---|
| `call_id` | ✅ | synthetic call identifier |
| `caller_id` | ✅ | who called (advisory metadata) |
| `customer_id` | ✅ | claimed customer (advisory — never grants access) |
| `transcript` | ✅ | the spoken text, already transcribed |
| `language`, `started_at`, `channel`, `confidence`, `redaction_notes` | optional | call metadata carried for audit |
| `auth_token`, `device_id` | optional | advisory demo passthrough (below) |

Only these known fields are read; any other key on the object (an audio URL, a
stray secret) is **dropped** and never reaches a RelayOps request. Synthetic
examples live in [`examples/voice/`](../examples/voice).

### Authority (important)

`customer_id` / `caller_id` are **advisory metadata** — exactly like the API's
advisory `customer_id`, they do not grant access. If a synthetic call carries an
`auth_token`, the adapter passes it through so the normalized request can be
authenticated by the **unchanged access gate**; the adapter never creates a token
or widens scope. An unauthenticated call adapts fine (with a warning) and simply
gets scoped actions refused downstream.

## 3. Output: a normalized request

`adapt_call` (or `adapt_dict`) returns a `VoiceAdapterResult`:

- `ok=True` with a `VoiceTurn` carrying:
  - `message` — the cleaned transcript (whitespace normalized; content unchanged);
  - `auth_token`, `device_id` — passthrough for the pipeline;
  - `metadata` — `VoiceChannelMetadata` (channel, call_id, caller_id, customer_id,
    language, started_at, confidence, redaction_notes);
  - `suggested_turn_id` — `voice_<call_id>`, so the audit record ties back to the
    call.
- `ok=False` with a stable `error` for a missing required field
  (`missing_field:<name>`) or an empty transcript (`empty_transcript`). The adapter
  never raises on bad input.

`VoiceTurn.to_request()` yields `{message, token, customer_id, device_id}` —
the same fields as the API `TurnRequest`.

## 4. Usage

```python
from src.channels.voice import adapt_dict, load_call, adapt_call
from src.graph.pipeline import handle_turn
from src.observability.audit_ledger import AuditLedger

result = adapt_call(load_call("examples/voice/synthetic_call_refund_request.json"))
assert result.ok
turn = result.turn

# Run the normalized request through the UNCHANGED pipeline. Voice does not bypass
# anything: this refund still escalates and the approval queue would hold it.
ledger = AuditLedger()
response = handle_turn(
    turn.message,
    auth_token=turn.auth_token,
    device_id=turn.device_id,
    audit=ledger,
    turn_id=turn.suggested_turn_id,
)
```

## 5. Safety properties (tested)

[`tests/test_voice_channel_adapter.py`](../tests/test_voice_channel_adapter.py)
asserts:

- a valid transcript normalizes into the internal request shape;
- empty transcript and each missing required field are rejected;
- channel metadata is preserved (and survives serialization for audit evidence);
- the adapter mutates neither the input dict nor the `VoiceCallInput`;
- the adapter executes no tool, approves/rejects nothing, and records no audit;
- the adapter imports no external voice SDK;
- **downstream invariants**: a refund voice call still takes the high-risk /
  escalation path; a cross-customer voice call (Bob targeting Alice's device) is
  still refused at the scoped tool boundary; the audit record's `turn_id` ties
  back to the originating call.

## 6. What a real voice deployment would add (deferred)

A production voice path would put a real telephony/STT provider *in front of* this
adapter and a real TTS provider *after* the reply — behind the same access gate,
broker, approval, and audit. That integration is intentionally **not** in this
repo; it is planned as a v4.0 `VOICE_PROVIDER_PLAN.md` deliverable in the
production pilot blueprint. The control-plane guarantees do not change: voice
remains a channel, and the human/operator remains accountable for every escalated
or held case.
