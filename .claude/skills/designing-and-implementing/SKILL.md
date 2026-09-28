---
name: designing-and-implementing
description: Use when receiving feature requests, architectural discussions, or multi-step implementation needs that require design before coding.
skills: [designing-and-implementing]
agent-roles: [navigator, driver]
---

# Design → Plan → Implement

## When to Use This Skill

Check if planning is needed:
```bash
bpsai-pair intent should-plan "user's request here"
```

Use this skill for: features, refactors, multistep work.
Skip planning for: typo fixes, small bugs, documentation tweaks.

## Workflow

### 1. Clarify Requirements
- Restate the goal in 1–3 sentences
- Identify affected components
- Ask clarifying questions if ambiguous
- Research existing code patterns

### 2. Propose Approaches
Present 2–3 options with pros/cons and recommend one.

### 3. Create Plan

```bash
bpsai-pair plan new <slug> --type feature --title "Title"
```

### 4. Add Tasks

Resolve the per-task model before writing the file (MR3.2 — single-source
doctrine, calibration-aware):
```bash
bpsai-pair calibration recommend-model --task-type <type> --complexity <n> [--cross-module]
```

Task format in `.paircoder/tasks/` (note: `model:` is declared in the
backlog/lane file, not here):
```yaml
---
id: TASK-XXX
title: Task title
status: pending
priority: P0  # P0=must, P1=should, P2=nice
complexity: 30  # 10-100 scale
---

## Objective
- What this accomplishes.

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Tests pass

## Dependencies
- Requires TASK-YYY (if any)
```

### 5. Sync to the PM Board (only if a provider is configured)

Check first — `pm.provider` defaults to `none`, and on a local-only project
this step exits 1 rather than no-opping:

```bash
bpsai-pair pm status
```

- **A PM provider is configured** — sync the plan:
  ```bash
  bpsai-pair plan sync-pm <plan-id> --target-list "Planned/Ready"
  ```
  (`sync-trello` is a deprecated alias)
- **`pm.provider: none`** — **skip this step.** Tasks live as local files;
  there is no board to sync to.

See `planning-with-pm/SKILL.md` for the full provider-detection tree.

### 6. Implement Each Task

1. `bpsai-pair task update TASK-XXX --status in_progress`
2. Write tests first (see implementing-with-tdd skill)
3. Implement feature
4. Complete via `/start-task` (the managing-task-lifecycle skill is slash-only —
   invoking it by name will not fire)

**Dispatching implementation to the Agent tool:** if step 3 hands the task
to a subagent (the Agent tool) rather than implementing inline, wrap the
dispatch prompt in the dispatch-contract block documented in
`docs/orchestration/dispatch-contract.md`:

```
--- dispatch-contract ---
task: TASK-XXX
repo: <target>
verification: <gate battery>
--- end ---
```

`dispatch_contract_gate.py` (PreToolUse, matcher `Agent`) flags
implementation-shaped dispatches missing this block — warn-only today, so
a missing block is advisory, not blocking, but the block is the standard
shape every dispatch from this step should carry.

## Key Files

- Plans: `.paircoder/plans/`
- Tasks: `.paircoder/tasks/`
- State: `.paircoder/context/state.md`
- Project context: `.paircoder/context/project.md`

## Commands

```bash
bpsai-pair plan list              # List plans
bpsai-pair plan show <id>         # Show plan details
bpsai-pair task list --plan <id>  # Tasks in plan
bpsai-pair task next              # Next task to work on
```
