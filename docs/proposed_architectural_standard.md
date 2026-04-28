# Proposed Architectural Standard: Service-Centered Multi-Interface Platform

## 1. Purpose
This standard defines how we structure systems that expose functionality through:
- User-facing APIs (REST/WebSocket)
- AI interfaces (MCP tools)
- Operational workflows (CLI / cron jobs)

Goals:
- Eliminate duplicated business logic  
- Maintain strict control over trading-critical operations  
- Support multiple clients safely and consistently  
- Enable enforcement in code reviews and automation  

---

## 2. Core Principle
All business logic lives in shared application services.  
All external interfaces are thin adapters.

---

## 3. High-Level Architecture
Clients (UI, AI, Cron)  
→ Adapters (REST, MCP, CLI)  
→ Application Services  
→ Domain Layer  
→ Provider Interfaces  
→ Gateways (Finance, Alpaca)

---

## 4. Process Model
Separate processes:
- Flask REST API
- FastMCP server
- CLI (cron)

All import shared `trading_core` package.

---

## 5. Repository Structure
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
    rest_api/
    mcp_server/
    cli/

---

## 6. Layer Responsibilities

### Application Services
Own:
- Business logic
- Risk checks
- Entitlements
- Order lifecycle
- Audit
- Idempotency

### Domain Layer
Own:
- Core models
- Pure logic

### Provider Interfaces
Define contracts for external systems.

### Gateways
Implement provider interfaces.
Handle API calls, auth, retries.
Do NOT contain business logic.

---

## 7. Interface Adapters

### REST API
- UI contract
- Calls services only

### MCP Server
- AI tools
- Read-only by default
- High-risk actions gated

### CLI
- Operational jobs
- Calls services directly

---

## 8. Strict Rules

1. No business logic outside services  
2. No direct gateway calls from adapters  
3. Gateways must be thin  
4. Shared core must be stateless or controlled  
5. Cross-cutting concerns centralized  
6. No interface-to-interface calls  

---

## 9. Data Flow

Stock Quote:
UI → REST → Service → Gateway  
AI → MCP → Service → Gateway  
CLI → Job → Service → Gateway  

Order:
UI/AI → Service → Risk → Broker → Audit  

---

## 10. Safety Model

MCP default:
- Read-only

High-risk actions:
- Require confirmation
- Require audit
- Require idempotency

---

## 11. Operational Jobs

CLI examples:
- reconcile_positions
- refresh_market_data

Use job runner:
- locking
- retries
- logging

---

## 12. Codex Guidance

Always:
- Use services
- Use provider interfaces

Never:
- Add business logic in adapters or gateways

---

## 13. Anti-Patterns

Avoid:
- REST calling gateways directly
- MCP duplicating logic
- CLI calling REST
- Gateways growing into services

---

## 14. Summary

System is service-first, not REST-first or MCP-first.
