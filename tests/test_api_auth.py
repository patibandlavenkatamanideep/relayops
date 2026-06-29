"""API auth + rate-limiting tests (v2.1).

Covers the stdlib HS256 bearer token (issue/verify, tamper/expiry/alg rejection),
the sliding-window rate limiter, and the HTTP surface: /v1/auth/login, bearer
auth on /v1/turn (with body-token backward compat), 401 on bad tokens, and 429
when a caller exceeds the limit. The gate stays the authority for scope — the
bearer token only wraps the opaque access token.
"""

from __future__ import annotations

import os
import tempfile
import unittest

import httpx

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="relayops_authapi_"), "audit.sqlite3")
os.environ["RELAYOPS_AUDIT_DB"] = _TMP_DB

from src.api import auth, routes  # noqa: E402
from src.api.main import app  # noqa: E402
from src.api.ratelimit import RateLimiter  # noqa: E402

routes._store = None


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class JwtTests(unittest.TestCase):
    def test_issue_verify_roundtrip(self):
        tok = auth.issue("cust_alice", "tok_alice", now=1000, ttl=60)
        claims = auth.verify(tok, now=1010)
        self.assertEqual(claims["sub"], "cust_alice")
        self.assertEqual(claims["tok"], "tok_alice")

    def test_expired_rejected(self):
        tok = auth.issue("cust_alice", "tok_alice", now=1000, ttl=60)
        with self.assertRaises(auth.AuthError):
            auth.verify(tok, now=2000)

    def test_tampered_signature_rejected(self):
        tok = auth.issue("cust_alice", "tok_alice", now=1000)
        head, payload, _sig = tok.split(".")
        forged = f"{head}.{payload}.{'A' * 43}"
        with self.assertRaises(auth.AuthError):
            auth.verify(forged, now=1001)

    def test_wrong_secret_rejected(self):
        tok = auth.issue("cust_alice", "tok_alice", now=1000, secret="real")
        with self.assertRaises(auth.AuthError):
            auth.verify(tok, now=1001, secret="attacker")

    def test_alg_none_rejected(self):
        # An attacker-supplied alg (e.g. "none") must never be honoured.
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        payload = (
            base64.urlsafe_b64encode(json.dumps({"tok": "x", "exp": 9999999999}).encode())
            .rstrip(b"=")
            .decode()
        )
        with self.assertRaises(auth.AuthError):
            auth.verify(f"{header}.{payload}.")

    def test_malformed_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.verify("not-a-jwt")

    def test_bearer_header_parsing(self):
        self.assertEqual(auth.bearer_from_header("Bearer abc.def.ghi"), "abc.def.ghi")
        self.assertEqual(auth.bearer_from_header("bearer xyz"), "xyz")
        self.assertIsNone(auth.bearer_from_header(None))
        self.assertIsNone(auth.bearer_from_header("Basic abc"))


class RateLimiterTests(unittest.TestCase):
    def test_allows_under_limit_then_blocks(self):
        rl = RateLimiter(limit=3, window=60)
        self.assertTrue(all(rl.allow("k", now=0) for _ in range(3)))
        self.assertFalse(rl.allow("k", now=0))

    def test_window_slides(self):
        rl = RateLimiter(limit=2, window=10)
        self.assertTrue(rl.allow("k", now=0))
        self.assertTrue(rl.allow("k", now=1))
        self.assertFalse(rl.allow("k", now=2))
        # After the window passes, the early hits expire and capacity returns.
        self.assertTrue(rl.allow("k", now=11))

    def test_keys_are_independent(self):
        rl = RateLimiter(limit=1, window=60)
        self.assertTrue(rl.allow("a", now=0))
        self.assertTrue(rl.allow("b", now=0))
        self.assertFalse(rl.allow("a", now=0))

    def test_retry_after(self):
        rl = RateLimiter(limit=1, window=10)
        rl.allow("k", now=0)
        self.assertFalse(rl.allow("k", now=3))
        self.assertEqual(rl.retry_after("k", now=3), 7)


class AuthEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes._limiter.reset()

    async def test_login_returns_bearer_token(self):
        async with _client() as c:
            r = await c.post("/v1/auth/login", json={"token": "tok_alice"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["token_type"], "bearer")
            self.assertTrue(body["access_token"])
            claims = auth.verify(body["access_token"])
            self.assertEqual(claims["tok"], "tok_alice")

    async def test_login_invalid_token_401(self):
        async with _client() as c:
            r = await c.post("/v1/auth/login", json={"token": "tok_nope"})
            self.assertEqual(r.status_code, 401)

    async def test_turn_with_bearer_token(self):
        async with _client() as c:
            login = await c.post("/v1/auth/login", json={"token": "tok_alice"})
            bearer = login.json()["access_token"]
            r = await c.post(
                "/v1/turn",
                json={"message": "my router isn't working, can you reset it?"},
                headers={"Authorization": f"Bearer {bearer}"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            d = r.json()
            self.assertEqual(d["intent"], "reset_device")
            self.assertFalse(d["escalated"])

    async def test_turn_with_bad_bearer_401(self):
        async with _client() as c:
            r = await c.post(
                "/v1/turn",
                json={"message": "reset my router"},
                headers={"Authorization": "Bearer not.a.valid.token"},
            )
            self.assertEqual(r.status_code, 401)

    async def test_turn_body_token_still_works(self):
        # Backward compat: the pre-v2.1 body token path is unchanged.
        async with _client() as c:
            r = await c.post("/v1/turn", json={"message": "reset my router", "token": "tok_alice"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["intent"], "reset_device")

    async def test_rate_limit_returns_429(self):
        saved = routes._limiter
        routes._limiter = RateLimiter(limit=2, window=60)
        try:
            async with _client() as c:
                payload = {"message": "reset my router", "token": "tok_alice"}
                self.assertEqual((await c.post("/v1/turn", json=payload)).status_code, 200)
                self.assertEqual((await c.post("/v1/turn", json=payload)).status_code, 200)
                blocked = await c.post("/v1/turn", json=payload)
                self.assertEqual(blocked.status_code, 429)
                self.assertIn("Retry-After", blocked.headers)
        finally:
            routes._limiter = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
