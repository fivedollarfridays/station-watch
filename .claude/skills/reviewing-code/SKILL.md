---
name: reviewing-code
description: Use when reviewing code changes, checking PRs, or evaluating code quality.
skills: [reviewing-code]
agent-roles: [nayru, laverna, vaivora]
context: fork
---

# Code Review

## Review Pipeline

The review pipeline dispatches three specialized agents:
- **Nayru** (reviewer): code quality, correctness, best practices
- **Laverna** (security-auditor): security vulnerabilities, SOC2 compliance
- **Vaivora** (vaivora): cross-module contracts, dependency impact (large diffs only, >500 lines or >10 files)

Use the review command to get the proper 3-agent pipeline:
```bash
bpsai-pair review pr <number>
bpsai-pair review branch
```

**IMPORTANT:** Do NOT dispatch generic agents for review. The review command handles agent dispatch with proper mythology names, severity-aware output, and size-scaled Vaivora dispatch. If you are already inside a review command invocation, do not duplicate the dispatch -- the command handles it.

## Landing a PR — the review predicate

Full rule text: `docs/orchestration/review-landing.md`. In short, a code PR merges only when:

1. `bpsai-pair review pr <N>` has run **on the final head**, from the **target repo's** cwd, with
   stdout captured to a file (full finding bodies are stdout-only; `findings.jsonl` is a titles-only
   index).
2. **Every P-level finding is dispositioned** — fixed in the PR, or left with a significant, stated
   reason on the PR. P2s included; no silent tech debt. Record a disposition table in the round
   summary.
3. **Zero unanswered external-reviewer threads on the final head sha**, each answered in-thread with
   the fix commit and evidence, or a reasoned won't-fix. "The reviewer ran" is not the gate.
4. **CI green on that same head.** A push invalidates the predicate — re-evaluate, never carry a
   verdict across a head change.
5. **Verify from a fresh fetch before concluding a commit or history is missing** — `git fetch`, then
   `git merge-base --is-ancestor <sha> <branch>`. A stale worktree/checkout means "not in my `git
   log`" is "not in my stale ref", not "gone"; a false force-push/lost-work report sends people
   chasing nothing, and banking it in agent memory outlives the session that wrote it.

### Reviewer rationing — the seat is a budget

- 🔴 **Never write the external review connector's mention literally** anywhere (comment, commit
  message, issue body, summary). It fires on the literal string in any comment and each occurrence
  spends the account seat. Write **"an explicit review request"** instead.
- **One request per PR, on the final head**, made by the orchestrating session — not per fix round.
- **Driver PRs open as DRAFT**; the orchestrator flips ready **once**, after native-review
  remediation. The seat follows the PR author; toggling does not move the cost.
- Reviewer exhausted or unavailable ⇒ the predicate is **pending**, not waived. Do not retry-loop.

### When to stop

**Stop** at a round with no new P-level findings, or after **two consecutive no-change rounds**.
**Round 4 on one PR is a redesign signal** — fold what is left into the owning epic. If rounds keep
flagging one mechanism from opposite sides, change its shape rather than its parameters.

### CI spend

**Batch fixes per round and push once per round**, never per fix — CI is ~96% of the org bill.
Workflows declare `cancel-in-progress`. **Never `gh run rerun`** to pick up a
base-branch fix (it reuses the original merge ref); push a merge-from-base.

### Who runs the loop

Dispatch the apply/test/reply/summarize loop to a **right-sized driver agent** (seed-03
`dispatch-dont-implement`). The orchestrator keeps deciding dispositions, ruling on contradictory
findings, the single review request, the ready-flip, and the merge.

## Quick Commands

```bash
# Run ALL checks at once (tests + linting)
bpsai-pair ci

# Validate project structure
bpsai-pair validate

# See what changed
git diff main...HEAD --stat
git diff main...HEAD
```

## Review Output Format

```markdown
## Code Review: [Description]

### Summary
Brief assessment.

### P0 (blocks merge -- breaking state, auto-reject)
1. **[File:Line]** - Issue and fix

### P1 (fix before merge -- quality issue, not breaking)
1. **[File:Line]** - Suggestion

### P2 (fix before merge -- lower priority improvement)
1. **[File:Line]** - Optional improvement

### Positive Notes
- What was done well

### Verdict
- [ ] Approve
- [ ] Approve with comments
- [ ] Request changes
```

## Project-Specific Checks

- Type hints on public functions
- Docstrings on public interfaces
- No hardcoded values (use config)
- Tests for new functionality
- Mock external services (Trello, GitHub APIs)
- Follow existing patterns in codebase

## Quick Checks

```bash
# Find debug statements
git diff main...HEAD | grep -E "print\(|breakpoint|pdb"

# Find TODOs in changes
git diff main...HEAD --name-only | xargs grep -n "TODO\|FIXME"

# Check for secrets
git diff main...HEAD | grep -iE "password|secret|api.?key|token"
```
