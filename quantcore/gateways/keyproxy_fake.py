"""FakeKeyProxyGateway — canned in-process keyproxy for route tests (3c).

KEYPROXY_FAKE=1 swaps this in for the real gateway module so /api/keyproxy
routes can be exercised with zero network: it generates a REAL P-256 keypair
and runs the REAL envelope decrypt (keyproxy/crypto.py), so tests mint
genuine envelopes against ``.public_key`` and the full seal→unseal path is
covered. What it fakes is only the transport and the provider probe.

Never-log policy applies here exactly as in the keyproxy: no key material,
envelope contents, or tokens are logged or embedded in exceptions —
rejections raise KeyProxyError with the gateway's canned user-safe copy.
"""
from __future__ import annotations

import jwt

from keyproxy import crypto

from .keyproxy_gateway import KeyProxyError, RESEND_MESSAGE

_FAKE_KID = "kp-fake-1"


class FakeKeyProxyGateway:
    """Drop-in for the keyproxy gateway module: same three functions."""

    def __init__(self):
        self._private_key = crypto.generate_private_key()
        self.public_key = self._private_key.public_key()

    def is_configured(self) -> bool:
        return True

    def get_public_keys(self) -> list[dict]:
        return [
            {
                "kid": _FAKE_KID,
                "alg": crypto.ENVELOPE_ALG,
                "spki": crypto.b64url_encode(
                    crypto.public_key_spki_der(self.public_key)
                ),
            }
        ]

    def validate_key(self, *, envelope: dict, scope: dict, auth_token: str) -> dict:
        expected_sub = None
        if auth_token:
            try:
                claims = jwt.decode(auth_token, options={"verify_signature": False})
                expected_sub = claims.get("sub")
            except Exception:
                expected_sub = None
        if expected_sub is None:
            # No token (auth-off local mode): trust the envelope's own sub,
            # exactly what a keyproxy without upstream auth cannot do — this
            # shortcut is why the fake is test-only.
            try:
                expected_sub = envelope["aad"]["sub"]
            except Exception:
                raise KeyProxyError(RESEND_MESSAGE) from None
        try:
            api_key = crypto.decrypt_envelope(
                envelope,
                {_FAKE_KID: self._private_key},
                expected_sub=expected_sub,
                expected_provider=scope.get("provider"),
                scope=scope,
                max_iat_skew=crypto.DEFAULT_MAX_IAT_SKEW_SECONDS,
            )
        except crypto.EnvelopeError:
            raise KeyProxyError(RESEND_MESSAGE) from None
        return {
            "valid": True,
            "provider": scope.get("provider"),
            "key_hint": "…" + api_key[-4:],
        }
