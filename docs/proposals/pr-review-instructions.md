# Automated PR Review Instructions

These instructions define the review performed by the `pr-review` cron job for `JohnFunkCode/StockPortfolioManager`.

## Scope

- Review open pull requests created or updated within the last four hours.
- Review at most the five most recent qualifying pull requests.
- Do not invent findings. If the implementation looks good, say so.

## Gather PR context

For each qualifying pull request, gather:

- PR number, title, author, description, head commit SHA, and changed files.
- The complete PR diff.
- Existing reviews and comments, so the same head commit is not reviewed twice by this job.

## Associated issue and objective evaluation

Determine whether the PR has an associated issue:

1. Check GitHub's `closingIssuesReferences`.
2. Inspect the PR title and body for references such as `#123`, `owner/repo#123`, `fixes`, `closes`, or `resolves`.
3. Only treat an issue in `JohnFunkCode/StockPortfolioManager` as associated. If a reference is ambiguous, report it as ambiguous instead of guessing.

When an associated issue exists:

- Read the issue's title, body, state, labels, and comments.
- Extract its objectives, acceptance criteria, constraints, and explicitly requested behavior.
- Evaluate each objective against the PR diff, changed files, tests, and verification results.
- Classify each objective as **Fully met**, **Partially met**, **Unmet**, or **Unverifiable**.
- Do not treat an implementation plan as an acceptance criterion unless the issue explicitly makes it one.

## Review checklist

Check for:

- Bugs, logic errors, and edge cases.
- Security issues, including injection, auth bypass, secrets, SSRF, and unsafe data handling.
- Performance problems, including N+1 queries, unbounded loops, and memory leaks.
- Missing error handling, regressions, dead code, and actionable code-quality issues.
- Overly complex changed code, using the Cyclomatic Complexity and Cognitive Complexity goals below.
- Missing or insufficient tests, including edge cases and behavior required by the associated issue.
- Relevant automated checks and test results. Clearly identify checks that could not run and why.

## Complexity goals

Apply these goals to every new or materially changed function, method, or other executable unit.
Measure the complete unit after the PR is applied, not only the added lines. Use the repository's
configured analyzer when one exists; otherwise use a consistently versioned analyzer available in
the review environment. Record the analyzer, version, and metric values in the review.

| Metric | Goal | Warning | Request changes |
|---|---:|---:|---:|
| Cyclomatic Complexity | `<= 10` | `11–15` | `> 15` |
| Cognitive Complexity | `<= 15` | `16–25` | `> 25` |

Reviewers should apply the following measurable rules:

- A changed unit above a **Request changes** threshold must be split, simplified, or have an
  explicit, technically supported exception in the PR description. A vague statement that the
  code is “necessarily complex” is not sufficient.
- A changed unit in a **Warning** band may be accepted only when the PR explains why refactoring
  is disproportionate and includes tests for its meaningful branches and failure paths. Otherwise,
  request simplification.
- A refactor that preserves behavior must not increase either metric beyond the applicable band
  without the same explanation and tests. Prefer a reduction in at least one metric when the PR's
  stated purpose is to simplify code.
- Do not use an aggregate file or repository average to hide a high-complexity function. Report the
  exact function/method, location, and both metric values. Review generated code, vendored code,
  and declarative configuration only when the analyzer includes them and the PR changes them.
- If the metric cannot be measured, mark the complexity check **Unverifiable**, state why, and do
  not claim that the complexity goals were met. A missing analyzer is a verification gap, not a
  zero score.

For each warning or exception, require at least one concrete simplification consideration in the
review (for example: guard clauses, extracting a cohesive helper, replacing nested conditionals
with a lookup or strategy, or separating validation from side effects). Complexity findings must
remain actionable: include the exact location, measured values, why they matter, and the smallest
reasonable refactoring or test change.

## Test quality and significance

Review tests for meaningful behavioral coverage, not merely increased line or branch coverage:

- Identify the behavior, risk, bug, or issue objective each new or changed test is intended to protect.
- Confirm that assertions would fail if the relevant production behavior regressed; flag tests that only execute code without checking meaningful outcomes.
- Check that tests exercise important success, failure, boundary, authorization, persistence, integration, and user-visible paths where applicable.
- Check that fixtures, mocks, and snapshots do not make the test pass while bypassing the behavior under test. Flag over-mocking, tautological assertions, assertions on implementation details, and tests that simply reproduce the implementation logic.
- Evaluate whether the test cases are representative of real inputs and failure modes, especially those described by the associated issue.
- Look for missing negative cases, edge cases, interaction cases, and regression cases that could allow the reported bug to return.
- Treat coverage percentages as supporting evidence only, never as proof that the tests matter or that the change is adequately tested.
- Report tests that are flaky, order-dependent, excessively broad, or otherwise unable to provide reliable protection.

For the review report, summarize whether the tests provide meaningful protection for the PR's behavior and issue objectives, and distinguish genuine test gaps from mere coverage-percentage gaps.

## Verdict

- **APPROVE**: No blocking issues and associated issue objectives are met or there is no associated issue.
- **REQUEST_CHANGES**: Critical or warning-level findings, or unmet associated-issue objectives.
- **COMMENT**: Non-blocking observations, partially met objectives, or material uncertainty.

## Save the review to GitHub

After completing each review, save it on the PR being reviewed:

- Use a formal GitHub review when permitted.
- If the authenticated user is the PR author and GitHub rejects a formal approval or change request, post the same review as a top-level PR comment.
- Use the heading `## Hermes Agent PR Review`.
- Include the reviewed head SHA, verdict, code findings, issue-objective assessment, and verification results.
- Before posting, check existing reviews/comments and skip posting if this job already posted a review for the same PR head commit.
- Verify the GitHub command/API call succeeded and include the resulting review or comment URL in the report.

## Report format

```text
## PR Reviews — today

### JohnFunkCode/StockPortfolioManager #[number]: [title]
**Author:** [name] | **Verdict:** APPROVE/REQUEST_CHANGES/COMMENT
**Reviewed head:** [SHA]
**Associated issue:** #[number] — [title], or `None found`
**Issue objective assessment:**
- **Fully met:** [objectives, if any]
- **Partially met:** [objectives, if any]
- **Unmet:** [objectives, if any]
- **Unverifiable:** [objectives, if any]
**Saved to GitHub:** [URL]

For each code finding:
- **File:Line** — exact location
- **Severity** — Critical / Warning / Suggestion
- **What's wrong** — one sentence
- **Fix** — how to fix it

Include verification results and clearly note checks that could not run.

For complexity findings, include:

- **Unit:** function or method and exact file/line location
- **Metrics:** Cyclomatic Complexity and Cognitive Complexity values
- **Analyzer:** tool and version
- **Severity:** Warning / Suggestion, or the review verdict's applicable blocking level
- **Action:** refactor, add branch/failure-path tests, or document a specific exception
```

If there are no qualifying pull requests, respond exactly:

```text
No new PRs to review.
```
