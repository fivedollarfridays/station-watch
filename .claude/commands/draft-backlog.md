---
description: Draft a backlog document in engage-compatible format
allowed-tools: Bash(bpsai-pair:*), Bash(cat:*), Read, Write, Edit
argument-hint: <description or rough notes file>
---

# Draft Backlog

Draft a sprint backlog from the provided description or notes file.

## Input
$ARGUMENTS

## Format Requirements

Each task MUST follow this exact format:
### {ID} — {Title} | Cx: {N} | P{0-2}

Where:
- ID: letters + numbers (e.g., T1.1, S44.1, AMU1.1)
- Title: brief description
- — (em dash, not --)
- Cx: complexity in context units
- P0/P1/P2: priority

Each task MUST have:
- **Description:** paragraph explaining what to build
- **AC:** acceptance criteria as checkboxes (- [ ] item)
- **Depends on:** task dependencies (if any)
- **Model:** the model to dispatch this task with (see below)

Each task MAY also declare (engage reads these per task):
- **Target repo:** a bare sibling name (e.g. `my-other-repo`) or path for a
  cross-repo task. Sets where engage dispatches this task. A whole-run override
  is available as `bpsai-pair engage <file> --target-repo <path>`, which
  REFUSES if a per-task **Target repo:** names a different repo — so keep
  per-task lines consistent with any intended override.
- **Base:** the base branch this task's work should build on (per-task override
  of the backlog-level `**Base:**`).
- **Summary:** (backlog level, one line, directly after the backlog-level
  `**Base:**`) — the lane's delivery summary in plain words, e.g.
  `**Summary:** Windows gh-filing wrapper repair + audited bypass`. Finalize
  retitles the kickoff PR to `{lane_slug}: {summary}`; without it the PR keeps
  a generated title. Always emit it.
- **Server contract:** / **Interface:** — REQUIRED for any task that
  consumes an interface another task in this lane creates (an endpoint, a CLI
  command of the dependency's repo, a schema or contract term), especially
  across repos.
  The block states, in the consumer's own task text, the
  request/response schema, the auth model, the status codes, and the
  idempotency semantics the consumer relies on — so its driver never has to guess or
  invent the producer's contract (a cross-repo `**Depends on:**` carries no
  view of the dependency's output). A pure ordering-only dependency (a docs,
  config, or data task that merely runs after another) needs no block; kickoff
  flags an interface-consuming `**Depends on:**` edge that carries none.

### Engage-compatible line rules (do NOT break these)

- **One physical line per AC.** Write each `- [ ]` criterion on a SINGLE line.
  Do not wrap a criterion across indented continuation lines — the materializer
  now folds indented continuations into the item, but a single line is
  unambiguous and avoids accidental joins with a following note.
- **Bare `**Depends on:**` values.** List dependencies as bare task IDs,
  comma-separated (e.g. `**Depends on:** T1.1, T1.2`), or `None`. No prose, no
  links, no wrapped continuation.
- **One `**Target repo:**` / `**Base:**` value per line**, no continuation.

### Lane-shape rules (do NOT break these)

- **Task IDs are unique across this repo's entire `.paircoder/tasks/`, not just within this lane.** Before picking an ID, check that no task file with that id already exists anywhere in the repo — a fresh lane reusing an old id is a collision, not a namespacing convenience. `engage --dry-run` refuses when a backlog id collides with an existing DONE task file from a different plan; pick a fresh id rather than trying to work around the refusal.
- **Self-target lanes carry NO per-task `**Target repo:**` lines.** When every task in this backlog modifies the repo `engage` is run from, omit `**Target repo:**` entirely — do not add one "for clarity," and do not add a backlog-level `**Target repo:**` either.
- **Cross-repo lanes declare `**Target repo:**` per task only, never at the backlog level.** Every task that targets a sibling repo gets its own `**Target repo:**` line; there is no backlog-level default that applies to the whole lane.

## Model Assignment (MR3.2)

For each task, resolve `**Model:**` before writing it:

```bash
bpsai-pair calibration recommend-model --task-type <type> --complexity <Cx> [--cross-module]
```

This prefers a confident calibration `recommended_model` (enough samples,
`bpsai_pair.orchestration.model_doctrine`) over the ratified doctrine
fallback (single source of truth — do not hardcode a separate mapping
here): `feature`/`bugfix`/`refactor` → sonnet, `chore`/mechanical work →
haiku, complex or cross-module tasks → opus.

## Workflow

1. Analyze the input (description or file)
2. Break into phases (### Phase N: title)
3. Create tasks with proper format, including `**Model:**` per task
4. Include a Delivery Summary table (with a Model column)
5. Include a Priority Order list
6. Validate: run `bpsai-pair engage <output-file> --dry-run`
7. If dry-run fails, fix formatting and retry
8. Deliver to plans/backlogs/
9. Emit a plan record so `plan list`/`status` can see this lane:
   `bpsai-pair plan ensure-from-backlog plans/backlogs/<filename>.md`

## Validation

After writing the backlog, ALWAYS run:
```bash
bpsai-pair engage plans/backlogs/<filename>.md --dry-run
```

If it fails, fix the format and retry until it passes.

## Plan Record

`draft-backlog` must emit a plan record alongside the backlog markdown,
just like `pc-plan`/`plan new` does — a user can't be relied on to
remember to draft one separately. After the dry-run validates, run:

```bash
bpsai-pair plan ensure-from-backlog plans/backlogs/<filename>.md
```

This is idempotent: if a plan record already exists for the derived id it
is left untouched.
