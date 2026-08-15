# Sidekick's tool vocabulary — close the accidental gaps, make the deliberate ones legible

**Source issue:** [#208](https://github.com/JohnFunkCode/StockPortfolioManager/issues/208)
**Status:** **IN PROGRESS** — plan approved 2026-08-14, implementation started
**Shape:** one PR, six commits (auth/route plumbing → `chat.py` → `chat_tools.py` → `registry.py` → tests → docs)
**Related:** [`architectural-standard-v2.md`](architectural-standard-v2.md) (§5.5 is itself amended
by this work), [`../capabilities-matrix.md`](../capabilities-matrix.md) (the doc the issue asks to
re-align), [`byok-key-proxy-plan.md`](byok-key-proxy-plan.md) (#100 — the identity model the
portfolio tools have to live inside)

Line numbers were captured on **2026-08-14** against `main` at `988263b`. If one has drifted,
search for the quoted code.

---

## The question, and the actual answer

Issue #208 asks why Sidekick doesn't have access to all the tools across the MCP servers. The audit
found three separate answers, and only one of them is the thing the title suspected.

**1. Most of the gap is deliberate policy, working as designed.**
[`architectural-standard-v2.md`](architectural-standard-v2.md) §5.5 already says exposing an
endpoint as a tool is a curation decision, and commit `c5df10f` shows the reasoning applied
concretely — `scan_arbitrage` returns summary rows rather than full per-pair statistics, because
"ten candidates with complete statistics each would crowd the conversation's context window." That
is not drift. Sidekick having 10 tools against the MCP servers' 62 is mostly a feature.

**2. But `SYSTEM_PROMPT` is stale, and that is the likeliest cause of the reported symptom.**
[`quantcore/services/chat.py:47`](../../quantcore/services/chat.py) names four components
(`signals`, `live_price`, `price_chart`, `spread_payoff`) out of the **fourteen** registered in
`BACKEND_COMPONENT_REGISTRY`, and never mentions the arbitrage, portfolio, or fundamentals tools at
all. A model does not reach for a tool its own prompt never names. Every registry entry can be
perfectly wired and the capability still won't surface in a conversation.

**3. Two gaps are genuinely accidental.**

| Gap | Evidence it was an oversight |
|---|---|
| `discover_arbitrage_pairs` | Its UI component `arbitrage_discovery` is **already registered on both sides** — `BACKEND_COMPONENT_REGISTRY` and `frontend/src/chat/componentRegistry.tsx` — with no tool able to produce the data it renders. Commit `45b9214` added four arbitrage viz components; commit `c5df10f` had added only three of the four arbitrage tools. |
| `portfolio-server` | Zero chat-tool coverage across all six tools, while `portfolio_table` / `portfolio_allocation` / `symbol_lots` are all registered components. Sidekick can *render* a portfolio card but cannot reason over the numbers in prose — "how's my portfolio doing?" has no answer path. |

## The second problem: the docs that hid all of this

[`docs/capabilities-matrix.md`](../capabilities-matrix.md) exists specifically to keep
MCP/REST/WebUI/Sidekick parity visible. It is stale on **both** sides of the comparison it is meant
to make:

| Claim in the matrix | Verified reality |
|---|---|
| 5 MCP servers, 51 unique tools (53 registrations) | **7 servers, 62 tools** |
| `stock-price-server` has 29 tools | 21 |
| `get_option_contracts` / `price_vertical_spread` are dual-registered on two servers | **False** — both exist only on `options-analysis-server`. No tool is dual-registered anywhere. |
| Sidekick has 7 data tools + 4 components | 10 tools + 14 components (13 + 14 after this work) |
| — | No "Domain: Arbitrage" section exists at all |
| — | "Portfolio & Watchlist Management" has no MCP or Sidekick columns despite six MCP tools |

**Correction, found during Step 4:** the dual-registration claim is *not* in
[`CLAUDE.md`](../../CLAUDE.md) — that was an assumption in the original plan, and checking it before
writing the fix is what caught it. CLAUDE.md carries a different stale number in the same class:
"The 5 remote MCP servers in `.mcp.json`", when there are 7. That one *is* worse for the reason the
plan gave — CLAUDE.md is auto-loaded into every agent session, so its wrong number is where every
future agent starts. Fixed in this PR, with the count and the counting command written in beside it.

Verified per-server counts — note the **anchored** pattern, `grep -c "^@mcp.tool" fastMCPTest/*.py`:

| Server | Tools |
|---|---|
| `arbitrage-server` | 5 |
| `company-fundamentals-server` | 12 |
| `market-analysis-server` | 3 |
| `news-sentiment-server` | 4 |
| `options-analysis-server` | 11 |
| `portfolio-server` | 6 |
| `stock-price-server` | 21 |
| **Total** | **62** |

**Gotcha — anchor the grep.** The unanchored `grep -c "@mcp.tool"` reports **12** for
`options-analysis` instead of 11: the module docstring in `fastMCPTest/options_analysis.py` mentions
`@mcp.tool()` in prose, and it counts. Total comes out 63, one tool that does not exist. Every count
in this plan and in the matrix was taken with `^@mcp.tool`.

## The third problem: §5.5 says the opposite of what it means

> **Never blanket-mirror the whole API.** Exposing an endpoint as a tool is a deliberate curation
> decision.

Read cold, that discourages exposure. The actual intent is the opposite: most APIs *should* reach an
MCP tool — thoughtfully shaped rather than mechanically derived. As written, the rule gives cover to
simply not exposing something, and never requires anyone to say why. That is precisely how
`discover_arbitrage_pairs` and `portfolio-server` fell through: silence looked like compliance.

---

## Step 1 — Rewrite §5.5 of the architectural standard

[`docs/proposals/architectural-standard-v2.md:119-124`](architectural-standard-v2.md). Invert the
rule from *default closed* to *default considered*:

- Most REST capabilities **should** reach an MCP tool — the AI surface is a first-class client, not
  an afterthought. What is forbidden is the **mechanical** mirror: one tool per endpoint, with
  parameters and descriptions inherited verbatim from the HTTP shape.
- A tool's shape is designed against a model's context budget, not the endpoint's response shape.
  Cite `scan_arbitrage` as the worked precedent.
- **Every new REST capability owes one of two artifacts in the same PR**: a designed MCP tool (name,
  parameters, LLM-facing description, and what it deliberately omits), or a written decision
  recording why it stays REST-only. **Silence is not a decision.**
- The read-only-by-default and write-gating bullets stay unchanged.

Then update §7 item 3 (line 204) to point at the strengthened §5.5 rather than restating
"curation decisions, not defaults" on its own terms.

**How to know it worked:** a reader who has just added a REST endpoint can tell from §5.5 alone what
they now owe, and reviewers have something checkable to ask for.

## Step 2 — Three new tools

### 2a. `quantcore/services/chat_tools.py` — three `TOOL_SCHEMAS` entries

Modeled on the existing arbitrage entries: say what the tool does, what it deliberately *isn't*, and
which `show_component` call to pair it with.

| Tool | Parameters | Notes |
|---|---|---|
| `discover_arbitrage_pairs` | `symbols` (required, comma-separated), `references`, `days`, `min_abs_correlation`, `require_economic_link` | Description must state that discovered pairs get **no NAV math and no convergence claim** — they are candidates for curation into `arb_universe.yaml`, not signals. Pairs with `show_component('arbitrage_discovery', …)`. |
| `get_portfolio_summary` | none | Cost basis, current value, gain/loss $ and %, dollars-per-day. |
| `get_symbol_lots` | `ticker` | Per-lot quantity, purchase price, trade date, gain/loss. |

**No `owner` parameter on any of them, ever.** This extends the existing `show_component` decision
(the model never picks whose data it sees) from component props to tool arguments. `show_component`'s
own schema needs no change — all four target components are already registered on both sides.

### 2b. `quantcore/services/chat.py` — handlers, and the owner question

`_handlers` gains `discover_arbitrage_pairs` inside the existing `if arbitrage is not None:` block
([`chat.py:351`](../../quantcore/services/chat.py)), plus a parallel `if portfolio is not None:`
block for the two portfolio tools. Handlers call services directly in-process, matching how the
arbitrage handlers already work — the documented Sidekick exception to Rule 6.

**The crux: how a BYOK chat session resolves `owner`.** `PortfolioService` methods all take `owner`.
The only existing resolver, `require_owner` ([`api/auth.py:240`](../../api/auth.py)), **403s the
entire request** on an unmapped identity. Three options, and why the third wins:

| Option | Why not |
|---|---|
| Reuse `require_owner` on the chat route | A chat turn serves many non-owner-scoped tools. Failing the whole turn — killing `get_stock_price` too — because portfolio identity is unprovisioned is a bad trade. |
| Default to `"john"` | A silent cross-user data leak the moment a second identity exists. Directly contradicts the principle behind decision #20. |
| **Soft-resolve at the route, degrade per tool call** | Fails closed on the one capability that needs an owner, leaves the rest of the turn standing. |

The chosen design:

1. Add `resolve_owner_or_none(principal)` beside `require_owner` in
   [`api/auth.py`](../../api/auth.py) — identical logic, returns `None` instead of raising, logs a
   **distinct** event name (`unmapped_principal_soft`) so the soft path stays distinguishable from
   the hard 403 in monitoring. Never auto-provisions.
2. [`api/routers/chat.py`](../../api/routers/chat.py) stays on `require_principal` and threads the
   result into a new `TurnContext.owner` field.
3. In `stream_chat`'s dispatch loop, add one owner-scoped special case beside the existing
   `show_component` one ([`chat.py:447`](../../quantcore/services/chat.py)): strip any
   model-supplied `owner` key, and if `context.owner is None`, emit a recoverable `is_error` tool
   result explaining the account isn't linked — **without ever touching the portfolio service**.
   Otherwise inject the resolved owner and fall through to the generic dispatch, reusing its
   existing status/error/logging machinery.

`registry.py` gains `portfolio=portfolio` on the `ChatService(...)` call. `portfolio` is already
constructed above `chat`, so no ordering change. `IdentityService` deliberately stays **out** of
`ChatService` — owner resolution belongs at the route boundary, and threading identity into the chat
service would give it a dependency it has so far avoided.

**One guard worth flagging in review:** the 25-symbol / 10-reference caps on discovery live only in
[`api/routers/arbitrage.py:29-30`](../../api/routers/arbitrage.py), not in
`ArbitrageService.discover_pairs()`. Sidekick calls the service directly, bypassing that route, so
the cap must be re-asserted in the handler as a `ValueError` — which `stream_chat` already surfaces
as a recoverable tool error — or an LLM tool call can trigger an uncapped sweep. This makes
discovery stricter than its arbitrage siblings (`analyze_arbitrage_pair` and `scan_arbitrage` have
schema-declared day bounds that nothing enforces server-side); say so in the PR so it doesn't read
as accidental inconsistency.

## Step 3 — Fix `SYSTEM_PROMPT`

Targeted rewrite of the intro paragraphs, not a restructure. It should:

- name the tool **categories** including arbitrage and portfolio;
- say all fourteen components exist and that `show_component`'s own description is the authority on
  their props — re-enumerating props in the prompt only guarantees it re-stales;
- state plainly that portfolio tools always read the caller's own holdings, and that the
  unlinked-account case gets reported rather than guessed around.

Spot-check the rest of the prompt for other four-component-era references while in there.

## Step 4 — Re-align `docs/capabilities-matrix.md`

In document order: header stats + `Last Updated`; the Tier-1 mitigation note; the Six Surfaces
table's Sidekick row; the Capability Summary table (MCP count, and the Sidekick row's full 13-name
list — this table is the right home for an exhaustive list, unlike the system prompt); the
per-server tool-count footnote (full rewrite to the verified numbers, dual-registration claim
dropped); the Options Analysis table's false `(both servers)` parentheticals; a **new Domain:
Arbitrage section** placed after Options Analysis; Portfolio & Watchlist expanded from 4 columns to
6 with MCP Tool and Sidekick columns; the Sidekick Chat & BYOK domain's tool and component lists;
footer version `2.1` → `2.2` with a changelog line naming this issue.

Also fix the same false dual-registration claim in [`CLAUDE.md`](../../CLAUDE.md) — a stale number in
the auto-loaded file is the one that propagates furthest.

## Step 5 — Tests

- [`tests/test_chat_protocol.py`](../../tests/test_chat_protocol.py) — three new names in
  `EXPECTED_TOOLS` (14 total). Add a schema-level assertion that `"owner"` is never a property on
  the two portfolio tools, mirroring the component-level `owner` rejection test already in
  [`tests/test_chat_tools.py`](../../tests/test_chat_tools.py).
- [`tests/test_chat_service.py`](../../tests/test_chat_service.py) — extend `ChatServiceTestBase`
  with a `portfolio` mock, then add `TestArbitrageDiscoveryTool` and `TestPortfolioTools` following
  the existing `TestArbitrageTools` patterns. The four cases that matter:
  1. comma-split parsing of `symbols` / `references`;
  2. over-cap rejection **without** calling the service;
  3. `context.owner is None` degrades gracefully **without touching the portfolio mock**;
  4. a model-hallucinated `owner` argument is discarded in favour of the resolved one.
- `tests/test_chat_tools.py` needs no changes — its component-directive coverage already spans all
  four target components.
- **No frontend changes.** No new component is registered, so `componentRegistry.tsx` and its vitest
  suite are untouched.

## Verification

```bash
python -m unittest tests.test_chat_protocol tests.test_chat_service tests.test_chat_tools
```

```bash
python -m unittest discover -s tests -t .
```

Then exercise it rather than trusting the unit tests. Start the API
(`uvicorn api.main:app --host 127.0.0.1 --port 5001`) and in one Sidekick session ask:

1. "find cointegrated pairs among MSTR, COIN, MARA" — should call the new tool **and** render
   `arbitrage_discovery`;
2. "how's my portfolio doing?" — prose totals, not just a card;
3. "how many shares of AAPL do I own and what's my basis?"

Confirm the unlinked-owner path separately: a principal with no identity mapping should get a clear
explanation for the portfolio question while price/technical questions in the same turn still work.

Re-check the finished matrix counts against `grep -c "^@mcp.tool" fastMCPTest/*.py` — **anchored**,
per the gotcha above — rather than against this document.

---

## Checkpoint log

*(Append one entry as each step lands — including what misled you, not just what worked.)*

- **2026-08-14 — plan approved.** Scope set by two explicit decisions from John: the stale
  `SYSTEM_PROMPT` is in scope (not deferred to a follow-up issue), and the two accidental tool gaps
  get **implemented**, not merely recommended. The §5.5 rewrite was added to scope mid-planning.

- **2026-08-14 — Step 1 landed (§5.5).** Rewrote the rule from a prohibition ("never blanket-mirror
  the whole API") into a design gate: most REST capabilities *should* reach a tool, what's forbidden
  is the mechanical one-tool-per-endpoint mirror, and **every new REST capability owes one of two
  artifacts in the same PR** — a designed tool, or a written decision that it stays REST-only.
  Silence stops counting as a decision. §7 item 3 now points at §5.5 instead of restating it in its
  own words, so the two can't drift apart.

- **2026-08-14 — Step 2 landed (three tools).** `discover_arbitrage_pairs`,
  `get_portfolio_summary`, `get_symbol_lots` in `chat_tools.py`; handlers in `chat.py`;
  `portfolio=portfolio` on the `ChatService(...)` call in `registry.py` (no ordering change —
  `portfolio` is already constructed above `chat`).

  Two things worth recording. **First, the owner question resolved as designed but the reasoning
  is the load-bearing part**: `resolve_owner_or_none` sits beside `require_owner` in `api/auth.py`
  and returns `None` where the other 403s, because a chat turn serves many non-owner-scoped tools
  and failing all of them over unprovisioned portfolio identity is a bad trade — while defaulting
  to `"john"` would be a silent cross-user leak the moment a second identity exists. The unlinked
  case degrades to a recoverable per-tool `is_error`, never a whole-turn failure. A model-supplied
  `owner` key is stripped at dispatch regardless.

  **Second, the cap on discovery had to be re-asserted in the handler.** `MAX_DISCOVER_SYMBOLS`
  (25) / `MAX_DISCOVER_REFERENCES` (10) were enforced only in `api/routers/arbitrage.py`. Sidekick
  dispatches **in-process**, so it never passes through that route — the cap would simply not have
  existed for chat. They now live on `ArbitrageService` with both the route and the handler
  asserting them. This is the general hazard, not a one-off: *any* guard implemented at the REST
  boundary is invisible to Sidekick, because Sidekick is not an MCP client and does not speak HTTP.

- **2026-08-14 — Step 3 landed (`SYSTEM_PROMPT`).** This was the likeliest real cause of the
  reported symptom, and the fix is deliberately un-clever: the prompt now names the tool
  *categories* including arbitrage and portfolio, and says `show_component`'s own description is
  the authority on which components exist and what props they take — **it states no count**. The
  previous version hard-coded four component names and went stale the moment a fifth was
  registered; a prompt that points at the registry cannot go stale the same way. That turned out to
  matter within the hour: an intermediate draft of the docs said 14 components when the registry
  holds 15, and the prompt needed no correction because it had no number in it.

- **2026-08-14 — Step 5 landed (tests).** `EXPECTED_TOOLS` in `tests/test_chat_protocol.py` grew to
  14, plus a schema assertion that `"owner"` is never a property on the two portfolio tools.
  `tests/test_chat_service.py` gained `TestArbitrageDiscoveryTool` and `TestPortfolioTools` over a
  new `portfolio` mock on `ChatServiceTestBase`. The four cases that carry weight: comma-split
  parsing; over-cap rejection **without** the service being called; `context.owner is None`
  degrading gracefully **without touching the portfolio mock at all**; and a hallucinated `owner`
  argument being discarded in favour of the resolved one. Full suite green — `OK (skipped=5)`.

- **2026-08-14 — Step 4 landed (`capabilities-matrix.md` + `CLAUDE.md`).** Counts corrected from
  "51 unique / 53 registrations across 5 servers" to **62 across 7**; the dual-registration claim
  deleted; new Domain: Arbitrage section; Portfolio & Watchlist widened 4 → 6 columns; Sidekick
  refreshed to 13 tools / 15 components; document version 2.2.

  Three things misled me here, all caught by checking rather than by writing:

  1. **The plan asserted CLAUDE.md repeated the dual-registration claim. It does not** — that was an
     assumption carried into the plan from the matrix. Had I "fixed" it without looking I'd have
     either edited nothing and claimed a fix, or invented a sentence to correct. CLAUDE.md's actual
     stale number was "5 remote MCP servers" (there are 7), now fixed with the counting command
     written in beside it.
  2. **I invented a REST path from memory** — `GET /api/portfolio/{ticker}/lots` — for the
     `get_symbol_lots` row. The real surface is `GET /api/portfolio/symbols` (positions, and
     `totals` for the summary) and `GET /api/portfolio/lots` (all lots, filtered to the ticker by
     the caller). A plausible-looking path is the easiest thing in a doc like this to get wrong and
     the hardest for a reader to doubt.
  3. **The 29 → 21 drop on stock-price needed a cause, not a guess.** `git log` on
     `fastMCPTest/stock_price_server.py` gives it: `60e4bcd`, "consolidate options tools onto
     options-analysis-server". The footnote cites the commit, so the next person who finds an old
     29 somewhere can resolve it instead of re-deriving it.

  The ⭐ "Built But Not Yet Surfaced" section was **not** re-audited — that is its own piece of
  work. Rows known false were corrected (the cross-symbol fundamentals screeners are closed by the
  `/fundamentals` page, #147 Part D), and the section carries a dated caveat saying the rest was
  last verified 2026-07-20. A stale-but-labelled list beats both a confidently wrong one and an
  unscoped re-audit bolted onto this PR.
