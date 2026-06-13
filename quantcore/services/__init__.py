"""Services — the single home for business logic (architectural standard v2 §5).

MCP tools, REST routes, and CLI scripts are thin adapters that make exactly one
call into this layer. Services receive repositories/gateways (and, for
composite services, other services) via constructor injection; the only place
wiring happens is services/registry.py. Service modules never import each
other or the registry.
"""
