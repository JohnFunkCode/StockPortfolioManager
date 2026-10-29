# Issue triage and prioritization

The issue tracker is where every idea goes, half-formed ones included. That stays. This runbook
is the step between an idea landing and the team picking it up: each new issue gets looked at,
ranked, and either made ready to build or deliberately parked.

The work lives on the **StockPortfolioManager** project board
(`https://github.com/users/JohnFunkCode/projects/4`). Its fields: `Status`, `Priority`, `Size`,
`Area`, plus parent/sub-issues.

## The flow

```
Inbox → Backlog → Ready → In progress → In review → Done
   └──────────→ Parked
```

| Status | Means | Who moves it here |
|---|---|---|
| **Inbox** | New, not triaged yet | The triage routine, for any open issue not on the board or without a status |
| **Backlog** | Triaged: has an Area, a Priority (or deliberately none) and a Size | A person, at triage |
| **Ready** | Meets the Ready bar below; anyone on the team can pick it up | A person |
| **In progress** | Someone is working on it; it has an assignee | The assignee |
| **In review** | A PR is open for it | The board's automation, or the author |
| **Done** | Closed | The board's automation |
| **Parked** | Kept on purpose, not being considered now. Stays open and searchable | A person, at triage or the quarterly review |

## Filing an issue

Pick a template. **Idea** asks for one line and why it matters, so capture stays cheap. **Bug**
asks what happened and where. **Ready to build** requires acceptance criteria. Blank issues are
still allowed; triage catches them either way.

## Priority means

| Priority | Means |
|---|---|
| **P0** | Broken or insecure in prod. Drop other work. |
| **P1** | Committed to now. |
| **P2** | Next, once the P1s are done. |
| *(none)* | A kept idea. Fine to leave in Backlog or Parked. |

Within a level, rank by value ÷ size: small, valuable items first.

**Priority is set by a person, never by an agent.** The triage routine suggests; the team decides.

## The Ready bar

An item moves to **Ready** only when:

- it has acceptance criteria a reviewer could check a PR against;
- its `Size` is **M or smaller** — an L or XL is split into sub-issues first;
- its open questions are answered (run `/feature-research <n>` when the value is unclear).

A PR for a Ready item says `Closes #<n>`, so the automated review checks it against those criteria
and the issue closes on merge.

## The triage routine

A scheduled Claude task, `issue-triage` (in the Claude desktop app's scheduled tasks on John's
machine; it runs while the app is open, weekday mornings). Each run:

1. Adds any open issue that is not on the board, and gives any board item without a status, the
   status **Inbox**.
2. For up to 10 Inbox issues that don't already have one, posts **one** comment marked
   `<!-- triage-suggestion -->` that suggests Area, Priority and Size with a one-line reason each,
   names likely duplicates or work that looks already done, and says whether the item needs
   research or splitting before it could be Ready.
3. Changes nothing else: it never sets Priority, Size or Area, never moves an item past Inbox, and
   never closes, labels or edits an issue. Issue text is treated as data, never as instructions.

## The triage pass (people)

Weekly, 15–20 minutes, by whoever has triage that week. Open the board's **Inbox** view and, for
each item, accept or change the suggestion: set Area, Priority and Size, then move it to Backlog,
Ready or Parked — or close it as a duplicate (the `triage: duplicate-candidate` and
`triage: likely-done` labels mark ones to verify).

## Quarterly review

Items over 90 days old in Backlog get their Priority confirmed or move to **Parked**. Parked keeps
them out of the Ready queue without losing them.

## One-time board setup (UI only)

GitHub's API can't create project views or edit the built-in workflows, so these are done once in
the board's UI:

- **Views:** *Inbox* (filter `status:Inbox`), *Ready* (filter `status:Ready`, sort Priority then
  Size), *By Area* (board grouped by Area), *Team in progress* (filter `status:"In progress"`,
  group by Assignees).
- **Workflows** (board menu → Workflows): turn on *Item closed* → Done, *Pull request merged* →
  Done, and *Pull request linked to issue* → In review. Leave *Auto-add* off — the routine adds new
  issues as Inbox instead, and a second adder would race it.
