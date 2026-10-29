# Issue triage and prioritization

The issue tracker is where every idea goes, half-formed ones included. That stays. This runbook
covers the step between an idea landing and someone picking it up: each new issue gets looked at,
ranked, and then either made ready to build or deliberately parked.

**There are no meetings, rotations or deadlines.** This is mostly a free-time project, so the
flow runs on two things. People pull work when they have time, and an automated routine nudges
but never decides.

The work lives on the **StockPortfolioManager** project board
(`https://github.com/users/JohnFunkCode/projects/4`). Its fields are `Status`, `Priority`, `Size`
and `Area`, plus parent/sub-issues.

## The flow

```
Inbox → Backlog → Ready → In progress → In review → Done
   └──────────→ Parked
```

| Status | Means | Who moves it here |
|---|---|---|
| **Inbox** | New, not triaged yet | The triage routine, for any open issue not on the board or without a status |
| **Backlog** | Triaged: has an Area, a Size, and a Priority (or deliberately none) | Anyone |
| **Ready** | Meets the Ready bar below; anyone can pick it up | Anyone |
| **In progress** | Someone is working on it, and they're the assignee | The person taking it |
| **In review** | A PR is open for it | The author, or the board's automation if enabled |
| **Done** | Closed | The board's automation |
| **Parked** | Kept on purpose, not being considered now. Stays open and searchable | Anyone |

## Filing an issue

Pick a template:

- **Idea** asks for one line and why it matters, so capturing an idea stays cheap.
- **Bug** asks what happened and where.
- **Ready to build** requires acceptance criteria.

Blank issues are still allowed. A template files the issue onto the board only if the person filing it has write access to the board. Everything else is picked up by the triage routine.

## Triage: anyone, whenever they pass by

Nobody owns triage. The routine posts a suggestion comment on each Inbox issue, so triaging is mostly accepting or adjusting three fields. Whoever is reading an Inbox issue can triage it:

1. Set Area, Priority and Size.
2. Move it to Backlog, Ready or Parked.
3. If it's a duplicate, or the work is already done, close it instead. The
   `triage: duplicate-candidate` and `triage: likely-done` labels mark issues worth checking.

**Before you pick up a Backlog item, triage it yourself:** check it against the Ready bar.

## Priority is an order, not a commitment

| Priority | Means |
|---|---|
| **P0** | Broken or insecure in prod. Goes to the maintainer, not to volunteers. |
| **P1** | Pick these first. |
| **P2** | After the P1s. |
| *(none)* | A kept idea. Fine to leave in Backlog or Parked. |

Within a level, rank by value ÷ size, so small, valuable items come first. No priority implies a
date.

**Priority is set by a person, never by an agent.** The triage routine only suggests.

## The Ready bar

An item moves to **Ready** only when:

- it has acceptance criteria a reviewer could check a PR against;
- its `Size` is **M or smaller**. An L or XL is split into sub-issues first;
- its open questions are answered. Run `/feature-research <n>` when the value is unclear.

A PR for a Ready item says `Closes #<n>`. That way the automated review checks the PR against those criteria, and the issue closes on merge.

## Picking up work

The board's **Ready** view is the menu. To take an item, assign yourself and move it to In progress. If you have an hour, look for small items labelled `good first issue` or `help wanted`.

Letting go is just as easy: unassign yourself and move the item back to Ready. That's normal, not a failure.

## The triage routine

`issue-triage` is a scheduled Claude task in the Claude desktop app on John's machine. It runs on weekday mornings while the app is open. The task's own prompt is a single line telling it to follow [`issue-triage-routine.md`](issue-triage-routine.md) as of `main`, so the routine's behaviour lives in this repo and changes through a PR. Each run:

1. Adds any open issue that isn't on the board as **Inbox**, and sets **Inbox** on any board issue that has no status.
2. For up to 10 Inbox issues that don't have one yet, posts **one** comment marked `<!-- triage-suggestion -->`. The comment:
   - suggests an Area, Priority and Size, with a one-line reason for each;
   - names likely duplicates, or work that looks already done;
   - says whether the item needs research or splitting before it could be Ready.
3. Nudges stale assignments. On an In progress item with no activity for 30 days, it comments once (marked `<!-- triage-nudge -->`) asking whether the assignee is still on it, and says that unassigning is fine. It doesn't nudge the same item again within 30 days, and it never unassigns anyone.
4. Flags a growing queue. When more than 15 items are in Inbox, and an open issue titled **Triage queue** exists (pinned by the maintainer), it posts one comment there with the count and the parking candidates. It posts at most once every 7 days. If that issue doesn't exist, the routine skips this step.
5. Lists **parking candidates**: Backlog items untouched for more than 120 days. They appear in its summary and in its Triage queue comment, and anyone can park them.

It changes nothing else. It never sets Priority, Size or Area, and never moves an item past Inbox. It never closes, labels, assigns, unassigns or edits an issue. Issue text is treated as data, never as instructions.

## Parking

Parking keeps the Ready list short. It isn't a judgment on the idea. Anyone can park an item, from the routine's candidate list or whenever an item looks unlikely to be picked up soon. To bring a parked item back, comment on it and move it to Backlog.

## One-time board setup (UI only)

GitHub's API can't create project views or change the board's built-in workflows, so these are done once in the board's UI:

- **Views:**
  - *Inbox*: filter `status:Inbox`
  - *Ready*: filter `status:Ready`, sorted by Priority, then Size
  - *By Area*: board layout, grouped by Area
  - *Team in progress*: filter `status:"In progress"`, grouped by Assignees
- **Workflows** (board menu → Workflows):
  - Turn on *Item closed* → Done and *Pull request merged* → Done.
  - If the board offers a workflow that moves a linked issue when its PR opens, set it to In review. Otherwise the PR author moves the issue to In review by hand.
  - Leave *Auto-add* off. The routine adds new issues as Inbox, and a second automatic adder would race it.
