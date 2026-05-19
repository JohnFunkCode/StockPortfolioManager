++++# Architectural Standard: Service-Centered Multi-Interface Platform

## 1. Purpose

This standard defines how we structure systems that expose functionality through:

- User-facing web applications (Node.js front end)
- User-facing APIs (REST/WebSocket)
- AI interfaces (MCP tools)
- Operational workflows (CLI / cron jobs)

The goal is to:

- Eliminate duplicated business logic  
- Maintain strict control over trading-critical operations  
- Support multiple clients (UI, AI, jobs) safely and consistently  
- Provide a structure that can be enforced in code reviews and automation  

---

## 2. Core Principle

> **All business logic lives in shared application services.  
All external interfaces are thin adapters.**

---

## 3. High-Level Architecture

```text
Clients
-------
Node.js Front End        AI Agents        Cron / Ops
        |                    |                |
   REST/WebSocket API     MCP Server         CLI
   \                 |                /
    -------- Application Services --------
                   |
             Domain Layer
                   |
        Provider Interfaces (Ports)
                   |
    -----------------------------------
    |                                 |
Market Data Gateway            Broker Gateway
(Finance APIs)                 (Alpaca)
```

---

## 4. Process Model

Each interface runs as its own process. The Node.js front end is also its own application/process, but it is a client of the REST/WebSocket API, not a peer adapter into the core Python services.

```text
Process 1: Node.js front-end application
Process 2: Flask REST/WebSocket API
Process 3: FastMCP server
Process 4: CLI (invoked by cron)
```

Python service processes:

- Import the same `trading_core` package
- Share no business logic between themselves
- Do not call each other over HTTP

The Node.js front end:

- Calls the Flask REST/WebSocket API
- Does not call MCP tools directly
- Does not call external provider gateways directly
- Does not contain trading business logic
- Owns UI state, presentation, form behavior, and user interaction flow

---

## 5. Repository Structure

```text
repo/
  trading_core/
    domain/
    services/
    providers/
    authz/
    risk/
    audit/
    jobs/
    config/

  apps/
    frontend_node/
    rest_api/
    mcp_server/
    cli/
```

---

## 6. Layer Responsibilities

### 6.1 Application Services (MANDATORY CENTER)

Own:

- Business logic
- Validation
- Risk checks
- Entitlements
- Order lifecycle
- Audit events
- Idempotency
- Transactions

Example:

```python
class OrderService:
    def place_order(self, request: OrderRequest) -> OrderResult:
        self.entitlements.check(request.user, request.account)
        self.risk.validate(request)
        order = self._create_internal_order(request)

        broker_result = self.broker.place_order(order)

        self.audit.record(order, broker_result)
        return broker_result
```

---

### 6.2 Domain Layer

Own:

- Core models (Order, Quote, Position)
- Pure business rules
- No external dependencies

---

### 6.3 Provider Interfaces (Ports)

Define provider-neutral contracts:

```python
class BrokerProvider:
    def place_order(self, order: OrderRequest) -> BrokerOrderResult: ...
```

---

### 6.4 Gateways (Adapters to External Systems)

Implement provider interfaces:

- Alpaca → BrokerProvider  
- Finance data provider → MarketDataProvider  

Responsibilities:

- API calls
- Authentication
- Retry logic
- Rate limiting
- Error translation

Non-responsibilities:

- Business rules
- Risk decisions
- Entitlements

---

## 7. Interface Adapters

### 7.0 Node.js Front End

Purpose: user-facing trading application

Must:

- Call the REST/WebSocket API for backend operations
- Treat REST/WebSocket schemas as the application contract
- Handle presentation, routing, client-side state, form interaction, and user confirmation workflows
- Display server-provided validation, risk, entitlement, and order-status results

Must NOT:

- Call MCP tools directly
- Call provider gateways such as Finance or Alpaca directly
- Implement trading business logic
- Bypass backend risk, entitlement, audit, or idempotency controls

The Node.js front end is a client, not a domain-service adapter. It should not import or duplicate backend domain logic.

---

### 7.1 REST API (Flask)

Purpose: UI contract

Must:

- Call application services only
- Handle HTTP concerns (auth, serialization, errors)

Must NOT:

- Contain business logic
- Call gateways directly

---

### 7.2 MCP Server (FastMCP)

Purpose: AI tool interface

Must:

- Call application services
- Be conservative (read-only by default)

High-risk operations (e.g., trading):

- Require explicit confirmation
- Must go through full service stack

---

### 7.3 CLI (Cron Jobs)

Purpose: operational workflows

Must:

- Call application services directly
- Use job runner (locking, retries, audit)

Must NOT:

- Call REST APIs unless externalized

---

## 8. Strict Rules (Codex-Enforceable)

### Rule 1 — No Business Logic Outside Services

❌ Forbidden:

```python
# REST endpoint
if price > limit:
    reject_order()
```

✅ Required:

```python
order_service.place_order(request)
```

---

### Rule 2 — No Direct Gateway Access from Adapters

❌ Forbidden:

```python
@app.get("/quote")
def get_quote():
    return finance_gateway.get_quote(symbol)
```

✅ Required:

```python
return market_data_service.get_quote(symbol)
```

---

### Rule 3 — Gateways Must Be Thin

❌ Forbidden:

```python
class AlpacaGateway:
    def place_order(...):
        if order.amount > 100000: reject()
```

✅ Required:

```python
# Gateway only translates and forwards
```

---

### Rule 4 — Shared Core Must Be Stateless (or Controlled State)

- No global mutable state
- All state flows through DB/services

---

### Rule 5 — Cross-Cutting Concerns Are Centralized

Must exist in core:

- Audit logging
- Idempotency
- Risk
- Entitlements
- Transactions

---

### Rule 6 — No Interface-to-Interface Calls

❌ Forbidden:

```text
CLI -> REST API -> Service
MCP -> REST API -> Service
```

✅ Required:

```text
CLI -> Service
MCP -> Service
REST -> Service
```

---

## 9. Data Flow Example

### Stock Quote

```text
Node.js Front End -> REST/WebSocket -> MarketDataService -> FinanceGateway -> Provider
AI Agent          -> MCP            -> MarketDataService -> FinanceGateway -> Provider
CLI               -> Job            -> MarketDataService -> FinanceGateway -> Provider
```

---

### Order Placement

```text
Node.js Front End -> REST/WebSocket -> OrderService
                                      -> RiskService
                                      -> EntitlementService
                                      -> BrokerProvider (Alpaca)
                                      -> AuditService

AI Agent -> MCP -> OrderService (same path, stricter controls)
```

---

## 10. Safety Model for Trading

### Default MCP posture:

- Read-only tools allowed
- Write actions gated or disabled

High-risk operations:

- place_order
- cancel_order
- fund transfers

Must include:

- confirmation step
- idempotency key
- full audit trail

---

## 11. Operational Jobs

Example CLI:

```bash
python -m apps.cli.reconcile_positions
python -m apps.cli.refresh_market_data
```

Each job:

- Uses services
- Uses job runner for:
  - locking
  - retries
  - logging

---

## 12. Codex Guidance (IMPORTANT)

When generating or reviewing code, enforce:

### Always:

- Create or reuse an application service
- Route all logic through services
- Use provider interfaces, not concrete gateways

### Never:

- Add business logic in:
  - Node.js front-end components
  - REST endpoints
  - MCP tools
  - CLI commands
  - gateway classes

### When unsure:

> Default to adding logic in `trading_core/services`

---

## 13. Anti-Patterns

Avoid:

```text
1. Node.js front end calling MCP tools directly
2. Node.js front end calling Finance or Alpaca directly
3. Trading business logic duplicated in front-end code
4. “Convenience shortcut” from REST → gateway
5. Duplicate logic in MCP and REST
6. CLI calling REST for internal workflows
7. Gateways growing into service layers
8. AI tools bypassing risk/entitlement checks
```

---

## 14. Migration Guidance

If existing code violates this:

1. Extract logic into services  
2. Replace adapter logic with service calls  
3. Wrap gateway calls behind provider interfaces  
4. Move duplicated trading logic out of the Node.js front end and into backend services  
5. Add missing cross-cutting services (risk, audit, etc.)  

---

## 15. Summary

- Multiple processes are expected  
- Multiple interfaces are expected  
- **Only one place for business logic is allowed**  

> The system is not REST-first or MCP-first.  
> It is **service-first**.
