"""Voice channel data types (v3.2).

Voice is an **input/output channel**, not a new autonomous phone agent. These
records model a *synthetic* voice call and its normalization into the existing
RelayOps request shape:

  * ``VoiceCallInput``      — a synthetic inbound call (transcript + call metadata).
  * ``VoiceChannelMetadata``— the channel evidence carried through for audit.
  * ``VoiceTurn``           — the normalized RelayOps request derived from a call.
  * ``VoiceAdapterResult``  — the adapter's deterministic output (ok / error).

Plain dataclasses, deterministic and local. Nothing here connects to Twilio, a
phone number, real audio, a speech-to-text/text-to-speech vendor, or any outbound
call, and nothing here executes an action, decides risk, or mints authority — the
transcript is treated exactly like typed text once normalized.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# The canonical channel tag stored on every voice-derived request's evidence.
CHANNEL_VOICE = "voice"

# The fields the adapter reads off a synthetic call object. Anything else on the
# object is ignored (a stray field never leaks into a RelayOps request).
REQUIRED_FIELDS = ("call_id", "caller_id", "customer_id", "transcript")
OPTIONAL_FIELDS = (
    "language",
    "started_at",
    "channel",
    "confidence",
    "redaction_notes",
    # Advisory demo passthrough: a synthetic call may already carry the caller's
    # demo bearer token / target device so the normalized request can run through
    # the real pipeline. The adapter passes these through verbatim; it never mints
    # a token or widens scope (authority still comes only from the access gate).
    "auth_token",
    "device_id",
)
KNOWN_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS


@dataclass
class VoiceChannelMetadata:
    """Channel evidence for a voice call — travels with the normalized request so
    the audit trail can show a turn arrived by voice and from which call."""

    channel: str
    call_id: str
    caller_id: str
    customer_id: str
    language: str = ""
    started_at: str = ""
    confidence: Optional[float] = None
    redaction_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceCallInput:
    """A synthetic inbound voice call. ``transcript`` is treated as customer text;
    ``customer_id``/``caller_id`` are advisory metadata (like the API's advisory
    ``customer_id``) — they never grant access."""

    call_id: str
    caller_id: str
    customer_id: str
    transcript: str
    language: str = ""
    started_at: str = ""
    channel: str = CHANNEL_VOICE
    confidence: Optional[float] = None
    redaction_notes: str = ""
    auth_token: Optional[str] = None
    device_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceCallInput":
        """Build from a raw call object, reading ONLY known fields (a stray field
        such as an audio URL or a secret is dropped, never carried forward).
        Missing fields default to empty so the adapter can report *which* required
        field is absent rather than raising an opaque KeyError."""
        picked = {name: data.get(name) for name in KNOWN_FIELDS if name in data}
        return cls(
            call_id=str(picked.get("call_id") or ""),
            caller_id=str(picked.get("caller_id") or ""),
            customer_id=str(picked.get("customer_id") or ""),
            transcript=str(picked.get("transcript") or ""),
            language=str(picked.get("language") or ""),
            started_at=str(picked.get("started_at") or ""),
            channel=str(picked.get("channel") or CHANNEL_VOICE),
            confidence=picked.get("confidence"),
            redaction_notes=str(picked.get("redaction_notes") or ""),
            auth_token=picked.get("auth_token"),
            device_id=picked.get("device_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VoiceTurn:
    """The normalized RelayOps request derived from a voice call: the same inputs
    the pipeline consumes (``message`` text + optional auth token + optional target
    device), plus the channel metadata and a suggested turn id that ties the
    audit record back to the originating call."""

    message: str
    auth_token: Optional[str]
    device_id: Optional[str]
    metadata: VoiceChannelMetadata
    suggested_turn_id: str = ""

    def to_request(self) -> dict[str, Any]:
        """The request payload, mirroring the API ``TurnRequest`` fields. Note
        ``customer_id`` is advisory only — the access gate resolves scope from the
        token, never from this field."""
        return {
            "message": self.message,
            "token": self.auth_token,
            "customer_id": self.metadata.customer_id,
            "device_id": self.device_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "auth_token": self.auth_token,
            "device_id": self.device_id,
            "suggested_turn_id": self.suggested_turn_id,
            "metadata": self.metadata.to_dict(),
        }


@dataclass
class VoiceAdapterResult:
    """The adapter's deterministic result: a normalized ``VoiceTurn`` on success,
    or ``ok=False`` with a stable error code (the adapter never raises for bad
    input, and never executes anything)."""

    ok: bool
    turn: Optional[VoiceTurn] = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "warnings": list(self.warnings),
            "turn": self.turn.to_dict() if self.turn else None,
        }
