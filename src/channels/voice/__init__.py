"""Voice channel (v3.2) — synthetic transcript adapter.

Converts a *synthetic* voice-call transcript into the existing RelayOps request
shape and carries channel metadata for audit. Voice is an input/output channel
only: it adds no authority, makes no decision, executes nothing, and connects to
no real voice provider (no Twilio, phone number, audio, STT/TTS, or outbound
call). The broker, envelope, scope, approval queue, audit, replay, and Hermes are
unchanged and un-bypassed.
"""

from __future__ import annotations

from .adapter import (
    EMPTY_TRANSCRIPT,
    MISSING_FIELD,
    adapt_call,
    adapt_dict,
    load_call,
    normalize_transcript,
)
from .models import (
    CHANNEL_VOICE,
    KNOWN_FIELDS,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    VoiceAdapterResult,
    VoiceCallInput,
    VoiceChannelMetadata,
    VoiceTurn,
)

__all__ = [
    "CHANNEL_VOICE",
    "REQUIRED_FIELDS",
    "OPTIONAL_FIELDS",
    "KNOWN_FIELDS",
    "VoiceCallInput",
    "VoiceChannelMetadata",
    "VoiceTurn",
    "VoiceAdapterResult",
    "adapt_call",
    "adapt_dict",
    "load_call",
    "normalize_transcript",
    "EMPTY_TRANSCRIPT",
    "MISSING_FIELD",
]
