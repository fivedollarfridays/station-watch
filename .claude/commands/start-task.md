---
description: Enter Driver role to work on a task with verification gates
allowed-tools: Bash(bpsai-pair:*), Bash(git:*), Bash(pytest:*), Bash(python:*)
argument-hint: <task-id>
---

Enter **Driver role** to complete task with verification.

**Task ID**: $ARGUMENTS

## Pre-Flight (Enforcement)

```bash
bpsai-pair budget check $ARGUMENTS
bpsai-pair task show $ARGUMENTS
```

If budget warns, inform user and ask to proceed.

## Acceptance Criteria Checklist

Echo back the task's acceptance criteria as a checklist before starting
work, and check each box off (with the evidence that satisfies it) as you
go. Under engage, completion now runs the SAME strict AC gate as an
interactive `task update --status done` — an unchecked box blocks
completion (the task lands `blocked`, not `done`), it does not just log a
warning.

## Conflicts Encountered (REQUIRED at completion)

Every completion MUST declare `conflicts_encountered` — pass it via
`task update --status done --conflicts-encountered <value>`, where the
value is either the literal string `none` or a list of the source
tensions you resolved while working (two authorities disagreeing: stale
doc vs code, CLAUDE.md vs task file, two live configs — name the sources
in tension, what you bound to, and why). The explicit `none` is required,
not optional: an absent field FAILS completion under engage (fail-closed
gate), and the interactive path warns until the arming date
**2026-10-20**, then fails closed too. Measured basis (surfacing-affordance
experiment arms): spontaneous disclosure is 0/320; a mandated structured
field is 46-78/80 — silence is never evidence of no conflicts, so say
`none` when it is true.

If completion is genuinely blocked by the gate (e.g. a legitimate
attestation the gate can't parse), `task update --status done` accepts
the audited escape hatch `--bypass-conflicts-gate` (requires
`--conflicts-gate-bypass-reason`; logged for audit) — see `task update
--help` for the exact flags.

**Under engage, the flag is not your channel.** engage runs `task update
--status done` on your behalf, so a `--conflicts-encountered` flag you
never invoke cannot carry your disclosure. Record it instead in your task
file's frontmatter, under the top-level `runtime:` block, before you
finish:

```yaml
runtime:
  conflicts_encountered: none
```

or, when there were tensions:

```yaml
runtime:
  conflicts_encountered:
    - "Task file specified X; the module's docstring said Y — bound to the task file, it is the newer authority."
```

The completion path reads that field. If it is absent your completion
fails closed exactly as a missing flag does on the interactive path —
nothing is filled in on your behalf, because an absent attestation must
never be recorded as an attested `none`.
<!-- Calibration note: mandating conflict disclosure shifts which
authority a model binds when sources disagree (measured, direction
confirmed). Deliberate for production — conflict-aware binding and
escalation is the wanted behavior — but calibration reads should watch
driver-behavior drift dating from this field's arming. -->

## Execute Workflow

Read and follow `.claude/skills/managing-task-lifecycle/SKILL.md` for the complete workflow.

## Key Constraints

- **ALWAYS** use `--strict` for `ttask done` (enforcement gate)
- **NEVER** mark complete without updating state.md
- **NEVER** use `--force` without explicit user approval
- All acceptance criteria must be checked before completion
- Tests must pass before completion
- If this task's own implementation is handed to a further Agent-tool
  dispatch (a subagent), wrap that dispatch's prompt in the
  dispatch-contract block from `docs/orchestration/dispatch-contract.md`,
  naming this task's own ID

## Task ID Formats

- `T1.1` - Sprint task (for `task` commands)
- `TRELLO-abc` - Trello card (for `ttask` commands)
