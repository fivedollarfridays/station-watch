---
description: Enter Navigator role to create plan from backlog or description
allowed-tools: Bash(bpsai-pair:*), Bash(cat:*), Read, Write
argument-hint: [backlog-file.md] or [feature description]
---

# Navigator Role — Planning Workflow

You are now in **Navigator role**. Your job is to create a bulletproof plan
with proper budget validation. Dispatch `explore` and `planner` agents as
necessary.

## Pre-Flight (Enforcement)

```bash
bpsai-pair budget status
bpsai-pair pm status
```

If budget is above 80% daily usage, warn the user before proceeding.

## Input Processing

The argument `$ARGUMENTS` can be:
1. A backlog file path (e.g., `backlog-sprint-28.md`)
2. A feature description (e.g., `"Add webhook support for notifications"`)

**If backlog file**: Read `.paircoder/context/$ARGUMENTS` or `.paircoder/docs/$ARGUMENTS`.
**If description**: Use the description directly to design the plan.

## Planning Workflow

### Step 1: Context Gathering

Read the planning skill and the current project state:

```bash
cat .claude/skills/planning-with-pm/SKILL.md
bpsai-pair status
cat .paircoder/context/state.md
```

`.claude/skills/planning-with-pm/SKILL.md` — its provider-detection tree
handles PM providers, Trello compat mode, and local-only planning.

### Step 2: Design the Plan

Based on the input, determine:
- **Plan slug**: kebab-case identifier (e.g., `webhook-support`)
- **Plan type**: `feature` | `bugfix` | `refactor` | `chore` (NOT `maintenance`)
- **Plan title**: Human-readable title
- **Task breakdown**: 3–8 tasks with complexity estimates

### Step 3: Budget Check (the plan gate)

The plan gate sums the planned task complexity against the sprint budget
BEFORE the plan record is written (`enforcement.require_budget_check`). Check
each planned task against budget as you design it:

```bash
bpsai-pair budget check <task-id>
```

**If the plan gate refuses, DO NOT proceed without user acknowledgment.**
Either reduce scope, split into multiple plans, or get the user's explicit
approval (an audited `--reason` bypass) before writing the plan.

### Step 4: Create Plan and Tasks

```bash
# Create the plan (gate runs before the record is written)
bpsai-pair plan new <slug> --type <type> --title "<title>"

# Add tasks with proper complexity and priority
bpsai-pair plan add-task <slug> \
    --id "T<sprint>.<seq>" \
    --title "<task title>" \
    --complexity <0-100> \
    --priority <P0|P1|P2|P3>
```

**Task ID Format**: Use `T<sprint>.<sequence>` format (e.g., T28.1, T28.2).
Task file content must be written directly — `plan add-task` only accepts
metadata. Every task gets a `model:` from
`bpsai-pair calibration recommend-model` (see the planning skill's task
template).

### Step 5: Update State

```bash
bpsai-pair context-sync \
    --last "Created plan: <plan-id>" \
    --next "Ready to start: <first-task-id>"
```

### Step 6: Report Summary

Provide a summary to the user:

```
**Plan Created**: <plan-id>
**Type**: <type>
**Tasks**: <count> tasks, <total-complexity> complexity points

Ready to start? Use `/start-task T28.1`
```

## Error Handling

- If the plan gate refuses, DO NOT proceed without user acknowledgment.
- If plan creation fails, check for duplicate slugs or invalid types.

## Key Constraints

- Plan types: `feature` | `bugfix` | `refactor` | `chore` (NOT `maintenance`)
- Task IDs: `T<sprint>.<seq>` format (e.g., T28.1)
- Always update state.md after planning.
- Set your project defaults in `.paircoder/config.yaml`.
