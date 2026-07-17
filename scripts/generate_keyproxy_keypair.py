#!/usr/bin/env python3
"""Generate a Key Proxy P-256 keypair and print it — this script NEVER writes.

The private key must exist in exactly two places: this terminal (briefly) and
Secret Manager. Pipe it straight in, per the runbook in
docs/proposals/byok-key-proxy-plan.md (packet 8b):

    python scripts/generate_keyproxy_keypair.py            # random kid
    python scripts/generate_keyproxy_keypair.py --kid kp-2026-07-a

The output contains:
  * the private key PEM (PKCS8)   -> `gcloud secrets versions add keyproxy-private-key`
  * the public key PEM            -> served by GET /v1/publickey
  * the kid                       -> names the key in envelopes' `kid` field
  * the SPKI fingerprint          -> add to the frontend pin list
                                     (VITE_KEYPROXY_SPKI_PINS, packet 1b+)

Rotation is dual-pin: ship a frontend release carrying old+new fingerprints,
add the new PEM to the secret bundle, drop the old pin in the next release.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from keyproxy.crypto import (  # noqa: E402
    generate_private_key,
    private_key_to_pem,
    public_key_to_pem,
    spki_fingerprint,
)


def default_kid() -> str:
    stamp = datetime.date.today().strftime("%Y-%m")
    return f"kp-{stamp}-{os.urandom(2).hex()}"


def render(kid: str | None = None) -> str:
    """Build the full output text. Pure — generates a keypair, writes nothing."""
    kid = kid or default_kid()
    private_key = generate_private_key()
    public_key = private_key.public_key()
    return "\n".join(
        [
            f"kid: {kid}",
            f"spki_fingerprint (VITE_KEYPROXY_SPKI_PINS entry): {spki_fingerprint(public_key)}",
            "",
            "public key (served by GET /v1/publickey):",
            public_key_to_pem(public_key).rstrip(),
            "",
            "PRIVATE key — pipe into Secret Manager, do not save to disk:",
            "  (e.g. rerun as: python scripts/generate_keyproxy_keypair.py \\",
            "        | gcloud secrets versions add keyproxy-private-key --data-file=-)",
            private_key_to_pem(private_key).rstrip(),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--kid",
        default=None,
        help="key id to print alongside the pair (default: kp-YYYY-MM-<4 hex>)",
    )
    args = parser.parse_args(argv)
    print(render(args.kid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
