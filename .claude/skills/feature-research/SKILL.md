---
name: feature-research
description: Research a GitHub feature issue for evidence, feasibility, and user value; comment only when the findings materially improve the issue. Use when asked to investigate or evaluate a proposed GitHub feature, including financial product features. Do not use for implementation or routine issue triage.
argument-hint: <issue number, URL, or owner/repo#number>
---

# Feature research (Claude Code)

This is the Claude Code entry point for the team's feature research skill. The procedure has one
home, shared with Codex, so the two cannot drift:

1. Read [`.agents/skills/feature-research/SKILL.md`](../../../.agents/skills/feature-research/SKILL.md)
   and follow it exactly — scope, the stop-early rules, the single permitted write, and the output
   format all come from there.
2. Read [the research methodology](../../../.agents/skills/feature-research/references/research-methodology.md)
   when that file tells you to.

Issue to research: $ARGUMENTS

If that is empty, ask which issue to research, as the shared skill requires.

## Claude Code specifics

These cover only how to carry out the shared procedure with Claude Code's tools; they add no rules.

- **Reading the issue:** `gh issue view <n> --repo <owner/repo> --comments`. Resolve the repository
  from the argument, else from `git remote get-url origin`. If `gh` fails in the sandbox with
  `x509: OSStatus -26276`, that is the macOS sandbox blocking certificate verification, not an
  auth problem — rerun that single call outside the sandbox.
- **Live sources:** use `WebSearch` to find primary sources and `WebFetch` to open each one and
  confirm the cited section says what you claim. If neither is available, say so and post nothing
  that depends on live sources.
- **Product presentation** (when the shared skill asks you to inspect a product and a peer): open
  each in the built-in browser (`mcp__Claude_Browser__*`) and use `read_page`/`get_page_text` for
  placement and attribution text, and a screenshot only for layout, preview, or expansion behavior
  that text can't show. Describe what you saw in the comment, with the page URL and the date you
  looked; don't paste screenshots or copied content into the issue. Don't sign in, accept terms, or
  change anything on a site to reach a page. If a page needs any of that, say it wasn't checked.
- **Repository context:** use the project's docs map (`readme.md` → "Documentation map",
  `CLAUDE.md`, `docs/architecture/`, `docs/proposals/`) to check whether the feature, or a
  constraint on it, is already documented. The MCP data tools may help check a premise against
  real data; they are evidence, not a substitute for a source.
- **Posting:** re-read the comments immediately before posting, write the comment to a file in the
  scratchpad, then `gh issue comment <n> --repo <owner/repo> --body-file <file>`. Report the URL
  `gh` prints.
- **No other writes:** no branch, commit, PR, label, or edit — even though this repo's usual
  workflow commits and opens PRs on its own.
