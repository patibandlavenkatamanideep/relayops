"""Voice channel adapter (v3.2) — synthetic transcript → RelayOps request.

``adapt_call`` takes a synthetic ``VoiceCallInput`` and normalizes it into a
``VoiceTurn`` — the same (text + optional token + optional device) request shape
the existing pipeline consumes — carrying the call's channel metadata for audit.

Hard boundaries (enforced by what this module does *not* import or call):

  * It connects to **no** voice provider — no Twilio, phone number, real audio,
    speech-to-text, or text-to-speech. It only reshapes text that is already a
    transcript.
  * It **executes nothing** — no tool, no action, no pipeline run. It returns a
    normalized request; the caller decides whether to run it through the pipeline.
  * It **decides nothing** — no risk classification, no approval, no scope grant.
    Downstream the access gate, broker, envelope, tool boundary, and approval
    queue behave exactly as they do for typed chat text.
  * It **mints no authority** — ``customer_id``/``caller_id`` are advisory; only a
    passed-through demo token (if the synthetic call carries one) authenticates,
    via the unchanged access gate.

So voice is a channel, not a new authority path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Union

from .models import (
    CHANNEL_VOICE,
    REQUIRED_FIELDS,
    VoiceAdapterResult,
    VoiceCallInput,
    VoiceChannelMetadata,
    VoiceTurn,
)

# Stable error codes (for tests/dashboards).
EMPTY_TRANSCRIPT = "empty_transcript"
MISSING_FIELD = "missing_field"  # suffixed with the field name, e.g. missing_field:call_id


def normalize_transcript(text: str) -> str:
    """Collapse a raw transcript into a single clean request line: strip, and
    fold internal whitespace/newlines to single spaces. Deterministic; content is
    never rewritten or 'understood', only tidied."""
    return " ".join(str(text).split())


def _suggested_turn_id(call_id: str) -> str:
    """A turn id derived from the call id so the audit record ties back to the
    originating voice call (evidence preservation, not a new identity)."""
    return f"voice_{call_id}"


def adapt_call(call: VoiceCallInput) -> VoiceAdapterResult:
    """Normalize one synthetic voice call into a RelayOps request.

    Returns ``ok=False`` with a stable error code for a missing required field or
    an empty transcript; never raises for bad input, never executes anything.
    """
    # Required-field validation, in declared order so the first gap is reported.
    for name in REQUIRED_FIELDS:
        if name == "transcript":
            continue
        if not str(getattr(call, name, "") or "").strip():
            return VoiceAdapterResult(ok=False, error=f"{MISSING_FIELD}:{name}")

    if not normalize_transcript(call.transcript):
        return VoiceAdapterResult(ok=False, error=EMPTY_TRANSCRIPT)

    metadata = VoiceChannelMetadata(
        channel=call.channel or CHANNEL_VOICE,
        call_id=call.call_id,
        caller_id=call.caller_id,
        customer_id=call.customer_id,
        language=call.language,
        started_at=call.started_at,
        confidence=call.confidence,
        redaction_notes=call.redaction_notes,
    )
    warnings: list[str] = []
    if call.auth_token is None:
        # Not an error: an unauthenticated call is allowed — the access gate will
        # simply refuse scoped actions downstream. Surface it as advisory evidence.
        warnings.append("no_auth_token: caller is unauthenticated; scoped actions will be refused")

    turn = VoiceTurn(
        message=normalize_transcript(call.transcript),
        auth_token=call.auth_token,
        device_id=call.device_id,
        metadata=metadata,
        suggested_turn_id=_suggested_turn_id(call.call_id),
    )
    return VoiceAdapterResult(ok=True, turn=turn, warnings=warnings)


def adapt_dict(data: dict[str, Any]) -> VoiceAdapterResult:
    """Normalize a raw synthetic-call dict (reads only known fields)."""
    return adapt_call(VoiceCallInput.from_dict(data))


def load_call(path: Union[str, Path]) -> VoiceCallInput:
    """Load a synthetic voice-call JSON file into a ``VoiceCallInput``.

    Reads a local JSON file only; it opens no audio and contacts no network.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("voice call file must contain a JSON object")
    return VoiceCallInput.from_dict(raw)
