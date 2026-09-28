---
description: Generate orchestrated execution plan with agent assignments
allowed-tools: Bash(bpsai-pair:*), Bash(cat:*)
argument-hint: <plan-id>
---

Generate an **orchestrated execution plan** for the given plan. This resolves task dependencies, assigns agent models/effort levels, and outputs a phased execution plan.

**Plan ID**: $ARGUMENTS

## Pre-Flight

```bash
bpsai-pair plan show $ARGUMENTS
bpsai-pair workspace status
bpsai-pair feedback status
```

If workspace not initialized, warn and continue with local-only mode.
If feedback has no data, use default model/effort assignments.

## Workflow

### 1. Load Plan Tasks

Load all tasks for the plan and gather their metadata:

```bash
bpsai-pair plan tasks $ARGUMENTS --json
```

### 2. Gather Intelligence

For each task, query the intelligence system:

```bash
bpsai-pair feedback query <task_type>
```

Use feedback data to enrich tasks with:
- **Token estimates** from historical averages
- **Model recommendations** (haiku/sonnet/opus)
- **Effort levels** (low/medium/high)

Falls back to defaults when no data available.

### 3. Resolve Dependencies

Build a dependency graph from task `depends_on` fields.
Topologically sort tasks into parallelizable execution phases.

Use `bpsai_pair.workspace.execution_plan.build_execution_plan()` for resolution.

### 4. Generate Execution Plan

Display the execution plan showing:
- **Ordered phases** with parallel/sequential indicators
- **Task assignments** with model, effort level, and token estimate per task
- **Dependency graph** showing which tasks block others
- **Budget summary** with total estimated tokens across all phases

### 5. Cross-Repo Scope (if workspace configured)

```bash
bpsai-pair workspace status
```

If multi-repo workspace detected:
- Show which repos each task targets
- Suggest cross-repo task ID format (CLI-T35.X, API-T35.X)
- Show agent team assignments per repo

## Output Format

```
Execution Plan: <plan-title>
===============================

Phase 1 (parallel, N task(s)):
  - T35.1: Task title  [model=sonnet, effort=medium, ~5,000 tokens]
  - T35.2: Task title  [model=haiku, effort=low, ~3,000 tokens]

Phase 2 (sequential, 1 task(s)):
  - T35.3: Task title  [model=opus, effort=high, ~12,000 tokens]
    depends on: T35.1, T35.2

Total: N tasks, M phases, ~X tokens
```

## Key Constraints

- **Display only** — does not spawn agents or execute tasks
- Keep execution_plan.py under 200 lines
- Graceful fallback when workspace/feedback unavailable
- Tasks with missing dependencies are treated as immediately schedulable
