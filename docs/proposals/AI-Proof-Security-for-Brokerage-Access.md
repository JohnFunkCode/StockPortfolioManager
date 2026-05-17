# Claude Desktop Brokerage Approval Architecture

## Purpose

This document captures a reference design for letting Claude Desktop interact with brokerage-related systems **without ever giving the model direct access to trading credentials**. The design focuses on approval gates, least privilege, secret isolation, and server-side enforcement.

---

## Core Architecture

```text
Claude Desktop
  -> local MCP/client
  -> GCP broker-gateway API
  -> approval workflow / approval UI
  -> executor service
  -> brokerage API
```

### Why this is needed

The core problem is not just storing a secret securely. The real problem is preventing a model or loosely controlled toolchain from using a high-value credential in unsafe ways.

This architecture separates:

- **proposal** from **execution**
- **human approval** from **machine action**
- **read-only access** from **trading access**
- **secret storage** from **secret use**

That separation reduces the chance that a prompt, tool bug, logging mistake, or overly broad interface can directly trigger financially impactful operations.

---

## Section 1: Local MCP/Client

### Design

Claude Desktop should communicate only with a narrow local client or MCP server exposing a small set of tools such as:

- `get_positions`
- `get_cash_balance`
- `propose_order`
- `get_pending_approval_status`

The client should **not** expose:

- arbitrary brokerage API passthrough
- raw secret retrieval
- generic HTTP proxying
- shell access to the backend

### Why this is needed

Claude should not be given a generic capability surface. A narrow interface forces all meaningful actions through known, reviewable operations.

If the model can make arbitrary requests, it becomes impossible to guarantee that approval, policy, and logging are enforced consistently. Narrow tools keep the attack surface small and make review practical.

---

## Section 2: Broker Gateway API

### Design

The gateway is the public-facing service in GCP that accepts requests from the local client. It should:

- authenticate the caller
- validate request structure
- classify the request by risk
- execute read-only actions directly
- convert trading requests into pending approval records

The gateway should run with its own service account and minimal permissions.

### Why this is needed

The gateway acts as a control plane. It is the first place where untrusted or semi-trusted requests become structured actions.

Without this layer, Claude or the local client might directly invoke the executor or brokerage API. That would collapse the distinction between a proposed action and an approved action.

The gateway also ensures that every request is normalized into a consistent format before anything important happens.

---

## Section 3: Approval Store

### Design

Every financially impactful action should be written as a structured pending record in a datastore such as Firestore.

A record should include fields such as:

- action ID
- account ID
- symbol
- side
- quantity
- order type
- limit price
- time in force
- risk tier
- status
- expiry time
- idempotency key
- canonical request hash
- policy version

### Why this is needed

Approval must be tied to an exact structured request, not to loose natural-language intent.

If the system stores only a text summary, then a later service or code path can reinterpret or mutate what was “approved.” A canonical structured record prevents drift between what was reviewed and what gets executed.

The request hash is especially important because it allows the executor to verify that the action being executed is byte-for-byte equivalent to the one that was approved.

---

## Section 4: Approval UI

### Design

A small authenticated approval UI should display pending actions and let an authorized human explicitly approve or reject them.

The screen should show the exact action details:

- account
- action type
- symbol
- quantity
- order type
- price constraints
- notional exposure
- expiry time

### Why this is needed

Approval must be a **server-side** control, not a prompt convention.

A separate approval UI ensures that the human sees the exact structured request outside the model conversation. That reduces the risk of approving something based on a misleading summary, model wording, or missing field.

It also creates a clean boundary where human intent is captured independently of Claude’s phrasing.

---

## Section 5: Approval Token or Workflow Resume Signal

### Design

After approval, the system should produce either:

- a short-lived, one-time approval token bound to the exact action, or
- a workflow callback/resume event bound to that action

The approval artifact should be tied to:

- action ID
- request hash
- approver identity
- expiration time
- one-time-use semantics

### Why this is needed

Approval should authorize **one specific action**, not grant open-ended power.

A reusable or long-lived approval token would turn the approval system into a disguised permission grant. One-time approval artifacts prevent replay, reduce lateral use, and keep each trade tied to a single human decision.

This also lets the executor verify that the approved action has not changed since approval.

---

## Section 6: Executor Service

### Design

The executor should be a separate service from the gateway. Its only job is to:

- load the pending approved action
- verify approval state and token validity
- re-check policy constraints
- fetch the trading secret from Secret Manager
- submit the request to the brokerage API
- persist the outcome
- mark the action executed or failed

The executor should be the **only** component allowed to access the trading credential.

### Why this is needed

This is the most important separation in the design.

If the gateway or local client can also fetch the trading secret, then approval becomes less meaningful because multiple components can bypass the intended control path.

By isolating trading-secret access to the executor, the system ensures that secret use happens only after:

1. request validation
2. human approval
3. policy enforcement

That sharply reduces the number of places where a bug or prompt can trigger a live trade.

---

## Section 7: Split Secrets by Capability

### Design

Use at least two separate secrets and, if the broker supports it, two different credentials:

- `broker-readonly`
- `broker-trading`

The gateway can use the read-only credential for balances and positions. Only the executor can use the trading credential.

### Why this is needed

Read-only access and trading access do not have the same risk profile.

If one credential does both, then any bug in a seemingly harmless read path could become a trading incident. Capability-splitting ensures that informational features cannot accidentally inherit execution power.

This is a classic least-privilege control. It limits blast radius when something fails.

---

## Section 8: Server-Side Policy Enforcement

### Design

Even after approval, the executor should apply hard rules such as:

- allowlisted account IDs
- allowlisted symbols
- no options
- no margin
- no short selling
- max single-order notional
- max daily aggregate notional
- order type restrictions
- trading window constraints
- stale approval rejection
- idempotency checks

### Why this is needed

Approval alone is too weak. Humans approve the wrong thing, UIs can mislead, and contexts change.

Server-side policy enforcement provides a second independent barrier. It ensures that even an approved action cannot execute if it violates system rules.

This matters because the most dangerous failure mode is treating approval as a blanket override. It should not be.

---

## Section 9: Read Path and Trade Path Separation

### Design

The system should have two clearly separate paths:

### Read-only path

For:

- balances
- positions
- order history
- market data lookups

This path can operate without human approval, using only read-only credentials.

### Trading path

For:

- buy/sell orders
- cancel order requests
- any financially impactful operation

This path should always go through pending approval and executor-side policy checks.

### Why this is needed

If read and write operations share the same implementation path, complexity and risk rise quickly.

Separate paths make it easier to reason about which actions can happen automatically and which must stop for review. They also let you audit low-risk and high-risk operations differently.

---

## Section 10: Idempotency and Replay Protection

### Design

Every proposed action should have an idempotency key and a one-time execution model.

The executor should reject:

- duplicate execution attempts
- reused approval tokens
- expired requests
- mismatched request hashes

### Why this is needed

Distributed systems retry. Users double-click. Clients reconnect. Queues redeliver.

Without idempotency, a single approved trade can become multiple executions due to transient infrastructure behavior rather than actual user intent.

Replay protection ensures that approval is consumed exactly once and cannot be reused to perform the same or a modified action later.

---

## Section 11: Logging and Audit Trail

### Design

Log these events:

- proposal created
- request classified
- approval shown
- approval granted or rejected
- executor invoked
- secret accessed by executor
- brokerage call attempted
- brokerage call result normalized
- action completed or failed

Do **not** log:

- raw secrets
- auth headers
- full credential material

### Why this is needed

Financially impactful systems need traceability.

When something goes wrong, the key questions are:

- who proposed the action?
- who approved it?
- what exact structured request was approved?
- what policy version was applied?
- what was sent to the brokerage API?
- what happened next?

Without an audit trail, you cannot reliably investigate failures, disputed orders, or suspicious activity.

---

## Section 12: Explicitly Out-of-Scope Operations

### Design

The initial version should exclude:

- money movement
- ACH or wire transfers
- beneficiary changes
- options trading
- margin trading
- short selling
- recurring automated strategies
- arbitrary brokerage endpoint passthrough

### Why this is needed

The first version should focus on a narrow, defensible subset of operations.

High-risk features multiply the failure modes and make policy design much harder. Excluding them early keeps the architecture reviewable and reduces the chance of accidentally exposing an irreversible financial operation through a system that has not yet matured.

---

## Design Principles Summary

This design is built around a few non-negotiable principles:

1. **Claude can propose, not directly execute.**
2. **Approval is enforced by the backend, not by prompt instructions.**
3. **Only the executor can access the trading credential.**
4. **Every approved action must match a canonical structured request.**
5. **Policy enforcement still applies after approval.**
6. **Read-only capability and trading capability must remain separate.**
7. **Every financially impactful action must be auditable.**

---

## Recommended First Version

For a first production-oriented implementation, keep it narrow:

- Cloud Run gateway
- Cloud Run executor
- Firestore pending action store
- Secret Manager with split read-only and trading secrets
- small approval UI
- one-time approval token
- strict allowlists and notional caps
- no money movement, options, or margin

This is enough to be useful while keeping the control plane understandable.

---

## Bottom Line

The point of this design is not merely to hide a secret. The point is to ensure that **no single model interaction, prompt, bug, or convenience shortcut can directly turn into an unauthorized trade**.

That requires layered controls:

- narrow interfaces
- structured pending actions
- explicit approval
- isolated secret use
- post-approval policy enforcement
- auditability

Anything less is mostly just moving risk around.
