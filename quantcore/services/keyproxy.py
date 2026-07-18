"""KeyProxyService — thin pass-through over the keyproxy gateway (packet 3b).

Per architectural-standard-v2 the REST tier is exactly one service call deep,
so even pure relays to the keyproxy (pubkey discovery for envelope minting,
key validation for the Settings save flow) go through a service. No logic
lives here — the gateway owns transport and error translation, the keyproxy
owns every decision.
"""

from __future__ import annotations


class KeyProxyService:
    """Relays keyproxy calls for the REST tier (router lands in packet 3c)."""

    def __init__(self, gateway=None):
        if gateway is None:
            from quantcore.gateways import keyproxy_gateway as gateway
        self._gateway = gateway

    def is_configured(self) -> bool:
        return self._gateway.is_configured()

    def get_public_keys(self) -> list[dict]:
        return self._gateway.get_public_keys()

    def validate_key(self, *, envelope: dict, scope: dict, auth_token: str) -> dict:
        return self._gateway.validate_key(
            envelope=envelope, scope=scope, auth_token=auth_token
        )
