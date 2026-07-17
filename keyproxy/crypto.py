"""Envelope crypto (v1) for the BYOK Key Proxy.

Implements the load-bearing contract in docs/proposals/byok-key-proxy-plan.md
("Envelope spec (v1)"): ephemeral-static ECDH on P-256 -> HKDF-SHA256 ->
AES-256-GCM, with the envelope's ``aad`` object bound into the GCM tag as
canonical JSON.

The browser (``frontend/src/vault/envelope.ts``, packet 1b) is the production
encryptor; the encrypt path in this module exists to generate and verify the
shared test vectors (``tests/vectors/keyproxy_envelope_v1.json``) that pin the
two implementations to byte-identical output.

Cross-runtime canonicalization contract (pinned by the vectors):
  * canonical JSON = keys sorted, separators ``,``/``:`` with no whitespace,
    minimal escaping (``ensure_ascii=False`` / plain ``JSON.stringify``),
    encoded as UTF-8
  * object keys must be ASCII — JS sorts keys by UTF-16 code unit and Python
    by code point, which diverge above the BMP, so non-ASCII keys are rejected
  * numbers must be integers — float formatting diverges between the runtimes
  * HKDF salt is empty, which per RFC 5869 both ``cryptography`` (salt=None)
    and WebCrypto (empty ArrayBuffer) expand to HashLen zero bytes

Logging policy (see the plan's "Logging policy" section): nothing in this
module logs, and every rejection raises :class:`EnvelopeError` with the same
constant, generic message — which check failed, AAD values, envelope contents,
and plaintext keys never appear in exception text.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from typing import Mapping, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENVELOPE_VERSION = 1
ENVELOPE_ALG = "ECDH-ES-P256+HKDF-SHA256+A256GCM"
HKDF_INFO_PREFIX = "quantcore-keyproxy-v1|"
IV_LENGTH = 12
GCM_TAG_LENGTH = 16
UNCOMPRESSED_POINT_LENGTH = 65  # 0x04 || X (32 bytes) || Y (32 bytes)
DEFAULT_MAX_IAT_SKEW_SECONDS = 60

_ENVELOPE_KEYS = frozenset({"v", "alg", "kid", "epk", "iv", "ct", "aad"})
_AAD_KEYS = frozenset({"sub", "provider", "iat", "jti", "scope_hash"})

# Single generic rejection message: a failed decrypt must not reveal which
# check failed (the plan mandates a generic 400 with no body logging).
_REJECT = "invalid envelope"


class EnvelopeError(ValueError):
    """Any envelope rejection. The message is always the same generic text."""


# --- b64url (unpadded, strict) ----------------------------------------------

def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or "=" in value
        or "+" in value
        or "/" in value
    ):
        raise EnvelopeError(_REJECT)
    padding = -len(value) % 4
    try:
        return base64.urlsafe_b64decode(value + "=" * padding)
    except (ValueError, binascii.Error):
        raise EnvelopeError(_REJECT) from None


# --- canonical JSON + scope hashing ------------------------------------------

def canonical_json(obj: object) -> bytes:
    """Canonical JSON bytes, byte-identical to the TS canonicalizer.

    Rejects (fail closed) anything the cross-runtime contract cannot carry:
    non-ASCII object keys, floats/NaN/Infinity, and non-JSON types.
    """
    _check_canonical_value(obj)
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _check_canonical_value(value: object) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise EnvelopeError(_REJECT)
            _check_canonical_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _check_canonical_value(item)
        return
    raise EnvelopeError(_REJECT)  # floats and any non-JSON type


def compute_scope_hash(scope: dict) -> str:
    """b64url SHA-256 of the scope's canonical JSON (the ``aad.scope_hash``)."""
    return b64url_encode(hashlib.sha256(canonical_json(scope)).digest())


# --- P-256 key material -------------------------------------------------------

def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(private_key: ec.EllipticCurvePrivateKey) -> str:
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def load_private_key_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
        key.curve, ec.SECP256R1
    ):
        raise EnvelopeError(_REJECT)
    return key


def public_key_to_pem(public_key: ec.EllipticCurvePublicKey) -> str:
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def public_key_spki_der(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def spki_fingerprint(public_key: ec.EllipticCurvePublicKey) -> str:
    """b64url SHA-256 of the SPKI DER — the ``VITE_KEYPROXY_SPKI_PINS`` format."""
    return b64url_encode(hashlib.sha256(public_key_spki_der(public_key)).digest())


def public_key_to_uncompressed_point(public_key: ec.EllipticCurvePublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )


# JWK helpers: WebCrypto's native key format, used by the shared vectors so
# packet 1b can import the exact same pinned keys with crypto.subtle.

def private_key_to_jwk(private_key: ec.EllipticCurvePrivateKey) -> dict:
    numbers = private_key.private_numbers()
    public = numbers.public_numbers
    return {
        "kty": "EC",
        "crv": "P-256",
        "d": b64url_encode(numbers.private_value.to_bytes(32, "big")),
        "x": b64url_encode(public.x.to_bytes(32, "big")),
        "y": b64url_encode(public.y.to_bytes(32, "big")),
    }


def private_key_from_jwk(jwk: dict) -> ec.EllipticCurvePrivateKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise EnvelopeError(_REJECT)
    secret = int.from_bytes(b64url_decode(jwk["d"]), "big")
    return ec.derive_private_key(secret, ec.SECP256R1())


def public_key_from_jwk(jwk: dict) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise EnvelopeError(_REJECT)
    x = int.from_bytes(b64url_decode(jwk["x"]), "big")
    y = int.from_bytes(b64url_decode(jwk["y"]), "big")
    return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()


# --- envelope encrypt / decrypt ----------------------------------------------

def _derive_key(shared_secret: bytes, kid: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,  # RFC 5869 empty salt == HashLen zeros; matches WebCrypto
        info=(HKDF_INFO_PREFIX + kid).encode("utf-8"),
    ).derive(shared_secret)


def _validate_aad(aad: object) -> dict:
    if not isinstance(aad, dict) or set(aad.keys()) != _AAD_KEYS:
        raise EnvelopeError(_REJECT)
    if not all(
        isinstance(aad[field], str)
        for field in ("sub", "provider", "jti", "scope_hash")
    ):
        raise EnvelopeError(_REJECT)
    if not isinstance(aad["iat"], int) or isinstance(aad["iat"], bool):
        raise EnvelopeError(_REJECT)
    return aad


def encrypt_envelope(
    api_key: str,
    recipient_public_key: ec.EllipticCurvePublicKey,
    *,
    kid: str,
    aad: dict,
    ephemeral_private_key: Optional[ec.EllipticCurvePrivateKey] = None,
    iv: Optional[bytes] = None,
) -> dict:
    """Build a v1 envelope.

    Production encryption happens in the browser; this path exists for the
    shared test vectors and round-trip tests. ``ephemeral_private_key`` and
    ``iv`` are injectable ONLY so vectors are deterministic — production
    callers must let both default to fresh random values.
    """
    _validate_aad(aad)
    ephemeral = ephemeral_private_key or generate_private_key()
    if iv is None:
        iv = os.urandom(IV_LENGTH)
    if len(iv) != IV_LENGTH:
        raise EnvelopeError(_REJECT)
    shared_secret = ephemeral.exchange(ec.ECDH(), recipient_public_key)
    key = _derive_key(shared_secret, kid)
    ciphertext = AESGCM(key).encrypt(
        iv, api_key.encode("utf-8"), canonical_json(aad)
    )
    return {
        "v": ENVELOPE_VERSION,
        "alg": ENVELOPE_ALG,
        "kid": kid,
        "epk": b64url_encode(public_key_to_uncompressed_point(ephemeral.public_key())),
        "iv": b64url_encode(iv),
        "ct": b64url_encode(ciphertext),
        "aad": dict(aad),
    }


def _timing_safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def decrypt_envelope(
    envelope: object,
    private_keys: Mapping[str, ec.EllipticCurvePrivateKey],
    *,
    expected_sub: str,
    expected_provider: str,
    scope: dict,
    now: Optional[int] = None,
    max_iat_skew: int = DEFAULT_MAX_IAT_SKEW_SECONDS,
) -> str:
    """Verify and decrypt a v1 envelope; returns the plaintext API key.

    Every failure — malformed structure, unknown ``kid``, stale ``iat``,
    ``sub``/``provider`` mismatch, ``scope_hash`` not matching the
    accompanying ``scope``, invalid point, GCM authentication failure —
    raises :class:`EnvelopeError` with the same generic message.

    ``jti`` single-use burning is session-layer state (packet 2a): the caller
    must burn ``envelope["aad"]["jti"]`` only after this returns successfully.
    """
    try:
        if not isinstance(envelope, dict) or set(envelope.keys()) != _ENVELOPE_KEYS:
            raise EnvelopeError(_REJECT)
        if envelope["v"] != ENVELOPE_VERSION or envelope["alg"] != ENVELOPE_ALG:
            raise EnvelopeError(_REJECT)
        kid = envelope["kid"]
        if not isinstance(kid, str):
            raise EnvelopeError(_REJECT)
        private_key = private_keys.get(kid)
        if private_key is None:
            raise EnvelopeError(_REJECT)

        aad = _validate_aad(envelope["aad"])
        current = int(time.time()) if now is None else now
        if abs(current - aad["iat"]) > max_iat_skew:
            raise EnvelopeError(_REJECT)
        if not _timing_safe_equal(aad["sub"], expected_sub):
            raise EnvelopeError(_REJECT)
        if not _timing_safe_equal(aad["provider"], expected_provider):
            raise EnvelopeError(_REJECT)
        if not _timing_safe_equal(aad["scope_hash"], compute_scope_hash(scope)):
            raise EnvelopeError(_REJECT)

        epk = b64url_decode(envelope["epk"])
        if len(epk) != UNCOMPRESSED_POINT_LENGTH or epk[0] != 0x04:
            raise EnvelopeError(_REJECT)
        iv = b64url_decode(envelope["iv"])
        if len(iv) != IV_LENGTH:
            raise EnvelopeError(_REJECT)
        ciphertext = b64url_decode(envelope["ct"])
        if len(ciphertext) <= GCM_TAG_LENGTH:
            raise EnvelopeError(_REJECT)

        peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk)
        shared_secret = private_key.exchange(ec.ECDH(), peer)
        key = _derive_key(shared_secret, kid)
        plaintext = AESGCM(key).decrypt(iv, ciphertext, canonical_json(aad))
        return plaintext.decode("utf-8")
    except EnvelopeError:
        raise
    except Exception:
        # Fail closed on anything unexpected (bad point encoding, InvalidTag,
        # type errors from hostile input). `from None` drops the original
        # exception so no library detail about the envelope leaks upward.
        raise EnvelopeError(_REJECT) from None
