"""Minimal HS256 bearer-token auth for the RelayOps API (v2.1).

Stdlib only (``hmac`` + ``hashlib`` + ``base64`` + ``json``) so the service gains
signed bearer tokens without a new runtime dependency to pin for the deploy. This
is not a general JWT library — just the HS256 issue/verify RelayOps needs.

The token is a signed *envelope* around the opaque access token, carrying the
customer it resolved to. The deterministic access gate stays the single
authority for scope: the API verifies the signature and expiry, then hands the
enveloped opaque token to the gate exactly as before. A forged or expired token
is rejected before the pipeline runs; it can never widen scope.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Any

JWT_SECRET = os.environ.get("RELAYOPS_JWT_SECRET", "dev-insecure-secret-change-me")
JWT_TTL = int(os.environ.get("RELAYOPS_JWT_TTL", "3600"))  # seconds
_ALG = "HS256"


class AuthError(Exception):
    """Raised when a bearer token is malformed, mis-signed, or expired."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _sign(signing_input: str, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()


def issue(
    customer_id: str,
    token: str,
    *,
    ttl: int | None = None,
    now: int | None = None,
    secret: str | None = None,
) -> str:
    """Mint a signed token for ``customer_id`` wrapping the opaque ``token``."""
    issued = int(time.time() if now is None else now)
    lifetime = JWT_TTL if ttl is None else ttl
    header = {"alg": _ALG, "typ": "JWT"}
    payload = {"sub": customer_id, "tok": token, "iat": issued, "exp": issued + lifetime}
    signing_input = f"{_b64url(_json(header))}.{_b64url(_json(payload))}"
    signature = _sign(signing_input, secret or JWT_SECRET)
    return f"{signing_input}.{_b64url(signature)}"


def verify(bearer: str, *, now: int | None = None, secret: str | None = None) -> dict[str, Any]:
    """Verify a bearer token; return its claims or raise ``AuthError``."""
    try:
        header_b64, payload_b64, signature_b64 = bearer.split(".")
    except ValueError as exc:
        raise AuthError("malformed token") from exc

    signing_input = f"{header_b64}.{payload_b64}"
    expected = _sign(signing_input, secret or JWT_SECRET)
    try:
        provided = _b64url_decode(signature_b64)
    except (ValueError, binascii.Error) as exc:
        raise AuthError("malformed signature") from exc
    # Constant-time compare; reject before parsing claims.
    if not hmac.compare_digest(expected, provided):
        raise AuthError("bad signature")

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed claims") from exc

    # Pin the algorithm: never honour "none" or an attacker-chosen alg.
    if header.get("alg") != _ALG:
        raise AuthError("unexpected algorithm")
    if int(time.time() if now is None else now) >= int(payload.get("exp", 0)):
        raise AuthError("expired token")
    if not payload.get("tok"):
        raise AuthError("token missing scope claim")
    return payload


def bearer_from_header(authorization: str | None) -> str | None:
    """Extract the credential from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def _json(obj: Any) -> bytes:
    # Compact, stable encoding so the signing input is deterministic.
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
