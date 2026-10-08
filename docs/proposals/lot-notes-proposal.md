# Proposal: Capturing the "Why" Behind Every Trade (Issue #266)

**Status:** Implemented in the PR for #266 (decisions below were agreed before building)
**Issue:** [#266 — Add an optional comment to a lot when it is opened or closed](https://github.com/JohnFunkCode/StockPortfolioManager/issues/266)
**Author:** John Funk
**Date:** 2026-10-08

## 1. Problem

The portfolio records *what* was bought and sold, and when, but never *why*. When we look back at a trade we're relying on memory, and that's unreliable. The reasoning written at decision time is what we need to judge decision quality later:
- Did the thesis play out?
- Do early exits share a pattern?
- Which kinds of reasoning lead to good outcomes?

The long-term goal is a **trade-decision analyzer**. It will compare *why we bought it when we did* and *why we sold it when we did* with what actually happened. This proposal captures the raw data that analyzer needs.

## 2. Proposal

Allow an optional free-text comment when a lot is:

- **Opened** (Add Lot): "Reason for purchase"
- **Closed** (Sell / Close Lot): "Reason for sale", with a separate note for each lot the sale touches

Comments can be edited afterwards. They are shown in the UI and exposed over REST, to the Sidekick (`get_symbol_lots`) and to MCP clients (`get_symbol_lots`, `get_symbol_sales`), so that the analysis tools can reason about them.

### Good news: no schema change
Both `positions.notes` and `lot_sales.notes` already exist in the database, and the repository can already write both. This is mostly wiring through the service, API and UI layers. No migration is needed.

## 3. Design decisions

| Decision | Choice | Rationale |
|---|---|---|
| Format | **Free text** | Simple to capture. A future analyzer can pull structure out of the text. |
| Required or optional? | **Optional, no nudging** | Don't add friction to entering a trade. |
| Maximum length | **5000 characters** | Postgres `TEXT` has no practical limit. The cap only catches accidental pastes and leaves room for a full thesis. |
| Sales spanning several lots | **One note per lot**, with a "Same note for all lots" shortcut that is on by default | Each lot needs its own record of why it was bought and sold when it was, so the analyzer can study timing. |
| Partial close | The remaining child lot **keeps the original purchase note** | The purchase reason still applies to the shares that are left. |
| Editing | **Notes are editable** | Lets users fix and expand their reasoning. No version history is kept. |
| Backfill | **No dedicated bulk tool** | Editing covers it: users can add notes to existing lots one at a time. |
| Privacy | **Owner-scoped** | Notes follow the same owner isolation as lots: SQL-level `WHERE owner = …`, and the owner comes from the authenticated principal. |
| Agent and Sidekick exposure | Notes in the MCP `get_symbol_lots` and the new `get_symbol_sales`, and in the Sidekick's `get_symbol_lots` (which also returns `sales`), **truncated to 500 characters** with a `notes_truncated` flag | Keeps the model's context small. The full text stays available over REST. |
| UI display of sale notes | **Deferred to #235** (realized P/L view) | The portfolio page shows only open lots. Closed-lot history belongs in the realized-P/L view. |

## 4. Scope

### Backend
- `CloseLotRequest` gets `notes` (the default for every lot touched) and `lot_notes` (per-lot overrides keyed by `lot_id`).
- `PortfolioService.close_lot` passes both to the repository. It rejects `lot_notes` keys that aren't part of the sale.
- **New** `POST /api/portfolio/lots/{id}/close/preview` returns which lots a sale would touch, without writing anything. The Close dialog uses it to show one note field per lot.
- **New** `GET /api/portfolio/sales?symbol=` is the first read path for `lot_sales`. It returns sale history with notes.
- **New** `PATCH /api/portfolio/sales/{sale_id}` edits a sale note, scoped to the owner. The UI for it comes with #235.
- Purchase notes are edited through the existing `PATCH /api/portfolio/lots/{id}`.

### Frontend
- **Add Lot dialog:** a multi-line "Reason for purchase" field with a character counter.
- **Close Lot dialog:**
  - "Reason for sale" as a single field.
  - When the sale spans more than one lot, a "Same note for all lots" toggle. Turning it off shows one field per lot.
- **Lot row:**
  - A note icon with a hover tooltip that shows the full text.
  - An "Edit note" action that adds, changes or clears the note. This also covers backfilling existing lots.

### MCP and Sidekick
- MCP `get_symbol_lots` already returned each open lot's `notes`; they are now truncated as described in section 3.
- **New** MCP tool `get_symbol_sales(ticker)` returns the symbol's sales with their notes (one call to `GET /api/portfolio/sales`).
- The Sidekick's in-process `get_symbol_lots` returns `lots[].notes` and a `sales` list, read directly from the service.

### Tests
- Repository, service and API tests, covering the note on each sale row, per-lot overrides, editing sale notes, the 5001-character rejection and owner scoping. Owner scoping includes showing that one owner can't read or edit another owner's sale notes.
- Vitest tests for both dialogs and the lot row.

## 5. Out of scope
- UI for viewing and editing sale notes (#235).
- **The trade-decision analyzer.** It's a future project that builds on this data.
- Structured thesis fields.
- Version history of note edits.
- A bulk backfill tool.
- Searching across notes.

## 6. Resolved questions
1. **Free text or structured?** Free text.
2. **Per-lot sale notes?** Yes. Record why each lot was bought when it was, and why it was sold when it was, to feed a future analyzer.
3. **Editable after the fact?** Yes. Notes stay editable.
4. **Backfill?** No dedicated tool. Users can add notes to existing lots through editing.
5. **Privacy?** Yes, notes are owner-scoped.

## 7. Risks
- **Low adoption.** Optional fields often go unused, and an analyzer with sparse notes isn't much use. We'll measure what percentage of new lots and sales have notes after 30 days.
- **Hindsight edits.** Because notes are editable, some may be rewritten after the outcome is known. This is accepted for v1. If the analyzer needs it later, we can add an edit history (a `notes_updated_at` column or an audit table).
- **Agent token cost.** Mitigated by truncating notes in MCP output.

## 8. Effort estimate
Roughly 1.5–2 days: about three quarters of a day on the backend (including sale-note editing), about a day on the frontend, plus tests. There's no migration and no deploy-order dependency.

## 9. Next steps
1. Update issue #266 with the final decisions.
2. Implement on branch `issue-266-lot-notes`.

## 10. Implementation notes
Things that were not obvious going in, recorded so nobody has to rediscover them.

- **The sale-notes tool is separate, not folded into `get_symbol_lots`.** `tests/test_mcp_tool_contracts.py` pins that every MCP tool makes exactly one REST call (Rule 6), so `get_symbol_lots` could not also fetch `/api/portfolio/sales`. Sale notes therefore ship as the new `get_symbol_sales` tool (63 MCP tools, up from 62). The Sidekick is in-process and has no such limit, so its `get_symbol_lots` returns both.
- **A FIFO/LIFO/HIFO sale is not limited to the lot you clicked.** `PortfolioService.close_lot` resolves the allocation across *every* open lot of the symbol, so selling 5 shares "from" lot #2 can draw on an older lot. That is why the Close dialog asks the preview endpoint on every share-count change rather than only when the count exceeds the clicked lot's quantity.
- **`lot_sales` has no `owner` column.** Every read and write on it is scoped by joining through the sale's lot in `positions`. Another owner's `sale_id` simply matches nothing, so it surfaces as a plain 404 and existence is never leaked.
- **Blank notes are stored as NULL**, and a per-lot override that is blank falls back to the sale's default note rather than clearing it.
- **Partial closes:** the remainder (child) lot keeps the *purchase* note; the sold portion's reason lives on its `lot_sales` row.
- **No migration.** Both `notes` columns already existed in `_SCHEMA` and `db/schema_snapshot.json`.
