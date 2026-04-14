# Phase 8 — UI Integration Runbook
### Stock Portfolio Manager — Agentic Market Intelligence System

---

## Overview

This runbook documents Phase 8: surfacing agent data in the React frontend. The signal scanner and deep analysis pipeline now have dedicated UI surfaces, and the dashboard shows live agent system health.

**Prerequisites:** Phases 1–7 complete.

---

## What Was Built in This Phase

| File | Purpose |
|------|---------|
| `api/agents.py` | New Flask blueprint — `/api/agents/signals`, `/api/agents/recommendations`, `/api/agents/health` |
| `api/app.py` | Registered `agents_bp` |
| `frontend/src/api/client.ts` | Added 401 → redirect to `/auth/login` |
| `frontend/src/api/agents.ts` | TypeScript types + `agentsApi` functions |
| `frontend/src/hooks/useAgents.ts` | React Query hooks: `useAgentSignals`, `useAgentRecommendations`, `useAgentHealth` |
| `frontend/src/components/agents/AgentHealthWidget.tsx` | Dashboard card — market status + circuit breaker states |
| `frontend/src/components/agents/SignalsPage.tsx` | Top-level page at `/agents/signals` |
| `frontend/src/components/securities/RecommendationPanel.tsx` | "Agent Analysis" tab in SecurityDetailPage |
| `frontend/src/App.tsx` | Added `Signals` nav item + `/agents/signals` route |
| `frontend/src/components/dashboard/DashboardPage.tsx` | Added `AgentHealthWidget` in the stat cards row |
| `frontend/src/components/securities/SecurityDetailPage.tsx` | Added tab 6 "Agent Analysis" → `RecommendationPanel` |

---

## 1 — Backend API Endpoints

### `GET /api/agents/signals`

Returns recent `agent_signals` rows for the authenticated tenant, ordered newest first.

**Auth:** `@require_auth` (session JWT cookie)

**Query parameters:**

| Param | Default | Description |
|-------|---------|-------------|
| `symbol` | — | Filter to one symbol (e.g. `AAPL`) |
| `direction` | — | `buy` \| `sell` \| `neutral` |
| `days` | `30` | Look-back window |
| `limit` | `50` | Max rows (capped at 200) |

**Example:**

**fish / bash**
```bash
curl -s "http://localhost:5001/api/agents/signals?symbol=AAPL&days=14" \
  --cookie "session=<JWT>" | python3 -m json.tool
```

**Example response:**
```json
{
  "signals": [
    {
      "id": "3f1e...",
      "symbol": "AAPL",
      "score": 6,
      "direction": "buy",
      "triggers": {"rsi": "oversold", "macd": "bullish_cross", "unusual_calls": "spike"},
      "escalated": true,
      "fired_at": "2026-04-13T10:32:15.123456"
    }
  ],
  "count": 1
}
```

---

### `GET /api/agents/recommendations`

Returns recent `agent_recommendations` rows for the authenticated tenant.

**Auth:** `@require_auth`

**Query parameters:**

| Param | Default | Description |
|-------|---------|-------------|
| `symbol` | — | Filter to one symbol |
| `limit` | `20` | Max rows (capped at 100) |

**Example:**

**fish / bash**
```bash
curl -s "http://localhost:5001/api/agents/recommendations?symbol=AAPL&limit=1" \
  --cookie "session=<JWT>" | python3 -m json.tool
```

**Example response:**
```json
{
  "recommendations": [
    {
      "id": "a2bc...",
      "symbol": "AAPL",
      "recommendation": "BUY",
      "conviction": "HIGH",
      "entry_low": 184.50,
      "entry_high": 187.00,
      "price_target": 210.00,
      "stop_loss": 178.00,
      "details": {
        "score": 18,
        "bull_case": ["MACD bullish cross", "DAOI flipped buy-on-rally"],
        "bear_case": ["IV rank elevated"],
        "options_play": "Buy April 190C / sell 200C spread"
      },
      "fired_at": "2026-04-13T10:35:00.000000"
    }
  ],
  "count": 1
}
```

---

### `GET /api/agents/health`

Returns circuit breaker states and market open status. No authentication required.

**fish / bash**
```bash
curl -s http://localhost:5001/api/agents/health | python3 -m json.tool
```

**Example response:**
```json
{
  "market_open": true,
  "circuit_breakers": {
    "get_rsi": {"state": "closed", "error_count": 0}
  },
  "timestamp": "2026-04-13T10:00:01.123456"
}
```

---

## 2 — Frontend: Signals Page

Navigate to `/agents/signals` via the **Signals** nav item (bolt icon in the top bar).

Features:
- Filterable by **symbol**, **direction** (buy/sell/neutral), and **look-back window** (7/14/30/60/90 days)
- Score bar shows conviction level (9 segments for max ±9)
- Triggers are displayed as chips — hover for the raw value
- Clicking a row navigates to `SecurityDetailPage` for that symbol
- Escalated signals (Pub/Sub P3) are labelled

---

## 3 — Frontend: Agent Analysis Tab

Open any security's detail page (`/securities/AAPL`) and click the **Agent Analysis** tab (rightmost tab).

Shows the most recent deep analysis result:
- **Recommendation badge** (BUY / SELL / HOLD / AVOID) with color coding
- **Conviction** (HIGH / MEDIUM / LOW)
- **Score gauge** — ±27 scale
- **Entry range**, **price target**, **stop loss**
- **Bull case / Bear case** — up to 5 bullet points per side
- **Options play** — suggested structure from the options analysis phase
- If no analysis has been run yet, a descriptive alert is shown

---

## 4 — Frontend: Dashboard Health Widget

The **Agent System** card in the Dashboard stat row shows:
- Market open/closed status (green / amber)
- Per-tool circuit breaker states as dots (green = closed, red = open)
- Hover over each dot for error count or reset countdown
- Refreshes every 60 seconds automatically

---

## 5 — JWT Expiry Handling

`frontend/src/api/client.ts` now redirects to `/auth/login` on any HTTP 401 response. This covers:
- Session cookie expired (1-hour TTL)
- Cookie missing (unauthenticated direct navigation)
- Token invalidated server-side

No manual session refresh is required — the OAuth callback re-issues a fresh JWT and returns the user to the app.

---

## Gotchas Reference

| Symptom | Cause | Fix |
|---------|-------|-----|
| `GET /api/agents/signals` returns 401 | Session cookie expired or missing | Re-authenticate via `/auth/login` |
| `RecommendationPanel` shows "no analysis run yet" | Deep Analysis hasn't been triggered for this symbol | Trigger manually: `POST /run-deep-analysis {"tenant_id": "...", "symbol": "AAPL", "source": "manual"}` |
| `AgentHealthWidget` shows no circuit breakers | No tools have been called yet this process lifetime | Circuit breakers are populated lazily on first tool error — initial state is empty |
| Signals page shows 0 results | `CIRCUIT_BREAKER_ENABLED=true` and market was closed when scanner ran | Force a run: `POST /run-signal-scanner {"force": true}` |

---

*Runbook prepared April 2026. Covers Phase 8 of the Agentic Market Intelligence System — GCP Edition.*
