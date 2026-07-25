"""Packet 1a tests: envelope crypto v1 (keyproxy/crypto.py).

Covers the shared vector file (tests/vectors/keyproxy_envelope_v1.json) that
packet 1b's TypeScript implementation must match byte-exactly, the tamper
cases from the plan's Verify contract (flipped AAD byte, wrong kid, stale
iat, altered scope vs scope_hash), and the never-log policy: every rejection
carries the same generic message with no envelope or key material in it.
"""

import copy
import json
import os
import subprocess
import sys
import unittest

from keyproxy import crypto

VECTORS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vectors", "keyproxy_envelope_v1.json",
)

with open(VECTORS_PATH, encoding="utf-8") as _fh:
    VECTORS = json.load(_fh)

RECIPIENT_KEY = crypto.private_key_from_jwk(VECTORS["recipient"]["private_jwk"])
KID = VECTORS["recipient"]["kid"]
PRIVATE_KEYS = {KID: RECIPIENT_KEY}


_VECTOR_ENVELOPE = object()  # sentinel: "use the case's own envelope"


def decrypt_vector(env_case, envelope=_VECTOR_ENVELOPE, **overrides):
    kwargs = dict(
        expected_sub=env_case["expected_sub"],
        expected_provider=env_case["expected_provider"],
        scope=env_case["scope"],
        now=env_case["aad"]["iat"],
    )
    kwargs.update(overrides)
    if envelope is _VECTOR_ENVELOPE:
        envelope = env_case["envelope"]
    return crypto.decrypt_envelope(envelope, PRIVATE_KEYS, **kwargs)


class TestCanonicalJson(unittest.TestCase):
    def test_vector_canonical_forms(self):
        for case in VECTORS["canonicalization"]:
            with self.subTest(case["name"]):
                self.assertEqual(
                    crypto.canonical_json(case["input"]).decode("utf-8"),
                    case["canonical"],
                )
                self.assertEqual(
                    crypto.compute_scope_hash(case["input"]), case["scope_hash"]
                )

    def test_key_order_is_irrelevant(self):
        a = {"b": 1, "a": {"y": 2, "x": 3}}
        b = {"a": {"x": 3, "y": 2}, "b": 1}
        self.assertEqual(crypto.canonical_json(a), crypto.canonical_json(b))

    def test_rejects_floats(self):
        with self.assertRaises(crypto.EnvelopeError):
            crypto.canonical_json({"ttl": 300.0})

    def test_rejects_non_ascii_keys(self):
        # JS sorts keys by UTF-16 code unit, Python by code point; they
        # diverge above the BMP, so the contract bans non-ASCII keys.
        with self.assertRaises(crypto.EnvelopeError):
            crypto.canonical_json({"clé": 1})

    def test_rejects_non_json_types(self):
        with self.assertRaises(crypto.EnvelopeError):
            crypto.canonical_json({"raw": b"bytes"})

    def test_non_ascii_values_stay_literal_utf8(self):
        canon = crypto.canonical_json({"k": "ünïcödé 💹"})
        self.assertEqual(canon, '{"k":"ünïcödé 💹"}'.encode("utf-8"))


class TestB64Url(unittest.TestCase):
    def test_round_trip(self):
        blob = os.urandom(37)
        self.assertEqual(crypto.b64url_decode(crypto.b64url_encode(blob)), blob)

    def test_no_padding_emitted(self):
        self.assertNotIn("=", crypto.b64url_encode(b"\x00"))

    def test_rejects_padded_or_standard_alphabet(self):
        for bad in ("AA==", "a+b", "a/b"):
            with self.subTest(bad):
                with self.assertRaises(crypto.EnvelopeError):
                    crypto.b64url_decode(bad)


class TestVectorEnvelopes(unittest.TestCase):
    def test_reencryption_reproduces_pinned_ciphertext(self):
        # The determinism proof: pinned ephemeral key + iv + aad must yield
        # the exact epk/ct in the file — the same bar packet 1b must clear.
        for case in VECTORS["envelopes"]:
            with self.subTest(case["name"]):
                rebuilt = crypto.encrypt_envelope(
                    case["api_key"],
                    RECIPIENT_KEY.public_key(),
                    kid=KID,
                    aad=case["aad"],
                    ephemeral_private_key=crypto.private_key_from_jwk(
                        case["ephemeral_private_jwk"]
                    ),
                    iv=crypto.b64url_decode(case["iv"]),
                )
                self.assertEqual(rebuilt, case["envelope"])

    def test_decrypt_recovers_api_key(self):
        for case in VECTORS["envelopes"]:
            with self.subTest(case["name"]):
                self.assertEqual(decrypt_vector(case), case["api_key"])

    def test_fresh_random_round_trip(self):
        scope = VECTORS["envelopes"][0]["scope"]
        aad = {
            "sub": "alice", "provider": "anthropic", "iat": 1752570000,
            "jti": "11111111-2222-4333-8444-555555555555",
            "scope_hash": crypto.compute_scope_hash(scope),
        }
        envelope = crypto.encrypt_envelope(
            "sk-ant-round-trip", RECIPIENT_KEY.public_key(), kid=KID, aad=aad
        )
        got = crypto.decrypt_envelope(
            envelope, PRIVATE_KEYS, expected_sub="alice",
            expected_provider="anthropic", scope=scope, now=aad["iat"],
        )
        self.assertEqual(got, "sk-ant-round-trip")

    def test_iat_boundary_accepted(self):
        case = VECTORS["envelopes"][0]
        for skew in (-60, 60):
            with self.subTest(skew=skew):
                self.assertEqual(
                    decrypt_vector(case, now=case["aad"]["iat"] + skew),
                    case["api_key"],
                )


class TestTamperFailsClosed(unittest.TestCase):
    """Every case must raise EnvelopeError — and nothing else."""

    def setUp(self):
        self.case = VECTORS["envelopes"][0]

    def assert_rejected(self, envelope=_VECTOR_ENVELOPE, **overrides):
        with self.assertRaises(crypto.EnvelopeError) as ctx:
            decrypt_vector(self.case, envelope=envelope, **overrides)
        return ctx.exception

    def tampered(self, **changes):
        envelope = copy.deepcopy(self.case["envelope"])
        for key, value in changes.items():
            envelope[key] = value
        return envelope

    def test_flipped_aad_byte(self):
        envelope = copy.deepcopy(self.case["envelope"])
        envelope["aad"]["sub"] = "johm"  # one byte off
        # AAD no longer matches expected_sub *and* breaks the GCM tag;
        # also verify pure AAD/GCM failure with expected_sub matching the lie.
        self.assert_rejected(envelope=envelope)
        self.assert_rejected(envelope=envelope, expected_sub="johm")

    def test_flipped_ciphertext_byte(self):
        raw = bytearray(crypto.b64url_decode(self.case["envelope"]["ct"]))
        raw[0] ^= 0x01
        self.assert_rejected(envelope=self.tampered(ct=crypto.b64url_encode(bytes(raw))))

    def test_truncated_tag(self):
        raw = crypto.b64url_decode(self.case["envelope"]["ct"])[:-1]
        self.assert_rejected(envelope=self.tampered(ct=crypto.b64url_encode(raw)))

    def test_wrong_kid_unknown(self):
        self.assert_rejected(envelope=self.tampered(kid="kp-9999-01-nope"))

    def test_wrong_kid_known_but_different_key(self):
        # A registered second key must still fail: kid feeds the HKDF info
        # string and selects the wrong private scalar.
        other = crypto.generate_private_key()
        envelope = self.tampered(kid="kp-other")
        with self.assertRaises(crypto.EnvelopeError):
            crypto.decrypt_envelope(
                envelope, {KID: RECIPIENT_KEY, "kp-other": other},
                expected_sub=self.case["expected_sub"],
                expected_provider=self.case["expected_provider"],
                scope=self.case["scope"], now=self.case["aad"]["iat"],
            )

    def test_stale_iat(self):
        for skew in (61, -61, 3600):
            with self.subTest(skew=skew):
                self.assert_rejected(now=self.case["aad"]["iat"] + skew)

    def test_altered_scope_vs_scope_hash(self):
        scope = copy.deepcopy(self.case["scope"])
        scope["budget"]["max_tokens"] = 10**9  # privilege escalation attempt
        self.assert_rejected(scope=scope)

    def test_sub_mismatch(self):
        self.assert_rejected(expected_sub="mallory")

    def test_provider_mismatch(self):
        self.assert_rejected(expected_provider="openai")

    def test_wrong_version_or_alg(self):
        self.assert_rejected(envelope=self.tampered(v=2))
        self.assert_rejected(envelope=self.tampered(alg="ECDH-ES-X25519"))

    def test_structure_violations(self):
        extra = self.tampered()
        extra["extra"] = "field"
        self.assert_rejected(envelope=extra)
        missing = self.tampered()
        del missing["iv"]
        self.assert_rejected(envelope=missing)
        self.assert_rejected(envelope="not-a-dict")
        self.assert_rejected(envelope=None)

    def test_aad_violations(self):
        for aad_change in (
            {"iat": "1752570000"},   # string, not int
            {"iat": True},           # bool masquerading as int
            {"sub": 42},
            {"scope_hash": None},
        ):
            aad = dict(self.case["aad"], **aad_change)
            with self.subTest(aad_change=aad_change):
                self.assert_rejected(envelope=self.tampered(aad=aad))
        no_jti = dict(self.case["aad"])
        del no_jti["jti"]
        self.assert_rejected(envelope=self.tampered(aad=no_jti))
        extra_key = dict(self.case["aad"], hacker="yes")
        self.assert_rejected(envelope=self.tampered(aad=extra_key))

    def test_bad_epk(self):
        self.assert_rejected(envelope=self.tampered(epk=crypto.b64url_encode(b"\x04" + b"\x00" * 64)))
        self.assert_rejected(envelope=self.tampered(epk=crypto.b64url_encode(b"\x02" + os.urandom(64))))
        self.assert_rejected(envelope=self.tampered(epk=crypto.b64url_encode(os.urandom(32))))
        self.assert_rejected(envelope=self.tampered(epk="not base64url!!"))

    def test_bad_iv_length(self):
        self.assert_rejected(envelope=self.tampered(iv=crypto.b64url_encode(os.urandom(16))))


class TestNeverLogPolicy(unittest.TestCase):
    """Rejections must be generic: no key material, no envelope contents,
    no hint of which check failed, no chained library exception."""

    def collect_failures(self):
        case = VECTORS["envelopes"][0]
        failures = []
        tampered = copy.deepcopy(case["envelope"])
        tampered["aad"]["sub"] = "evil"
        for kwargs in (
            dict(envelope=tampered),
            dict(now=case["aad"]["iat"] + 999),
            dict(expected_provider="openai"),
            dict(scope={"v": 1, "altered": True}),
        ):
            try:
                decrypt_vector(case, **kwargs)
                self.fail("expected rejection")
            except crypto.EnvelopeError as exc:
                failures.append(exc)
        return case, failures

    def test_all_rejections_are_generic_and_unchained(self):
        case, failures = self.collect_failures()
        for exc in failures:
            self.assertEqual(str(exc), "invalid envelope")
            self.assertIsNone(exc.__cause__)
            text = repr(exc) + str(exc.args)
            self.assertNotIn(case["api_key"], text)
            self.assertNotIn(case["envelope"]["ct"], text)
            self.assertNotIn(case["envelope"]["epk"], text)
            self.assertNotIn("sk-ant", text)


class TestKeypairScript(unittest.TestCase):
    def test_prints_pair_and_writes_nothing(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        before = set(os.listdir(repo_root))
        proc = subprocess.run(
            [sys.executable, os.path.join("scripts", "generate_keyproxy_keypair.py"),
             "--kid", "kp-test-unit"],
            cwd=repo_root, capture_output=True, text=True, check=True,
        )
        self.assertEqual(set(os.listdir(repo_root)), before)
        out = proc.stdout
        self.assertIn("kid: kp-test-unit", out)
        self.assertIn("spki_fingerprint", out)
        self.assertIn("BEGIN PUBLIC KEY", out)
        self.assertIn("BEGIN PRIVATE KEY", out)

    def test_render_generates_fresh_keys_each_call(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        try:
            import generate_keyproxy_keypair as script
        finally:
            sys.path.pop(0)
        first, second = script.render("kp-a"), script.render("kp-a")
        self.assertNotEqual(first, second)
        self.assertTrue(script.default_kid().startswith("kp-"))


if __name__ == "__main__":
    unittest.main()
