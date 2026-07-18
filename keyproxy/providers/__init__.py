"""Provider modules for the Key Proxy (packet 2b).

Each provider module owns the operation taxonomy and egress for exactly one
upstream credential consumer. Lookup is a closed registry: an unknown
provider name yields ``None`` and the caller fails closed — new providers
become reachable only by being consciously added here.
"""

from __future__ import annotations

from types import ModuleType
from typing import Optional

from keyproxy.providers import anthropic

_PROVIDERS: dict[str, ModuleType] = {anthropic.PROVIDER: anthropic}


def get_provider(name: object) -> Optional[ModuleType]:
    """Return the provider module for ``name``, or ``None`` (fail closed)."""
    if not isinstance(name, str):
        return None
    return _PROVIDERS.get(name)
