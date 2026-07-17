"""BYOK Key Proxy — the only service that ever sees a plaintext user API key.

See docs/proposals/byok-key-proxy-plan.md. Packet 1a ships the envelope
crypto (crypto.py); the FastAPI app and provider modules land in Phase 2+.
"""
