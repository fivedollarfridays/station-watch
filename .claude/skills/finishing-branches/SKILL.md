---
name: finishing-branches
description: Use when work is complete and ready for integration, merge, or PR creation.
skills: [finishing-branches]
agent-roles: [nayru]
---

# Finish Branch

## Pre-Merge Checklist

### 1. Run All Checks

```bash
bpsai-pair ci                 # Tests + linting in one command
bpsai-pair validate           # Check project structure
```

`ci` runs tests and linting only. The architecture gates are enforced
separately in GitHub Actions, so run them too or the merge check fails:

```bash
bpsai-pair arch check-wiring <package-dir> --strict
bpsai-pair arch check-provenance <package-dir> --strict
bpsai-pair arch check-config-readers <package-dir> --strict
```

### 2. Security Scan

```bash
bpsai-pair security scan-secrets --staged   # Check for leaked secrets
```

### 3. Review Changes

```bash
git diff main...HEAD --stat
git diff main...HEAD | grep -E "print\(|breakpoint|TODO|FIXME"
```

### 4. Update Task Status

Follow managing-task-lifecycle skill for two-step completion.

### 5. Create PR

```bash
bpsai-pair github auto-pr     # Auto-creates PR from branch, detects TASK-xxx
```

The `engage.pr.draft_until_gate` / audited `--no-draft` opt-out policy
described below applies to PRs `bpsai-pair engage` opens for you (it flips
each one ready autonomously during its own finalize, after a clean
security verdict). `bpsai-pair github auto-pr` is a SEPARATE command with
its own `--draft`/`--no-draft` boolean -- it does NOT read
`engage.pr.draft_until_gate` and its `--no-draft` writes no
`bypass_log.jsonl` receipt. If you open the PR by hand with `auto-pr`
instead of via `engage`, mark it ready yourself once CI is green
(`bpsai-pair review checks-gate` -- pass `--flip-ready`-equivalent behavior
is built into that command already) rather than assuming the engage
draft-policy applies.

## PR Template

```markdown
## Summary
Brief description.

## Changes
- Added X
- Modified Y
- Fixed Z

## Testing
- [ ] Unit tests added/updated
- [ ] All tests passing
- [ ] Manual testing completed

## Checklist
- [ ] No debug statements
- [ ] Documentation updated
- [ ] Task status updated
```

## Post-Merge

```bash
git checkout main
git pull origin main
git branch -d <feature-branch>
```

## Quick Finish

```bash
pytest && ruff check . && git add -A && git commit -m "[TASK-XXX] Description" && git push
```
