---
description: Capture an in-session insight as durable knowledge for Computer Prime
allowed-tools: Bash(bpsai-pair:*)
argument-hint: "<insight text>" [--no-push]
---

Capture a critical insight that surfaced during the current session and route it through the synthesis prompt so it lands in Prime's permanent knowledge base.

## Pre-Flight

The CLI handles synthesis and review. Just invoke it with the raw insight in quotes.

## Execute Workflow

```bash
bpsai-pair prime-learn $ARGUMENTS
```

> Note: `$ARGUMENTS` is unquoted so flags like `--no-push`, `--yes`,
> `--reject`, and `--category decision` are parsed as options, not as
> part of the insight text. Quote the insight itself when invoking the
> slash command, e.g. `/prime-learn "tests should hit a real DB" --no-push`.

## Behavior

1. CLI synthesizes the raw insight into a durable knowledge statement (LLM call).
2. Operator approves, edits, or rejects the synthesis.
3. On approval, appends to `.paircoder/context/prime-knowledge-{date}.yaml`.
4. By default, also pushes to the A2A `#knowledge` channel so Computer Prime reads it on next cycle.
5. Pass `--no-push` to skip the A2A push and save locally only.
6. Pass `--yes` (auto-approve) or `--reject` (skip save) for scripted / non-TTY use.

## Key Constraints

- Quote multi-word insights so the shell passes a single argument: `/prime-learn "..." --no-push`.
- Failed A2A push does NOT block the local YAML save -- graceful degradation.
- Works in any project that has a `.paircoder/` directory; not framework-specific.
- Synthesis prompt mirrors `scripts/session_digest.py` so post-hoc digests and in-the-moment captures share a YAML schema.

## Examples

```bash
bpsai-pair prime-learn "mocking classes hides interface mismatches"
bpsai-pair prime-learn "tests should hit a real DB, not mocks" --no-push
bpsai-pair prime-learn "decision: ratify reasoning trace v1 schema" --category decision
bpsai-pair prime-learn "captured from CI hook" --yes --no-push
```
