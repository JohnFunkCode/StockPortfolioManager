# RFC: Security Architecture Review – BYOK Key Proxy
This review was created with a guided review discussion with ChatGPT about the byok-key-proxy-plan.md document.
Note: all this feedback has been incorporated into the latest plan.

**Status:** Draft for Architecture Review Board

## 1. Purpose

This document records an independent security review of the proposed BYOK Key Proxy architecture and recommends changes prior to production deployment.

## 2. Executive Summary

**Recommendation:** Proceed with the architecture after targeted hardening.

The design is fundamentally sound. The envelope cryptography, browser vault, and Key Proxy isolation are strong. The largest remaining risks are authorization, trust-boundary separation, operational resilience, and abuse resistance—not cryptography.

### Overall Risk

| Area | Rating |
|---|---|
| Cryptography | Low |
| Identity | Low |
| Authorization | Critical |
| Client Trust | High |
| Operational Resilience | High |
| Trading Reuse | Critical |

## 3. Architecture Overview

### Security objectives

- User-owned API keys
- Plaintext visible only inside Key Proxy
- No persistent server-side credential storage
- Strong replay protection
- Minimal trusted computing base
- Provider isolation
- Future extensibility

### Primary trust boundaries

1. Browser runtime
2. Frontend deployment
3. Express gateway
4. quantcore-api
5. Key Proxy
6. Provider API

## 4. Threat Model (STRIDE)

### Spoofing
- Forged service JWTs
- Session theft
- Browser session theft

Mitigation:
- Asymmetric JWTs
- Audience validation
- WebAuthn for sensitive scopes

### Tampering
- Envelope modification
- Scope modification
- Provider request modification

Mitigation:
- AEAD
- Canonical hashing
- Proxy validation

### Repudiation
Mitigation:
- Immutable audit records
- Per-user identity
- Correlation IDs

### Information Disclosure
Threats:
- Logging
- Tracing
- Debugging
- Crash dumps

Mitigation:
- Allowlist logging
- Secret scanning
- Redaction

### Denial of Service
Threats:
- Replay flood
- Token exhaustion
- Cost amplification

Mitigation:
- Budgets
- Rate limits
- Cost ceilings

### Elevation of Privilege
Threats:
- API compromise
- Overly broad scopes

Mitigation:
- Independent authorization
- Narrow scopes

## 5. Data Flow

Browser
→ Express/IAP
→ quantcore-api
→ Key Proxy
→ Provider

Only the Key Proxy may decrypt credentials.

## 6. Trust Boundary Table

| Component | Secrets | Trusted? | Notes |
|---|---|---|---|
| Browser | User key (temporary) | No | Assume compromise |
| Express | Signing key | Yes | Identity only |
| quantcore-api | None ideally | Partially | Do not allow signing |
| Key Proxy | Decryption key | Yes | Smallest TCB |
| Provider | API key | External | Out of scope |

## 7. Principal Findings

### Critical

1. Replace HS256 with asymmetric identity.
2. Treat Google IAP as authentication only.
3. Constrain API key usage with server policy.
4. Narrow chat scopes.
5. Add durable replay state.

### High

- Frontend supply chain
- Logging
- Egress controls
- Cost governance

### Medium

- Argon2id
- Unlock policy
- Browser UX

## 8. Recommended Architecture

Google IAP
→ Identity
→ Authorization Service
→ Signed Scoped Capability
→ Key Proxy
→ Provider

The client supplies intent—not authority.

## 9. Attack Trees

### Browser Compromise
Goal: misuse key.

Controls:
- scope limits
- budgets
- policy
- WebAuthn (sensitive actions)

### API Compromise
Goal: abuse sessions.

Controls:
- asymmetric identity
- proxy enforcement
- independent grants

### Replay
Goal: redeem twice.

Controls:
- one-time nonce
- persistent replay cache
- expiry

## 10. Security Invariants

1. Only Key Proxy decrypts credentials.
2. Verifiers cannot mint identities.
3. One envelope = one redemption.
4. Every session is bounded.
5. Costs are bounded.
6. Browser never grants authority.
7. Logging never records secrets.
8. Replay always fails.
9. Policy is enforced independently.
10. Adjacent services may be compromised.

Each invariant should map to automated tests.

## 11. Risk Register

| Risk | Severity | Mitigation |
|---|---|---|
| Shared signing key | Critical | Asymmetric JWT |
| API abuse | Critical | Narrow scopes |
| Replay durability | High | External nonce store |
| Frontend compromise | High | Signed releases, CSP |
| Excessive spend | High | Cost budgets |
| Proxy compromise | Accepted | Minimize TCB |

## 12. Alternatives

- Database encryption: simpler, weaker.
- OAuth delegation: preferred where available.
- Client certificates: enterprise only.
- Confidential Computing: future enhancement.
- HSM-backed envelope keys: future enhancement.

## 13. Production Readiness Checklist

### Identity
- [ ] Asymmetric JWT
- [ ] Audience validation

### Authorization
- [ ] Server-derived scopes
- [ ] Cost limits

### Infrastructure
- [ ] Replay persistence
- [ ] Egress restrictions

### Frontend
- [ ] Signed releases
- [ ] CSP
- [ ] Trusted Types

### Operations
- [ ] Secret scanning
- [ ] Log redaction
- [ ] Chaos testing

### Security Testing
- [ ] Replay races
- [ ] Rollout testing
- [ ] Compromised API simulation
- [ ] Cost abuse
- [ ] SSRF
- [ ] Unicode canonicalization

## 14. Decision Log

| Decision | Recommendation |
|---|---|
| Google IAP | Keep |
| Key Proxy | Keep |
| SPKI pinning | Keep |
| HS256 | Replace |
| Replay cache | Externalize |
| Trading reuse | Separate review |

## 15. Residual Risks

Accepted:
- Endpoint malware
- Browser compromise
- Provider compromise
- Cloud administrator compromise
- Key Proxy compromise

## 16. Conclusion

The architecture is well conceived and should proceed after hardening authorization, identity separation, replay durability, operational controls, and client abuse resistance.

It provides a strong foundation for BYOK LLM credentials. Financial workflows should undergo an independent architecture review before reuse.
